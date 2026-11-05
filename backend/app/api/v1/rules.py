import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Account, CategorizationRule
from app.schemas import RuleIn, RuleOut
from app.services.categories import require_category

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)) -> list[CategorizationRule]:
    return list(
        db.scalars(
            select(CategorizationRule).order_by(
                CategorizationRule.priority.desc(), CategorizationRule.merchant_pattern
            )
        )
    )


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleIn, db: Session = Depends(get_db)) -> CategorizationRule:
    require_category(db, body.target_category)
    if body.account_id and db.get(Account, body.account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {body.account_id!r}")
    if body.hour_start is not None and body.hour_end is not None and body.hour_start >= body.hour_end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hour_start must be before hour_end")
    rule = CategorizationRule(**body.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    rule = db.get(CategorizationRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    db.delete(rule)
    db.commit()
