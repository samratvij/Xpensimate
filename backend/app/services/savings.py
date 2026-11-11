"""Savings and investment flows: money moved between the household's own accounts.

A transfer is a TRANSFER transaction (excluded, so never spending) that moves `amount` from account_id to
counterpart_account_id. Where it goes is told by the two accounts' kinds: chequing to savings is saving, savings
or chequing to an investment or retirement account is investing, and back the other way undoes it. A move between two spending
accounts (or with an unknown side, like an e-transfer between two people in the household) is neither.

These are flows: what was moved this month. Balances and market returns are stocks, kept apart (step 3), so a
market gain is never counted as saving.

The savings rate is (income − spending) / income for the month. What was actually moved into savings and
investments is shown next to it: the two differ by whatever stays in chequing.
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.core.accounts import INVESTMENT_KINDS, SAVINGS_KINDS
from app.core.taxonomy import DEFAULT_PURPOSE, people
from app.db.models import Account, Transaction
from app.services.merchant import merchant_key, normalize_merchant

TRANSFER = "TRANSFER"
TRANSFER_CATEGORY = "Transfer"
SAVINGS, INVESTMENTS, SPENDING, OTHER = "savings", "investments", "spending", "other"

# Statement and screenshot rows on a bank account that move money rather than buy something.
TRANSFER_WORDS = re.compile(
    r"\b(e-?transfer|transfer|tfr|interac|to savings|from savings|wealthsimple|questrade|contribution|"
    r"withdrawal to|deposit to)\b",
    re.IGNORECASE,
)


def names_a_person(text: str) -> bool:
    """Whether a transfer names someone in the household (by first name): money between them, not to an outsider."""
    names = {name.split()[0] for name in people()}
    return any(re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in names)
# The same move seen from both accounts (a statement each, or an email and a statement).
TWIN_DAYS = 3


def bucket(kind: str | None) -> str:
    if kind in SAVINGS_KINDS:
        return SAVINGS
    if kind in INVESTMENT_KINDS:
        return INVESTMENTS
    if kind in ("chequing", "credit_card"):
        return SPENDING
    return OTHER


def flow(from_kind: str | None, to_kind: str | None) -> str | None:
    """to_savings | to_investments | from_savings | from_investments, or None for a move that is neither."""
    src, dst = bucket(from_kind), bucket(to_kind)
    if from_kind is None or to_kind is None or src == dst:
        return None
    if dst in (SAVINGS, INVESTMENTS):
        return f"to_{dst}"
    if src in (SAVINGS, INVESTMENTS):
        return f"from_{src}"
    return None


def purpose_for(db: Session, from_id: str | None, to_id: str | None) -> str:
    """Whose move it is: the owner of the savings or investment side, else of the account it left."""
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_([i for i in (from_id, to_id) if i])))}
    src, dst = accounts.get(from_id), accounts.get(to_id)
    for account in (dst, src):
        if account and bucket(account.kind) in (SAVINGS, INVESTMENTS):
            return account.owner
    return (src or dst).owner if (src or dst) else DEFAULT_PURPOSE


def classify(db: Session, txn: Transaction) -> None:
    txn.excluded = True
    txn.amount = abs(txn.amount)
    txn.category = TRANSFER_CATEGORY
    txn.purpose = purpose_for(db, txn.account_id, txn.counterpart_account_id)
    txn.merchant_normalized = normalize_merchant(txn.merchant_raw)
    txn.inference_confidence = Decimal("1.00")
    txn.categorized_by = "transfer"


def find_twin(db: Session, account_id: str, amount: Decimal, when: datetime, exclude: set = frozenset()) -> Transaction | None:
    """A transfer already recorded that involves this account, for this amount, within a few days."""
    window = timedelta(days=TWIN_DAYS)
    query = select(Transaction).where(
        Transaction.txn_type == TRANSFER,
        or_(Transaction.account_id == account_id, Transaction.counterpart_account_id == account_id),
        Transaction.amount == abs(amount),
        Transaction.timestamp.between(when - window, when + window),
    ).order_by(Transaction.timestamp)
    return next((t for t in db.scalars(query) if t.id not in exclude), None)


def suggest(db: Session, account_id: str, merchant: str) -> str | None:
    """A likely decision for a transfer-looking row: "move:<account>" when the row names another of the
    household's accounts by its last four digits or the same description moved money before, "transfer" when it
    names someone in the household (money between them), else None (ask)."""
    accounts = list(db.scalars(select(Account).where(Account.id != account_id, Account.card_last4.is_not(None))))
    named = [a.id for a in accounts if any(re.search(rf"(?<!\d){d}(?!\d)", merchant) for d in a.card_digits)]
    if len(named) == 1:
        return f"move:{named[0]}"
    key = merchant_key(merchant)
    for past in db.scalars(
        select(Transaction).where(
            Transaction.txn_type == TRANSFER,
            or_(Transaction.account_id == account_id, Transaction.counterpart_account_id == account_id),
        ).order_by(Transaction.timestamp.desc()).limit(200)
    ):
        if merchant_key(past.merchant_raw) == key:
            other = past.counterpart_account_id if past.account_id == account_id else past.account_id
            return f"move:{other}" if other else "transfer"
    if names_a_person(merchant):
        return "transfer"
    return None


def month_flows(db: Session, start: datetime, end: datetime) -> dict[str, Decimal]:
    """Money moved into and out of savings and investment accounts in [start, end)."""
    zero = Decimal("0.00")
    out = {"to_savings": zero, "from_savings": zero, "to_investments": zero, "from_investments": zero}
    src, dst = aliased(Account), aliased(Account)
    query = (
        select(Transaction.amount, src.kind, dst.kind)
        .join(src, Transaction.account_id == src.id)
        .join(dst, Transaction.counterpart_account_id == dst.id)
        .where(Transaction.txn_type == TRANSFER, Transaction.timestamp >= start, Transaction.timestamp < end)
    )
    for amount, from_kind, to_kind in db.execute(query):
        src_b, dst_b = bucket(from_kind), bucket(to_kind)
        if src_b == dst_b:
            continue
        if dst_b in (SAVINGS, INVESTMENTS):
            out[f"to_{dst_b}"] += amount
        if src_b in (SAVINGS, INVESTMENTS):
            out[f"from_{src_b}"] += amount
    return out


def totals(db: Session, start: datetime, end: datetime, income: Decimal, spent: Decimal) -> dict:
    """A month's savings in the summary's shape (schemas.SavingsTotals)."""
    flows = month_flows(db, start, end)
    kept = income - spent
    net_savings = flows["to_savings"] - flows["from_savings"]
    net_investments = flows["to_investments"] - flows["from_investments"]
    return {
        "kept": kept,
        "rate": round(float(kept / income * 100), 1) if income > 0 else None,
        **flows,
        "net_to_savings": net_savings,
        "net_to_investments": net_investments,
        "moved": net_savings + net_investments,
    }
