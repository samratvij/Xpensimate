from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.budget_evaluator import evaluate
from app.services.categories import current_budget_lines
from app.db.base import get_db
from app.db.models import BudgetEnvelope
from app.schemas import BudgetIn, BudgetOut

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetOut])
def list_budgets(db: Session = Depends(get_db)) -> list[BudgetEnvelope]:
    return list(db.scalars(select(BudgetEnvelope).order_by(BudgetEnvelope.category)))


def _check_line(db: Session, line: str) -> str:
    if line not in current_budget_lines(db):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown budget line {line!r}")
    return line


@router.put("/{category}", response_model=BudgetOut)
def upsert_budget(category: str, body: BudgetIn, db: Session = Depends(get_db)) -> BudgetEnvelope:
    """Budgets are per budget line: a category for shared spending, or a person line like "Alex Personal"."""
    _check_line(db, category)
    budget = db.get(BudgetEnvelope, category) or BudgetEnvelope(category=category)
    budget.monthly_limit = body.monthly_limit
    budget.warning_threshold_percent = body.warning_threshold_percent
    db.add(budget)
    db.commit()
    db.refresh(budget)
    evaluate(db, {category})
    return budget


@router.delete("/{category}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(category: str, db: Session = Depends(get_db)) -> None:
    budget = db.get(BudgetEnvelope, category)
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No budget for that category")
    db.delete(budget)
    db.commit()
