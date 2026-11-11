import re
import statistics
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.categories import categories_with
from app.core.taxonomy import budget_line
from app.db.models import Transaction, TransactionItem

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def month_bounds(month: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Local-time [start, end) of a YYYY-MM month."""
    match = _MONTH.match(month)
    if not match:
        raise ValueError("month must be YYYY-MM")
    year, mon = int(match[1]), int(match[2])
    start = datetime(year, mon, 1, tzinfo=tz)
    end = datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=tz)
    return start, end


GroupBy = Literal["budget_line", "category"]


def spend_by(db: Session, start: datetime, end: datetime, purpose: str | None, group_by: GroupBy) -> dict[str, Decimal]:
    """Itemized transactions count by their line items' category/purpose; others by the transaction's."""
    in_month = (Transaction.timestamp >= start, Transaction.timestamp < end, Transaction.excluded.is_(False))

    whole = select(Transaction.category, Transaction.purpose, func.sum(Transaction.amount)).where(
        *in_month, Transaction.is_itemized.is_(False)
    )
    if purpose:
        whole = whole.where(Transaction.purpose == purpose)

    items = (
        select(
            TransactionItem.category,
            TransactionItem.purpose,
            func.sum(TransactionItem.price),
        )
        .join(Transaction)
        .where(*in_month, Transaction.is_itemized.is_(True))
    )
    if purpose:
        items = items.where(TransactionItem.purpose == purpose)

    totals: dict[str, Decimal] = {}
    queries = (
        whole.group_by(Transaction.category, Transaction.purpose),
        items.group_by(TransactionItem.category, TransactionItem.purpose),
    )
    for query in queries:
        for category, item_purpose, amount in db.execute(query):
            key = budget_line(category, item_purpose) if group_by == "budget_line" else category
            totals[key] = totals.get(key, Decimal("0")) + amount
    return totals


def month_of(when: datetime, tz: ZoneInfo) -> str:
    return when.astimezone(tz).strftime("%Y-%m")


# Full months looked back on for the usual amount (the median, so one big month doesn't skew it).
ESTIMATE_MONTHS = 6


def _shift(month: str, by: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + by
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def usual_spend(db: Session, month: str, tz: ZoneInfo, purpose: str | None, group_by: GroupBy) -> dict[str, Decimal]:
    """Median monthly spend on the estimated lines over the full months before `month`. The first month with
    any transactions is skipped (history starts partway through it), and so is everything before it."""
    first = db.scalar(select(func.min(Transaction.timestamp)).where(Transaction.excluded.is_(False)))
    if first is None:
        return {}
    first_full = _shift(month_of(first, tz), 1)
    history = []
    for k in range(1, ESTIMATE_MONTHS + 1):
        past = _shift(month, -k)
        if past < first_full:
            break
        history.append(spend_by(db, *month_bounds(past, tz), purpose, group_by))
    if not history:
        return {}
    usual = {}
    # Everyday necessities with no bill (categories switched to "estimated"): what they usually cost a month
    # stands in for what is still to come.
    for line in categories_with(db, "estimated"):
        amount = Decimal(statistics.median([h.get(line, Decimal("0")) for h in history])).quantize(Decimal("0.01"))
        if amount > 0:
            usual[line] = amount
    return usual


def estimated_spend(
    db: Session, month: str, tz: ZoneInfo, today: date, spent: dict[str, Decimal], bills_due: dict[str, Decimal],
    purpose: str | None = None, group_by: GroupBy = "budget_line",
) -> dict[str, Decimal]:
    """What the necessities will likely still cost this month: the usual amount less what is already spent and
    the bills still due on that line, never below zero. Nothing is estimated for a past month."""
    start, end = month_bounds(month, tz)
    if end.date() <= today:
        return {}
    out = {}
    for line, usual in usual_spend(db, month, tz, purpose, group_by).items():
        rest = usual - spent.get(line, Decimal("0")) - bills_due.get(line, Decimal("0"))
        if rest > 0:
            out[line] = rest
    return out
