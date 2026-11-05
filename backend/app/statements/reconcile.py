"""Plan and apply a bank statement import.

plan() compares each statement row with what the app already has for that account:
  match       the app already has this charge (from an alert or the phone): it gets confirmed
  add         missing from the app: created and categorized like any new transaction
  payment     a card payment: recorded as "not an expense", and the bank-side alert that paid it
              (e.g. a PC Money "purchase" with no merchant) is excluded too
  unclear     several app transactions fit equally well: the user picks one, adds it, or skips
  imported    already added by an earlier import of the same statement
  outside     not in the month being imported
It also lists app transactions for the account in that period that are not on the statement.

apply_import() then does the work in the background, row by row, recording progress.
"""
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.daemons.parsers.common import UNKNOWN_MERCHANT
from app.db.base import SessionLocal
from app.db.models import StatementImport, Transaction
from app.services.ingest import NewTransaction, find_by_source_ref, ingest_transaction
from app.services.reconciler import merchant_score
from app.statements.parsers import PAYMENT, ParsedStatement, StatementRow, source_refs

log = logging.getLogger(__name__)

STATEMENT = "statement"
# Bank alerts are timestamped when the email arrives; statements use the transaction date.
DATE_WINDOW_DAYS = 3
AMOUNT_TOLERANCE = Decimal("0.01")
TIE_MERCHANT_MARGIN = 0.2
# A bank-side debit that pays a card shows up as an alert with no merchant, or a transfer.
PAYMENT_WORDS = ("PAYMENT", "TRANSFER", "PYMT")
# Times for statement rows without one: midday, so the local date never shifts.
DEFAULT_TIME = time(12, 0)


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _local_day(txn: Transaction) -> date:
    return txn.timestamp.astimezone(_tz()).date()


def _brief(txn: Transaction) -> dict:
    return {"id": str(txn.id), "merchant": txn.merchant_normalized or txn.merchant_raw, "amount": str(txn.amount),
            "date": _local_day(txn).isoformat(), "source": txn.source}


def _window(row_day: date) -> tuple[datetime, datetime]:
    tz = _tz()
    start = datetime.combine(row_day - timedelta(days=DATE_WINDOW_DAYS + 1), time.min, tzinfo=tz)
    end = datetime.combine(row_day + timedelta(days=DATE_WINDOW_DAYS + 1), time.min, tzinfo=tz)
    return start, end


def _candidates(db: Session, account_id: str, row: StatementRow, claimed: set) -> list[tuple[Transaction, int, float]]:
    """App transactions this row could be: same account (or unknown), same amount, close date."""
    query = select(Transaction).where(
        Transaction.excluded.is_(False),
        Transaction.source != STATEMENT,
        (Transaction.account_id == account_id) | Transaction.account_id.is_(None),
        Transaction.amount.between(row.amount - AMOUNT_TOLERANCE, row.amount + AMOUNT_TOLERANCE),
        Transaction.timestamp.between(*_window(row.spent_on)),
    )
    found = []
    for txn in db.scalars(query):
        days = abs((_local_day(txn) - row.spent_on).days)
        if txn.id in claimed or days > DATE_WINDOW_DAYS:
            continue
        found.append((txn, days, merchant_score(row.merchant, txn.merchant_raw)))
    return sorted(found, key=lambda c: (c[1], -c[2]))


def _payment_pair(db: Session, account_id: str, row: StatementRow, claimed: set) -> Transaction | None:
    """The debit on another account that paid this card (same amount, close date, no real merchant)."""
    paid = -row.amount
    query = select(Transaction).where(
        Transaction.excluded.is_(False),
        Transaction.account_id != account_id,
        Transaction.amount.between(paid - AMOUNT_TOLERANCE, paid + AMOUNT_TOLERANCE),
        Transaction.timestamp.between(*_window(row.spent_on)),
    )
    for txn in db.scalars(query):
        name = txn.merchant_raw.upper()
        if txn.id not in claimed and (txn.merchant_raw == UNKNOWN_MERCHANT or any(w in name for w in PAYMENT_WORDS)):
            return txn
    return None


