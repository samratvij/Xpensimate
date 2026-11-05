from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import BudgetEnvelope
from app.schemas import CategoryName, CategoryOut
from app.services import categories as svc

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryOut]:
    used = svc.usage(db)
    budgets = {b.category: b.monthly_limit for b in db.scalars(select(BudgetEnvelope))}
    return [CategoryOut(name=n, transactions=used.get(n, 0), monthly_limit=budgets.get(n)) for n in svc.category_names(db)]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def add_category(body: CategoryName, db: Session = Depends(get_db)) -> CategoryOut:
    row = svc.add_category(db, body.name)
    return CategoryOut(name=row.name, transactions=0, monthly_limit=None)


@router.post("/{name}/rename", response_model=CategoryOut)
def rename_category(name: str, body: CategoryName, db: Session = Depends(get_db)) -> CategoryOut:
    """Renames the category everywhere: transactions, receipt lines, rules, budgets, bills and history."""
    row = svc.rename_category(db, name, body.name)
    budget = db.get(BudgetEnvelope, row.name)
    return CategoryOut(name=row.name, transactions=svc.usage(db).get(row.name, 0),
                       monthly_limit=budget.monthly_limit if budget else None)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def remove_category(name: str, into: str = Query(..., description="Category that takes over its transactions"),
                    db: Session = Depends(get_db)) -> None:
    svc.merge_category(db, name, into)
