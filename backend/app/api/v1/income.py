import uuid
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import require_token
from app.db.base import get_db
from app.db.models import Account, IncomeSource, Transaction
from app.schemas import (
    IncomeIn,
    IncomeMonthOut,
    IncomeSourceIn,
    IncomeSourceOut,
    IncomeSourcePatch,
    PayDateOut,
    TransactionOut,
)
from app.services import income
from app.services.ingest import NewTransaction, ingest_transaction
from app.services.notes import clean_note
from app.services.spending import month_bounds, spend_by

router = APIRouter(prefix="/income", tags=["income"])

MANUAL = "manual"


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _require_account(db: Session, account_id: str | None) -> None:
    if account_id and db.get(Account, account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {account_id!r}")


@router.get("", response_model=IncomeMonthOut)
def month_income(month: str | None = None, earner: str | None = None, db: Session = Depends(get_db)) -> IncomeMonthOut:
    """A month's income: received and still expected, by person, with each deposit and pay date."""
    tz = _tz()
    now = datetime.now(tz)
    month = month or now.strftime("%Y-%m")
    try:
        start, end = month_bounds(month, tz)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    spent = sum(spend_by(db, start, end, earner, "budget_line").values(), Decimal("0.00"))
    totals = income.totals(db, start, end, now.date(), earner, spent, Decimal("0.00"))
    query = select(Transaction).where(
        Transaction.txn_type == income.INCOME, Transaction.timestamp >= start, Transaction.timestamp < end
    )
    if earner:
        query = query.where(Transaction.purpose == earner)
    pay_dates = [
        PayDateOut(source_id=p.source.id, name=p.source.name, earner=p.source.earner, day=p.day,
                   amount=p.source.amount, status=p.status)
        for p in income.schedule(db, start.date(), end.date(), now.date())
        if earner in (None, p.source.earner)
    ]
    return IncomeMonthOut(
        month=month, earner=earner, **totals,
        deposits=list(db.scalars(query.order_by(Transaction.timestamp.desc()))),
        pay_dates=pay_dates,
    )


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def add_income(body: IncomeIn, db: Session = Depends(get_db), user: str = Depends(require_token)) -> Transaction:
    """Record income by hand: a deposit no alert or screenshot reported."""
    _require_account(db, body.account_id)
    txn, _ = ingest_transaction(
        db,
        NewTransaction(
            source=MANUAL, source_ref=None, account_id=body.account_id,
            timestamp=datetime.combine(body.received_on, time(12, 0), tzinfo=_tz()),
            amount=body.amount, merchant=body.payer, income=True,
        ),
        raw_payload={**body.model_dump(mode="json"), "entered_by": user},
    )
    txn.purpose, txn.purpose_is_manual = body.earner, True
    txn.category, txn.category_is_manual = body.category, True
    txn.note = clean_note(body.note)
    txn.edited_by = user
    db.commit()
    db.refresh(txn)
    return txn


@router.get("/sources", response_model=list[IncomeSourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[IncomeSource]:
    return list(db.scalars(select(IncomeSource).order_by(IncomeSource.active.desc(), IncomeSource.name)))


@router.post("/sources", response_model=IncomeSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(body: IncomeSourceIn, db: Session = Depends(get_db)) -> IncomeSource:
    _require_account(db, body.account_id)
    source = IncomeSource(**body.model_dump())
    db.add(source)
    db.flush()
    income.link_existing(db, source)
    db.commit()
    db.refresh(source)
    return source


def _get_source(db: Session, source_id: uuid.UUID) -> IncomeSource:
    source = db.get(IncomeSource, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Income source not found")
    return source


@router.patch("/sources/{source_id}", response_model=IncomeSourceOut)
def update_source(source_id: uuid.UUID, body: IncomeSourcePatch, db: Session = Depends(get_db)) -> IncomeSource:
    source = _get_source(db, source_id)
    changes = body.model_dump(exclude_unset=True)
    _require_account(db, changes.get("account_id"))
    for field, value in changes.items():
        setattr(source, field, value)
    db.flush()
    income.link_existing(db, source)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """Its paycheques stay income; they are just no longer tied to a schedule."""
    db.delete(_get_source(db, source_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