def plan(db: Session, parsed: ParsedStatement, account_id: str, month: str | None) -> tuple[list[dict], list[dict], date | None, date | None]:
    refs = source_refs(account_id, parsed.rows)
    in_scope = [r for r in parsed.rows if month is None or r.spent_on.strftime("%Y-%m") == month]
    period_start = min((r.spent_on for r in in_scope), default=None)
    period_end = max((r.spent_on for r in in_scope), default=None)

    claimed: set = set()
    planned = []
    for row, ref in zip(parsed.rows, refs):
        entry = {
            "index": row.index, "date": row.spent_on.isoformat(), "time": row.at.strftime("%H:%M") if row.at else None,
            "merchant": row.merchant, "amount": str(row.amount), "kind": row.kind,
            "bank_category": row.bank_category, "source_ref": ref,
        }
        if month is not None and row.spent_on.strftime("%Y-%m") != month:
            planned.append({**entry, "action": "outside"})
            continue
        if existing := find_by_source_ref(db, STATEMENT, ref):
            claimed.add(existing.id)
            planned.append({**entry, "action": "imported", "transaction": _brief(existing)})
            continue
        if row.kind == PAYMENT:
            pair = _payment_pair(db, account_id, row, claimed)
            if pair is not None:
                claimed.add(pair.id)
            planned.append({**entry, "action": "payment", "pays": _brief(pair) if pair else None})
            continue

        candidates = _candidates(db, account_id, row, claimed)
        if not candidates:
            planned.append({**entry, "action": "add"})
            continue
        best, best_days, best_score = candidates[0]
        tied = [c for c in candidates[1:] if c[1] == best_days and best_score - c[2] < TIE_MERCHANT_MARGIN]
        if tied:
            planned.append({**entry, "action": "unclear",
                            "candidates": [_brief(t) for t, _, _ in [candidates[0], *tied]]})
            continue
        claimed.add(best.id)
        planned.append({**entry, "action": "match", "transaction": _brief(best)})

    missing = []
    if period_start and period_end:
        tz = _tz()
        query = select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.excluded.is_(False),
            Transaction.source != STATEMENT,
            Transaction.timestamp >= datetime.combine(period_start, time.min, tzinfo=tz),
            Transaction.timestamp < datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=tz),
        )
        missing = [_brief(t) for t in db.scalars(query.order_by(Transaction.timestamp)) if t.id not in claimed]
    return planned, missing, period_start, period_end


def counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["action"]] = out.get(r["action"], 0) + 1
    return out


def _timestamp(row: dict) -> datetime:
    at = time.fromisoformat(row["time"]) if row.get("time") else DEFAULT_TIME
    return datetime.combine(date.fromisoformat(row["date"]), at, tzinfo=_tz())


def _add(db: Session, imp: StatementImport, row: dict, *, excluded: bool = False) -> Transaction:
    txn, _ = ingest_transaction(
        db,
        NewTransaction(
            source=STATEMENT,
            source_ref=row["source_ref"],
            account_id=imp.account_id,
            timestamp=_timestamp(row),
            amount=Decimal(row["amount"]),
            merchant=row["merchant"],
            reconciliation_status="PENDING_MATCH" if not excluded else "SETTLED",
            bank_category=row.get("bank_category"),
            excluded=excluded,
        ),
        raw_payload={"statement_import_id": str(imp.id), **row},
    )
    txn.statement_import_id = imp.id
    return txn


def apply_row(db: Session, imp: StatementImport, row: dict, decision: str | None) -> str:
    """Carry out one row's action (or the user's decision for it). Returns what was done."""
    action = decision or row["action"]
    if action.startswith("match:") or action == "match":
        txn_id = action.split(":", 1)[1] if ":" in action else row["transaction"]["id"]
        allowed = {c["id"] for c in row.get("candidates", [])} | ({row["transaction"]["id"]} if row.get("transaction") else set())
        if txn_id not in allowed:
            raise ValueError(f"Row {row['index']}: {txn_id} is not one of its candidates")
        txn = db.get(Transaction, txn_id)
        txn.statement_import_id = imp.id
        return "matched"
    if action == "add":
        _add(db, imp, row)
        return "added"
    if action == "payment":
        _add(db, imp, row, excluded=True)
        if row.get("pays") and (paid := db.get(Transaction, row["pays"]["id"])):
            paid.excluded = True
        return "payment"
    return "skipped"


def apply_import(import_id, decisions: dict[str, str]) -> None:
    """Background task: apply every row, committing as it goes so progress is visible."""
    with SessionLocal() as db:
        imp = db.get(StatementImport, import_id)
        done: dict[str, int] = {}
        try:
            for i, row in enumerate(imp.rows):
                outcome = apply_row(db, imp, row, decisions.get(str(row["index"])))
                done[outcome] = done.get(outcome, 0) + 1
                imp.processed = i + 1
                db.commit()
            imp.status = "done"
            imp.result = done
        except Exception as e:  # noqa: BLE001 - recorded for the user, then re-raised to the log
            db.rollback()
            imp = db.get(StatementImport, import_id)
            imp.status = "failed"
            imp.result = {**done, "error": str(e)}
            log.exception("Statement import %s failed", import_id)
        db.commit()
