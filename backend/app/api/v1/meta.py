from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_token
from app.core.taxonomy import person_lines, purposes
from app.services.categories import category_names, current_budget_lines
from app.db.base import get_db
from app.db.models import Account
from app.schemas import AccountOut, MeOut, TaxonomyOut

router = APIRouter(tags=["meta"])


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.id)))


@router.get("/taxonomy", response_model=TaxonomyOut)
def get_taxonomy(db: Session = Depends(get_db)) -> TaxonomyOut:
    return TaxonomyOut(categories=category_names(db), purposes=list(purposes()),
                       budget_lines=list(current_budget_lines(db)), person_lines=person_lines())


@router.get("/me", response_model=MeOut)
def who_am_i(user: str = Depends(require_token)) -> MeOut:
    return MeOut(user=user)
