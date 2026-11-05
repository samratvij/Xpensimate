from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.budget_evaluator import check_transaction
from app.db.models import RawIngestLog, Transaction
from app.services.categorizer import apply_classification, categorize
from app.services.reconciler import merchant_score, try_match_transaction
from app.services.recurring import link_transaction


@dataclass
class NewTransaction:
    source: str
    source_ref: str | None
    account_id: str | None
    timestamp: datetime
    amount: Decimal
    merchant: str
    currency: str = "CAD"
    reconciliation_status: str = "SETTLED"
    bank_category: str | None = None
    # Not an expense (e.g. a card payment from a statement): stored for dedupe/audit, never categorized.
    excluded: bool = False


def find_by_source_ref(db: Session, source: str, source_ref: str) -> Transaction | None:
    return db.scalar(select(Transaction).where(Transaction.source == source, Transaction.source_ref == source_ref))


# Live feeds that can report the same purchase twice (a bank alert email and a phone notification).
LIVE_SOURCES = ("gmail_pubsub", "android_push")
TWIN_WINDOW = timedelta(hours=36)
TWIN_MERCHANT_SCORE = 0.5


def _feed(source: str, source_ref: str | None) -> str:
    """Which feed reported a transaction: the source plus its ref prefix (gmail / ws / gpay / direct push)."""
    prefix = source_ref.split(":", 1)[0] if source_ref and ":" in source_ref else "direct"
    return f"{source}:{prefix}"


def find_twin(db: Session, new: NewTransaction) -> Transaction | None:
    """The same purchase already recorded by another live feed: same amount, close in time, same or unknown
    account, and a similar merchant (or both on the same known account)."""
    if new.source not in LIVE_SOURCES:
        return None
    candidates = db.scalars(
        select(Transaction).where(
            Transaction.source.in_(LIVE_SOURCES),
            Transaction.source_ref.is_distinct_from(new.source_ref),
            Transaction.amount.between(new.amount - Decimal("0.01"), new.amount + Decimal("0.01")),
            Transaction.timestamp.between(new.timestamp - TWIN_WINDOW, new.timestamp + TWIN_WINDOW),
            Transaction.excluded.is_(False),
        )
    )
    for txn in candidates:
        if _feed(txn.source, txn.source_ref) == _feed(new.source, new.source_ref):
            continue  # the same feed reporting twice means two genuine purchases, not a twin
        if new.account_id and txn.account_id and new.account_id != txn.account_id:
            continue
        same_account = new.account_id is not None and new.account_id == txn.account_id
        if same_account or merchant_score(new.merchant, txn.merchant_raw) >= TWIN_MERCHANT_SCORE:
            return txn
    return None


def ingest_transaction(db: Session, new: NewTransaction, raw_payload: dict[str, Any]) -> tuple[Transaction, bool]:
    """Categorize and store one upstream event. Returns (transaction, created); duplicates return the existing row."""
    if new.source_ref and (existing := find_by_source_ref(db, new.source, new.source_ref)):
        return existing, False
    if twin := find_twin(db, new):
        db.add(RawIngestLog(source=new.source, payload={**raw_payload, "duplicate_of": str(twin.id)}))
        db.commit()
        return twin, False

    txn = Transaction(
        account_id=new.account_id,
        source=new.source,
        source_ref=new.source_ref,
        timestamp=new.timestamp,
        amount=new.amount,
        txn_type="REFUND" if new.amount < 0 else "PURCHASE",
        currency=new.currency.upper(),
        merchant_raw=new.merchant,
        reconciliation_status=new.reconciliation_status,
        category_is_manual=False,
        purpose_is_manual=False,
    )
    if new.excluded:
        txn.excluded = True
        txn.categorized_by = "statement"
    else:
        apply_classification(
            txn,
            categorize(
                db,
                merchant=new.merchant,
                account_id=new.account_id,
                amount=new.amount,
                timestamp=new.timestamp,
                currency=txn.currency,
                bank_category=new.bank_category,
            ),
        )
        link_transaction(db, txn)
    db.add(RawIngestLog(source=new.source, payload=raw_payload))
    db.add(txn)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with an identical event; return the row that won.
        db.rollback()
        if new.source_ref and (existing := find_by_source_ref(db, new.source, new.source_ref)):
            return existing, False
        raise
    db.refresh(txn)
    # A receipt may already be waiting for this charge.
    try_match_transaction(db, txn)
    check_transaction(db, txn)
    return txn, True
