import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import Account, SavingsGoal, SavingsTarget
from app.schemas import (
    GoalAccountOut,
    GoalIn,
    GoalOut,
    GoalPatch,
    SavingsPlanOut,
    SavingsTargetIn,
    SavingsTargetOut,
    TargetProgressOut,
)
from app.services import goals

router = APIRouter(prefix="/savings", tags=["savings"])


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def goal_out(db: Session, goal: SavingsGoal, today: date) -> GoalOut:
    p = goals.goal_progress(db, goal, today)
    return GoalOut(
        id=goal.id, name=goal.name, target_amount=goal.target_amount, target_date=goal.target_date,
        account_kinds=goal.account_kinds or [], account_ids=goal.account_ids or [], active=goal.active,
        current=p.current, remaining=p.remaining, percent=p.percent, months_left=p.months_left,
        required_monthly=p.required_monthly, moved_this_month=p.moved_this_month, oldest_balance=p.oldest_balance,
        accounts=[GoalAccountOut(account_id=a.id, account_name=a.account_name, owner=a.owner, balance=b, as_of=d)
                  for a, b, d in p.accounts],
    )


@router.get("", response_model=SavingsPlanOut)
def plan(db: Session = Depends(get_db)) -> SavingsPlanOut:
    """The monthly savings target with this month's progress, and every goal with its progress."""
    today = _today()
    target = db.get(SavingsTarget, goals.HOUSEHOLD)
    progress = goals.target_progress(db, f"{today:%Y-%m}", today)
    return SavingsPlanOut(
        target=SavingsTargetOut.model_validate(target) if target else None,
        progress=TargetProgressOut(**vars(progress)) if progress else None,
        goals=[goal_out(db, g, today) for g in db.scalars(select(SavingsGoal).order_by(SavingsGoal.target_date))],
    )


@router.put("/target", response_model=SavingsTargetOut)
def set_target(body: SavingsTargetIn, db: Session = Depends(get_db)) -> SavingsTarget:
    """Set the household's monthly savings target (0 turns it off)."""
    target = db.get(SavingsTarget, goals.HOUSEHOLD) or SavingsTarget(scope=goals.HOUSEHOLD)
    for field, value in body.model_dump().items():
        setattr(target, field, value)
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def _check_accounts(db: Session, ids: list[str] | None) -> None:
    for account_id in ids or []:
        if db.get(Account, account_id) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account {account_id!r}")


def _get(db: Session, goal_id: uuid.UUID) -> SavingsGoal:
    goal = db.get(SavingsGoal, goal_id)
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Goal not found")
    return goal


@router.post("/goals", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(body: GoalIn, db: Session = Depends(get_db)) -> GoalOut:
    _check_accounts(db, body.account_ids)
    if not body.account_kinds and not body.account_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick the accounts the goal counts")
    goal = SavingsGoal(**body.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal_out(db, goal, _today())


@router.patch("/goals/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: uuid.UUID, body: GoalPatch, db: Session = Depends(get_db)) -> GoalOut:
    goal = _get(db, goal_id)
    changes = body.model_dump(exclude_unset=True)
    _check_accounts(db, changes.get("account_ids"))
    for field, value in changes.items():
        setattr(goal, field, value)
    if not goal.account_kinds and not goal.account_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick the accounts the goal counts")
    db.commit()
    db.refresh(goal)
    return goal_out(db, goal, _today())


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    db.delete(_get(db, goal_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
