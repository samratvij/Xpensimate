from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import BudgetEnvelope
from app.schemas import CategorySpend, SummaryOut
from app.services.recurring import expected_spend
from app.services.spending import GroupBy, month_bounds, spend_by

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("", response_model=SummaryOut)
def get_summary(
    month: str | None = None,
    purpose: str | None = None,
    group_by: GroupBy = "budget_line",
    db: Session = Depends(get_db),
) -> SummaryOut:
    tz = ZoneInfo(get_settings().timezone)
    month = month or datetime.now(tz).strftime("%Y-%m")
    try:
        start, end = month_bounds(month, tz)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))

    spent = spend_by(db, start, end, purpose, group_by)
    expected = expected_spend(db, start.date(), end.date(), datetime.now(tz).date(), group_by, purpose)
    # Budgets are per budget line, so they only apply to that grouping.
    budgets = {b.category: b for b in db.scalars(select(BudgetEnvelope))} if group_by == "budget_line" else {}

    rows = []
    for category in sorted(spent.keys() | budgets.keys() | expected.keys()):
        amount = spent.get(category, Decimal("0.00"))
        still_due = expected.get(category, Decimal("0.00"))
        budget = budgets.get(category)
        percent = float(amount / budget.monthly_limit * 100) if budget else None
        rows.append(
            CategorySpend(
                category=category,
                spent=amount,
                expected=still_due,
                projected=amount + still_due,
                monthly_limit=budget.monthly_limit if budget else None,
                warning_threshold_percent=budget.warning_threshold_percent if budget else None,
                percent_used=round(percent, 1) if percent is not None else None,
                over_warning=budget is not None and percent >= float(budget.warning_threshold_percent),
                over_limit=budget is not None and amount > budget.monthly_limit,
                projected_over_limit=budget is not None and amount + still_due > budget.monthly_limit,
            )
        )
    return SummaryOut(
        month=month,
        group_by=group_by,
        purpose=purpose,
        total_spent=sum(spent.values(), Decimal("0.00")),
        total_expected=sum(expected.values(), Decimal("0.00")),
        categories=rows,
    )
