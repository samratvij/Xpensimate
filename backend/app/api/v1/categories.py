from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import BudgetEnvelope
from app.db.models import CategoryRow
from app.schemas import CategoryList, CategoryName, CategoryOut, CategoryRoles
from app.services import categories as svc

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryOut]:
    used = svc.usage(db)
    budgets = {b.category: b.monthly_limit for b in db.scalars(select(BudgetEnvelope))}
    rows = {r.name: r for r in db.scalars(select(CategoryRow))}
    return [CategoryOut(name=n, transactions=used.get(n, 0), monthly_limit=budgets.get(n),
                        **(svc.roles_of(rows[n]) if n in rows else {})) for n in svc.category_names(db)]


@router.patch("/{name}", response_model=CategoryOut)
def set_roles(name: str, body: CategoryRoles, db: Session = Depends(get_db)) -> CategoryOut:
    """Switch what a category is for: estimated (usual monthly spend), subscriptions, tipped."""
    row = svc.set_roles(db, name, body.model_dump(exclude_none=True))
    budget = db.get(BudgetEnvelope, row.name)
    return CategoryOut(name=row.name, transactions=svc.usage(db).get(row.name, 0),
                       monthly_limit=budget.monthly_limit if budget else None, **svc.roles_of(row))


@router.put("", response_model=list[CategoryOut])
def set_categories(body: CategoryList, db: Session = Depends(get_db)) -> list[CategoryOut]:
    """Saves the whole list at once (setup): adds, renames, reorders, and removes categories nothing is filed under."""
    svc.set_categories(db, [(c.name, c.original) for c in body.categories])
    return list_categories(db)


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
                       monthly_limit=budget.monthly_limit if budget else None, **svc.roles_of(row))


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def remove_category(name: str, into: str = Query(..., description="Category that takes over its transactions"),
                    db: Session = Depends(get_db)) -> None:
    svc.merge_category(db, name, into)
