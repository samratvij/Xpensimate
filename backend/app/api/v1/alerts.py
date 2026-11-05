from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import BudgetAlert
from app.schemas import AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[BudgetAlert]:
    """Alert history, newest first; the app uses it to catch up on anything a push didn't deliver."""
    query = select(BudgetAlert).order_by(BudgetAlert.created_at.desc()).limit(limit)
    if month:
        query = query.where(BudgetAlert.month == month)
    return list(db.scalars(query))
