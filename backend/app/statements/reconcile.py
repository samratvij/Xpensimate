"""Plan and apply a bank statement import.

plan() compares each statement row with what the app already has for that account:
  match       the app already has this charge (from an alert or the phone): it gets confirmed
  add         missing from the app: created and categorized like any new transaction
  payment     a card payment: recorded as "not an expense", and the bank-side alert that paid it
              (e.g. a PC Money "purchase" with no merchant) is excluded too
  unclear     several app transactions fit equally well: the user picks one, adds it, or skips
  income      a deposit that is pay (payroll words, or one of the household's income sources): added as income
  deposit     other money in (an e-transfer received, a transfer from another account): the user says what it
              is: "income", "add" (money back, counted against spending), "move:<account>" (from another of the
              household's accounts), "transfer" (own money, other side unknown) or "skip"
  transfer    money sent out of a bank account (an e-transfer, a move to savings): "move:<account>" (to another
              of the household's accounts: saving or investing when that account is one), "transfer" (own
              money, other side unknown, e.g. to Alex or Sam), "add" (spending: someone was paid for
              something) or "skip"
Deposits and transfers carry "suggested" when the app can tell (app/services/savings.py); it is what happens
unless the user picks otherwise. A move the app already has (seen from the other account, or a Wealthsimple
email) is a match.
  imported    already added by an earlier import of the same statement
  outside     not in the month being imported
It also lists app transactions for the account in that period that are not on the statement. One with a
charge from the same merchant on the statement a few days later was a hold the bank replaced with the final
amount (an order that changed): it carries "replaced_by", and importing excludes it unless the user keeps it
(see app/services/adjustments.py).

apply_import() then does the work in the background, row by row, recording progress.
"""
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.daemons.parsers.common import UNKNOWN_MERCHANT
from app.db.base import SessionLocal
from app.db.models import Account, StatementImport, Transaction
from app.services import income, savings
from app.services.adjustments import CENT, STATEMENT_MERCHANT_SCORE, STATEMENT_WINDOW_DAYS, same_merchant
from app.services.ingest import NewTransaction, find_by_source_ref, ingest_transaction
from app.services.reconciler import merchant_score
from app.statements.parsers import DEPOSIT, PAYMENT, PURCHASE, TRANSFER_OUT, ParsedStatement, StatementRow, source_refs

log = logging.getLogger(__name__)

STATEMENT = "statement"
SCREENSHOT = "screenshot"  # rows read from a screenshot of the bank's app (app/statements/screenshot.py)
# Bank alerts are timestamped when the email arrives; statements use the transaction date.
DATE_WINDOW_DAYS = 3
AMOUNT_TOLERANCE = Decimal("0.01")
TIE_MERCHANT_MARGIN = 0.2
# A bank-side debit that pays a card shows up as an alert with no merchant, or a transfer.
PAYMENT_WORDS = ("PAYMENT", "TRANSFER", "PYMT")
# Statement rows that can be the final charge of a hold: what's in the app once the import is done.
REPLACING_ACTIONS = ("match", "add", "imported")
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


def source_for(bank_format: str) -> str:
    return SCREENSHOT if bank_format == SCREENSHOT else STATEMENT


def _candidates(db: Session, account_id: str, row: StatementRow, claimed: set,
                source: str) -> list[tuple[Transaction, int, float]]:
    """App transactions this row could be: same account (or unknown), same amount, close date.

    A statement doesn't match rows of earlier statements (those are found by their ref). A screenshot is
    partial and often overlaps an earlier one or a statement, so anything already recorded can match it.
    """
    query = select(Transaction).where(
        Transaction.excluded.is_(False),
        Transaction.source != STATEMENT if source == STATEMENT else true(),
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


def _income_twin(db: Session, account_id: str, row: StatementRow, claimed: set) -> Transaction | None:
    """Pay the app already has (from an email or the phone) for this deposit: same account and amount, close in date."""
    start, end = _window(row.spent_on)
    return db.scalar(select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.txn_type == income.INCOME,
        Transaction.amount == -row.amount,
        Transaction.timestamp >= start, Transaction.timestamp < end,
        Transaction.id.not_in(claimed) if claimed else true(),
    ).order_by(Transaction.timestamp))


