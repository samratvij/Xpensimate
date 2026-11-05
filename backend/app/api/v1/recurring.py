import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.budget_evaluator import check_transaction
from app.core.config import get_settings
from app.core.security import require_token
from app.db.base import get_db
from app.db.models import RecurringExpense, Transaction
from app.schemas import ExpectedOut, MarkPaidIn, RecurringIn, RecurringOut, RecurringPatch, TransactionOut
from app.services.categories import require_category
from app.services.recurring import detect, link_existing, occurrences, save_detected
from app.services.spending import month_bounds

router = APIRouter(prefix="/recurring", tags=["recurring"])

MANUAL = "manual"


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def _get_or_404(db: Session, recurring_id: uuid.UUID) -> RecurringExpense:
    item = db.get(RecurringExpense, recurring_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recurring expense not found")
    return item


@router.get("", response_model=list[RecurringOut])
def list_recurring(db: Session = Depends(get_db)) -> list[RecurringExpense]:
    return list(db.scalars(select(RecurringExpense).order_by(RecurringExpense.active.desc(), RecurringExpense.amount.desc())))


@router.post("", response_model=RecurringOut, status_code=status.HTTP_201_CREATED)
def create_recurring(body: RecurringIn, db: Session = Depends(get_db)) -> RecurringExpense:
    require_category(db, body.category)
    item = RecurringExpense(**body.model_dump(), origin="user")
    db.add(item)
    db.flush()
    link_existing(db, item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/detect", response_model=list[RecurringOut])
def detect_recurring(db: Session = Depends(get_db)) -> list[RecurringExpense]:
    """Find repeating charges in the imported history and transactions; returns the newly added ones."""
    return save_detected(db, detect(db, _today()))


@router.get("/expected", response_model=list[ExpectedOut])
def expected(month: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    """Every recurring due date in the month with its status: paid, upcoming or overdue."""
    tz = ZoneInfo(get_settings().timezone)
    try:
        start, end = month_bounds(month or datetime.now(tz).strftime("%Y-%m"), tz)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return [
        {"recurring": o.item, "due": o.due, "status": o.status, "budget_line": o.budget_line}
        for o in occurrences(db, start.date(), end.date(), _today())
    ]


@router.patch("/{recurring_id}", response_model=RecurringOut)
def update_recurring(recurring_id: uuid.UUID, body: RecurringPatch, db: Session = Depends(get_db)) -> RecurringExpense:
    item = _get_or_404(db, recurring_id)
    require_category(db, body.category)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    item.origin = "user"  # edited: detection leaves it alone from now on
    link_existing(db, item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{recurring_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurring(recurring_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    db.delete(_get_or_404(db, recurring_id))
    db.commit()


@router.post("/{recurring_id}/paid", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def mark_paid(
    recurring_id: uuid.UUID, body: MarkPaidIn, db: Session = Depends(get_db), user: str = Depends(require_token)
) -> Transaction:
    """Record a payment made outside the cards Xpensimate sees (e.g. rent by e-transfer) as a transaction."""
    item = _get_or_404(db, recurring_id)
    paid_on = body.paid_on or _today()
    source_ref = f"recurring:{item.id}:{paid_on.isoformat()}"
    if db.scalar(select(Transaction.id).where(Transaction.source == MANUAL, Transaction.source_ref == source_ref)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{item.name} is already marked paid on {paid_on}")
    txn = Transaction(
        source=MANUAL,
        source_ref=source_ref,
        timestamp=datetime.combine(paid_on, time(12, 0), tzinfo=ZoneInfo(get_settings().timezone)),
        amount=body.amount or item.amount,
        txn_type="PURCHASE",
        merchant_raw=item.name,
        merchant_normalized=item.name,
        category=item.category,
        category_is_manual=True,
        purpose=item.purpose,
        purpose_is_manual=True,
        categorized_by="manual",
        recurring_id=item.id,
        edited_by=user,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    check_transaction(db, txn)
    return txn
