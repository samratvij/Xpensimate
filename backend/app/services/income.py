"""Income: paycheques and other money earned.

Income is stored as transactions with txn_type INCOME, a positive amount and excluded = true, so every
spending total (which counts only non-excluded rows) is unaffected. Its category is an income kind (Salary,
Other income) and its purpose is who earned it.

An income source is a regular paycheque (who, which account, payer, usual amount, cadence). A deposit is pay
when it matches a source (same account, payer pattern, amount within tolerance) or says so (payroll, direct
deposit); it is then linked to the source, which is what tells "paid" from "still expected" for each pay date.
Deposits that are neither (e-transfers from family, transfers between own accounts) are not income.
"""
import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.taxonomy import DEFAULT_PURPOSE
from app.db.models import Account, IncomeSource, Transaction
from app.services.merchant import matches_pattern, merchant_key, normalize_merchant

INCOME = "INCOME"
SALARY, OTHER_INCOME = "Salary", "Other income"
INCOME_CATEGORIES = (SALARY, OTHER_INCOME)
# Who income can belong to.

PAY_WORDS = re.compile(r"\b(payroll|paie|direct deposit|salary|pay ?cheque|wages)\b", re.IGNORECASE)

# Days between pay dates; a deposit pays the unpaid pay date nearest it within this share of a period.
PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 30}
CADENCES = tuple(PERIOD_DAYS)
MAX_PAYMENT_OFFSET = 0.6
# A pay date this many days in the past without a deposit is late rather than upcoming.
LATE_GRACE_DAYS = 3


def looks_like_pay(text: str) -> bool:
    return bool(PAY_WORDS.search(text))


def _amount_fits(source: IncomeSource, amount: Decimal) -> bool:
    return abs(amount - source.amount) <= source.amount * source.amount_tolerance


def source_for(db: Session, account_id: str | None, payer: str, amount: Decimal) -> IncomeSource | None:
    """The income source a deposit is a paycheque of: same account (when the source names one), the payer
    pattern in the payer's name or, for a deposit whose payer the alert doesn't give, just the amount."""
    if amount <= 0:
        return None
    fits = []
    for source in db.scalars(select(IncomeSource).where(IncomeSource.active.is_(True))):
        if source.account_id and account_id and source.account_id != account_id:
            continue
        if not _amount_fits(source, amount):
            continue
        named = matches_pattern(source.payer_pattern, merchant_key(payer), payer)
        unnamed = account_id is not None and source.account_id == account_id and payer.strip().lower() in ("", "deposit")
        if named or unnamed:
            fits.append(source)
    return min(fits, key=lambda s: abs(amount - s.amount), default=None)


def is_pay(db: Session, account_id: str | None, payer: str, amount: Decimal) -> bool:
    """Whether a deposit should be recorded as income (rather than ignored as a transfer or the like)."""
    return amount > 0 and (looks_like_pay(payer) or source_for(db, account_id, payer, amount) is not None)


def classify(db: Session, txn: Transaction) -> None:
    """Who earned a new income transaction and what kind it is: its source's, else the account owner's."""
    source = source_for(db, txn.account_id, txn.merchant_raw, txn.amount)
    txn.income_source_id = source.id if source else None
    txn.category = SALARY if source or looks_like_pay(txn.merchant_raw) else OTHER_INCOME
    if source:
        txn.purpose = source.earner
    else:
        account = db.get(Account, txn.account_id) if txn.account_id else None
        txn.purpose = account.owner if account else DEFAULT_PURPOSE
    txn.merchant_normalized = normalize_merchant(txn.merchant_raw)
    txn.inference_confidence = Decimal("1.00")
    txn.categorized_by = "income"


# --- totals ---------------------------------------------------------------------------------------

def income_by(db: Session, start: datetime, end: datetime, earner: str | None = None) -> dict[str, Decimal]:
    """Income received in [start, end), by who earned it."""
    query = (
        select(Transaction.purpose, func.sum(Transaction.amount))
        .where(Transaction.txn_type == INCOME, Transaction.timestamp >= start, Transaction.timestamp < end)
        .group_by(Transaction.purpose)
    )
    if earner:
        query = query.where(Transaction.purpose == earner)
    return {who: total for who, total in db.execute(query)}


# --- pay dates ------------------------------------------------------------------------------------

def _month_days(start: date, end: date):
    year, month = start.year, start.month
    while date(year, month, 1) < end:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def pay_dates(source: IncomeSource, start: date, end: date) -> list[date]:
    """Pay dates in [start, end)."""
    if source.cadence in ("semimonthly", "monthly"):
        dates = []
        for year, month in _month_days(start, end):
            last = calendar.monthrange(year, month)[1]
            days = (15, last) if source.cadence == "semimonthly" else (min(source.anchor_date.day, last),)
            dates += [d for d in (date(year, month, day) for day in days) if start <= d < end]
        return dates
    step = PERIOD_DAYS[source.cadence]
    offset = (start - source.anchor_date).days % step
    day = start + timedelta(days=(step - offset) % step)
    dates = []
    while day < end:
        dates.append(day)
        day += timedelta(days=step)
    return dates


