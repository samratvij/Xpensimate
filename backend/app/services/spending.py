import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