def _replacements(missing: list[Transaction], planned: list[dict]) -> dict:
    """Missing transaction id -> the statement row that replaced it: same merchant, a different amount, dated
    up to a week later (the closest one). Each row replaces at most one charge."""
    rows = [p for p in planned if p["action"] in REPLACING_ACTIONS and Decimal(p["amount"]) > 0]
    earliest, latest = STATEMENT_WINDOW_DAYS
    used: set = set()
    found = {}
    for txn in sorted(missing, key=lambda t: t.timestamp):
        if txn.amount <= 0:
            continue
        day = _local_day(txn)
        options = []
        for row in rows:
            gap = (date.fromisoformat(row["date"]) - day).days
            if (row["index"] in used or not earliest <= gap <= latest
                    or abs(Decimal(row["amount"]) - txn.amount) <= CENT
                    or not same_merchant(row["merchant"], txn.merchant_raw, STATEMENT_MERCHANT_SCORE, brands=True)):
                continue
            options.append((abs(gap), row))
        if options:
            row = min(options, key=lambda o: o[0])[1]
            used.add(row["index"])
            found[txn.id] = {k: row[k] for k in ("index", "merchant", "amount", "date")}
    return found


def plan(db: Session, parsed: ParsedStatement, account_id: str, month: str | None) -> tuple[list[dict], list[dict], date | None, date | None]:
    source = source_for(parsed.bank_format)
    refs = source_refs(account_id, parsed.rows)
    in_scope = [r for r in parsed.rows if month is None or r.spent_on.strftime("%Y-%m") == month]
    period_start = min((r.spent_on for r in in_scope), default=None)
    period_end = max((r.spent_on for r in in_scope), default=None)

    claimed: set = set()
    planned = []
    account = db.get(Account, account_id)
    bank_account = account is not None and account.kind != "credit_card"
    for row, ref in zip(parsed.rows, refs):
        entry = {
            "index": row.index, "date": row.spent_on.isoformat(), "time": row.at.strftime("%H:%M") if row.at else None,
            "merchant": row.merchant, "amount": str(row.amount), "kind": row.kind,
            "bank_category": row.bank_category, "source_ref": ref, "pending": row.pending,
        }
        if month is not None and row.spent_on.strftime("%Y-%m") != month:
            planned.append({**entry, "action": "outside"})
            continue
        if existing := find_by_source_ref(db, source, ref):
            claimed.add(existing.id)
            planned.append({**entry, "action": "imported", "transaction": _brief(existing)})
            continue
        moves_money = row.kind == TRANSFER_OUT or (
            bank_account and row.kind == PURCHASE and savings.TRANSFER_WORDS.search(row.merchant))
        if row.kind == DEPOSIT or moves_money:
            when = datetime.combine(row.spent_on, DEFAULT_TIME, tzinfo=_tz())
            known = (_income_twin(db, account_id, row, claimed) if row.kind == DEPOSIT else None) or \
                savings.find_twin(db, account_id, row.amount, when, claimed)
            if known:
                claimed.add(known.id)
                planned.append({**entry, "action": "match", "transaction": _brief(known)})
            elif row.kind == DEPOSIT and income.is_pay(db, account_id, row.merchant, -row.amount):
                planned.append({**entry, "action": "income"})
            else:
                planned.append({**entry, "action": "deposit" if row.kind == DEPOSIT else "transfer",
                                "suggested": savings.suggest(db, account_id, row.merchant)})
            continue
        if row.kind == PAYMENT:
            pair = _payment_pair(db, account_id, row, claimed)
            if pair is not None:
                claimed.add(pair.id)
            planned.append({**entry, "action": "payment", "pays": _brief(pair) if pair else None})
            continue

        candidates = _candidates(db, account_id, row, claimed, source)
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
    # A screenshot is often filtered or cut off, so what it doesn't show says nothing.
    if period_start and period_end and source == STATEMENT:
        tz = _tz()
        query = select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.excluded.is_(False),
            Transaction.source != STATEMENT,
            Transaction.timestamp >= datetime.combine(period_start, time.min, tzinfo=tz),
            Transaction.timestamp < datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=tz),
        )
        stray = [t for t in db.scalars(query.order_by(Transaction.timestamp)) if t.id not in claimed]
        replaced = _replacements(stray, planned)
        missing = [{**_brief(t), **({"replaced_by": replaced[t.id]} if t.id in replaced else {})} for t in stray]
    return planned, missing, period_start, period_end


