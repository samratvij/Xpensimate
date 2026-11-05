"""Match itemized receipts to card transactions (PRD 6.3).

A receipt and a transaction match when they are at most 72 hours apart and the card amount equals the
receipt total within $0.05, or, for a restaurant receipt, exceeds it by a plausible tip. Matching copies
the receipt lines into transaction_items, scaled so they sum exactly to the card amount (tax and tip
spread proportionally), which makes the monthly summary count each line under its own budget line.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import Receipt, Transaction, TransactionItem
from app.services.merchant import merchant_key

MATCH_WINDOW = timedelta(hours=72)
AMOUNT_TOLERANCE = Decimal("0.05")
MAX_TIP_RATIO = Decimal("0.30")
# A tip match loosens the amount, so it also needs the merchant names to agree.
MIN_TIP_MERCHANT_SCORE = 0.5
# Two equally good candidates (same amount gap) are a tie unless one merchant name is clearly closer.
TIE_MERCHANT_MARGIN = 0.2
CENT = Decimal("0.01")


@dataclass
class Candidate:
    receipt: Receipt
    transaction: Transaction
    amount_diff: Decimal  # card amount minus receipt total
    hours_apart: float
    merchant_score: float
    tip: bool

    @property
    def sort_key(self):
        return (self.tip, abs(self.amount_diff), -self.merchant_score, self.hours_apart)


def merchant_score(a: str, b: str) -> float:
    return SequenceMatcher(None, merchant_key(a).lower(), merchant_key(b).lower()).ratio()


def is_dining(receipt: Receipt) -> bool:
    totals: Counter = Counter()
    for item in receipt.items:
        totals[item.category] += abs(item.price)
    return bool(totals) and totals.most_common(1)[0][0] == "Eating Out"


def score(receipt: Receipt, txn: Transaction) -> Candidate | None:
    if txn.amount <= 0 or receipt.total <= 0 or txn.excluded:
        return None
    hours = abs((txn.timestamp - receipt.timestamp).total_seconds()) / 3600
    if hours > MATCH_WINDOW.total_seconds() / 3600:
        return None
    diff = txn.amount - receipt.total
    similarity = merchant_score(receipt.merchant, txn.merchant_normalized or txn.merchant_raw)
    if abs(diff) <= AMOUNT_TOLERANCE:
        tip = False
    elif AMOUNT_TOLERANCE < diff <= receipt.total * MAX_TIP_RATIO and is_dining(receipt):
        tip = True
    else:
        return None
    return Candidate(receipt, txn, diff, round(hours, 1), round(similarity, 2), tip)


def _unmatched_transactions():
    claimed = exists().where(Receipt.matched_transaction_id == Transaction.id)
    return select(Transaction).where(Transaction.is_itemized.is_(False), Transaction.excluded.is_(False), ~claimed)


def transaction_candidates(db: Session, receipt: Receipt) -> list[Candidate]:
    query = _unmatched_transactions().where(
        Transaction.timestamp.between(receipt.timestamp - MATCH_WINDOW, receipt.timestamp + MATCH_WINDOW)
    )
    found = (score(receipt, txn) for txn in db.scalars(query))
    return sorted((c for c in found if c), key=lambda c: c.sort_key)


def receipt_candidates(db: Session, txn: Transaction) -> list[Candidate]:
    query = select(Receipt).where(
        Receipt.matched_transaction_id.is_(None),
        Receipt.timestamp.between(txn.timestamp - MATCH_WINDOW, txn.timestamp + MATCH_WINDOW),
    )
    found = (score(receipt, txn) for receipt in db.scalars(query))
    return sorted((c for c in found if c), key=lambda c: c.sort_key)


def pick(candidates: list[Candidate]) -> Candidate | None:
    """The single clear winner, or None when there is nothing or a tie a human should settle."""
    if not candidates:
        return None
    best = candidates[0]
    if best.tip and best.merchant_score < MIN_TIP_MERCHANT_SCORE:
        return None
    for other in candidates[1:]:
        same_gap = other.tip == best.tip and abs(abs(other.amount_diff) - abs(best.amount_diff)) <= CENT
        if same_gap and best.merchant_score - other.merchant_score < TIE_MERCHANT_MARGIN:
            return None
    return best


def allocate(prices: list[Decimal], total: Decimal) -> list[Decimal]:
    """Scale line totals so they sum exactly to `total`; rounding cents go to the largest line."""
    base = sum(prices, Decimal("0"))
    if not prices or base == 0:
        return list(prices)
    scaled = [(p * total / base).quantize(CENT, rounding=ROUND_HALF_UP) for p in prices]
    largest = max(range(len(prices)), key=lambda i: abs(prices[i]))
    scaled[largest] += total - sum(scaled, Decimal("0"))
    return scaled


def apply_match(db: Session, receipt: Receipt, txn: Transaction) -> None:
    receipt.matched_transaction_id = txn.id
    txn.is_itemized = True
    txn.reconciliation_status = "RECONCILED"
    txn.items.clear()

    items = sorted(receipt.items, key=lambda i: i.position)
    for item, price in zip(items, allocate([i.price for i in items], txn.amount)):
        # A purpose the user set on the whole transaction wins over OCR guesses for its lines.
        purpose_from_txn = txn.purpose_is_manual and not item.purpose_is_manual
        txn.items.append(
            TransactionItem(
                sku_name=item.sku_name,
                category=item.category,
                category_is_manual=item.category_is_manual,
                purpose=txn.purpose if purpose_from_txn else item.purpose,
                purpose_is_manual=item.purpose_is_manual or purpose_from_txn,
                price=price,
                quantity=item.quantity,
                note=item.note,
            )
        )

    summarize_labels(txn, keep_manual=True)


def summarize_labels(txn: Transaction, *, keep_manual: bool) -> None:
    """Set the transaction-level labels to its biggest lines (shown in lists; the summary uses the items).

    keep_manual leaves a label the user picked for the whole charge alone (receipt matching); a manual split
    is itself the user's latest word, so it replaces them.
    """
    if not txn.items:
        return
    if not (keep_manual and txn.category_is_manual):
        txn.category = _largest(txn.items, "category")
    if not (keep_manual and txn.purpose_is_manual):
        txn.purpose = _largest(txn.items, "purpose")


def _largest(items: list[TransactionItem], field: str) -> str:
    totals: Counter = Counter()
    for item in items:
        totals[getattr(item, field)] += item.price
    return totals.most_common(1)[0][0]


def unmatch(db: Session, receipt: Receipt) -> None:
    txn = db.get(Transaction, receipt.matched_transaction_id) if receipt.matched_transaction_id else None
    receipt.matched_transaction_id = None
    if txn is not None:
        txn.items.clear()
        txn.is_itemized = False
        txn.reconciliation_status = "PENDING_MATCH"


def try_match_receipt(db: Session, receipt: Receipt) -> Transaction | None:
    if receipt.matched_transaction_id is not None:
        return None
    if winner := pick(transaction_candidates(db, receipt)):
        apply_match(db, receipt, winner.transaction)
        db.commit()
        return winner.transaction
    return None


def try_match_transaction(db: Session, txn: Transaction) -> Receipt | None:
    if txn.is_itemized:
        return None
    if winner := pick(receipt_candidates(db, txn)):
        apply_match(db, winner.receipt, txn)
        db.commit()
        return winner.receipt
    return None