def paid_pay_dates(source: IncomeSource, deposits: list[date], start: date, end: date) -> set[date]:
    """The pay dates in [start, end) that `deposits` (local dates of the source's linked income) paid. Each
    deposit, in date order, pays the unpaid pay date nearest to it within MAX_PAYMENT_OFFSET of a period (pay
    often lands a day or two early, sometimes across a month edge)."""
    period = PERIOD_DAYS[source.cadence]
    reach = timedelta(days=period * MAX_PAYMENT_OFFSET)
    margin = timedelta(days=period * 2)
    open_dates = pay_dates(source, start - margin, end + margin)
    paid: set[date] = set()
    for day in sorted(deposits):
        near = [d for d in open_dates if abs(day - d) <= reach]
        if not near:
            continue
        due = min(near, key=lambda d: (abs((day - d).days), d))
        open_dates.remove(due)
        paid.add(due)
    return {d for d in paid if start <= d < end}


@dataclass
class PayDate:
    source: IncomeSource
    day: date
    status: str  # received | upcoming | late


def schedule(db: Session, start: date, end: date, today: date) -> list[PayDate]:
    """Every pay date of every active income source in [start, end), with whether it has arrived."""
    tz = ZoneInfo(get_settings().timezone)
    reach = timedelta(days=max(PERIOD_DAYS.values()))
    window = (datetime.combine(start - reach, datetime.min.time(), tzinfo=tz),
              datetime.combine(end + reach, datetime.min.time(), tzinfo=tz))
    deposits: dict = defaultdict(list)
    for source_id, timestamp in db.execute(
        select(Transaction.income_source_id, Transaction.timestamp).where(
            Transaction.income_source_id.is_not(None), Transaction.txn_type == INCOME,
            Transaction.timestamp >= window[0], Transaction.timestamp < window[1],
        )
    ):
        deposits[source_id].append(timestamp.astimezone(tz).date())
    result = []
    for source in db.scalars(select(IncomeSource).where(IncomeSource.active.is_(True))):
        paid = paid_pay_dates(source, deposits.get(source.id, []), start, end)
        for day in pay_dates(source, start, end):
            if day in paid:
                status = "received"
            elif day < today - timedelta(days=LATE_GRACE_DAYS):
                status = "late"
            else:
                status = "upcoming"
            result.append(PayDate(source, day, status))
    return sorted(result, key=lambda p: (p.day, p.source.name))


def expected_income(db: Session, start: date, end: date, today: date, earner: str | None = None) -> dict[str, Decimal]:
    """Paycheques not yet seen for [start, end), by earner (late ones included: the deposit may just not have
    been recorded yet). Nothing is expected of a past month."""
    if end <= today:
        return {}
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for pay in schedule(db, start, end, today):
        if pay.status != "received" and earner in (None, pay.source.earner):
            totals[pay.source.earner] += pay.source.amount
    return dict(totals)


def totals(db: Session, start: datetime, end: datetime, today: date, earner: str | None,
           spent: Decimal, still_to_spend: Decimal) -> dict:
    """A month's income in the summary's shape (schemas.IncomeTotals)."""
    zero = Decimal("0.00")
    received = income_by(db, start, end, earner)
    expected = expected_income(db, start.date(), end.date(), today, earner)
    got, coming = sum(received.values(), zero), sum(expected.values(), zero)
    return {
        "received": got, "by_person": received, "expected": coming, "expected_by_person": expected,
        "left_after_spending": got - spent, "projected_left": got + coming - spent - still_to_spend,
    }


# How far back a new or edited source looks for deposits that were its paycheques.
BACKFILL_DAYS = 45


def link_existing(db: Session, source: IncomeSource) -> int:
    """Claim recent unlinked income for a source that was just added or edited."""
    since = datetime.now(ZoneInfo(get_settings().timezone)) - timedelta(days=BACKFILL_DAYS)
    linked = 0
    for txn in db.scalars(select(Transaction).where(
        Transaction.txn_type == INCOME, Transaction.income_source_id.is_(None), Transaction.timestamp >= since,
    )):
        if source_for(db, txn.account_id, txn.merchant_raw, txn.amount) is source:
            txn.income_source_id = source.id
            if not txn.purpose_is_manual:
                txn.purpose = source.earner
            if not txn.category_is_manual:
                txn.category = SALARY
            linked += 1
    return linked