def counts(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["action"]] = out.get(r["action"], 0) + 1
    return out


def _timestamp(row: dict) -> datetime:
    at = time.fromisoformat(row["time"]) if row.get("time") else DEFAULT_TIME
    return datetime.combine(date.fromisoformat(row["date"]), at, tzinfo=_tz())


def _add(db: Session, imp: StatementImport, row: dict, *, excluded: bool = False, is_income: bool = False,
         move: tuple[str | None, str | None] | None = None) -> Transaction:
    """Create a statement row's transaction; `move` = (from, to) records it as a transfer between own accounts."""
    txn, _ = ingest_transaction(
        db,
        NewTransaction(
            source=source_for(imp.bank_format),
            source_ref=row["source_ref"],
            account_id=move[0] if move else imp.account_id,
            timestamp=_timestamp(row),
            amount=Decimal(row["amount"]),
            merchant=row["merchant"],
            reconciliation_status="PENDING_MATCH" if not excluded else "SETTLED",
            bank_category=row.get("bank_category"),
            excluded=excluded,
            income=is_income,
            transfer=move is not None,
            transfer_to=move[1] if move else None,
        ),
        raw_payload={"statement_import_id": str(imp.id), **row},
    )
    txn.statement_import_id = imp.id
    return txn


def apply_row(db: Session, imp: StatementImport, row: dict, decision: str | None) -> str:
    """Carry out one row's action (or the user's decision for it). Returns what was done."""
    action = decision or row.get("suggested") or row["action"]
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
    if action == "income":
        _add(db, imp, row, is_income=True)
        return "income"
    if action == "transfer" or action.startswith("move:"):
        other = action.split(":", 1)[1] if ":" in action else None
        out = Decimal(row["amount"]) > 0  # money leaving this account
        _add(db, imp, row, move=(imp.account_id, other) if out else (other, imp.account_id))
        return "transfer"
    if action == "payment":
        _add(db, imp, row, excluded=True)
        if row.get("pays") and (paid := db.get(Transaction, row["pays"]["id"])):
            paid.excluded = True
        return "payment"
    return "skipped"


def exclude_replaced(db: Session, imp: StatementImport, decisions: dict[str, str], keep: set[str]) -> int:
    """Exclude the holds the statement replaced, except those the user kept or whose final charge they skipped."""
    count = 0
    for entry in imp.missing:
        final = entry.get("replaced_by")
        if not final or entry["id"] in keep or decisions.get(str(final["index"])) == "skip":
            continue
        txn = db.get(Transaction, entry["id"])
        if txn is not None and not txn.excluded:
            txn.excluded = True
            count += 1
    return count


def apply_import(import_id, decisions: dict[str, str], keep: set[str] = frozenset()) -> None:
    """Background task: apply every row, committing as it goes so progress is visible, then drop replaced holds."""
    with SessionLocal() as db:
        imp = db.get(StatementImport, import_id)
        done: dict[str, int] = {}
        try:
            for i, row in enumerate(imp.rows):
                outcome = apply_row(db, imp, row, decisions.get(str(row["index"])))
                done[outcome] = done.get(outcome, 0) + 1
                imp.processed = i + 1
                db.commit()
            if replaced := exclude_replaced(db, imp, decisions, keep):
                done["replaced"] = replaced
            imp.status = "done"
            imp.result = done
        except Exception as e:  # noqa: BLE001 - recorded for the user, then re-raised to the log
            db.rollback()
            imp = db.get(StatementImport, import_id)
            imp.status = "failed"
            imp.result = {**done, "error": str(e)}
            log.exception("Statement import %s failed", import_id)
        db.commit()
