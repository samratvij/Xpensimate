import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.budget_evaluator import check_transaction
from app.core.config import get_settings
from app.core.security import require_token
from app.core.taxonomy import UNCATEGORIZED, budget_line_condition
from app.db.base import get_db
from app.db.models import Account, CategorizationRule, Receipt, Transaction, TransactionItem
from app.schemas import (
    ManualTransactionIn,
    RecategorizeOut,
    SplitIn,
    TransactionDetail,
    TransactionOut,
    TransactionPatch,
    TransactionPush,
)
from app.services.categorizer import apply_classification, categorize
from app.services.categories import require_category
from app.services.notes import clean_note
from app.core.taxonomy import earners
from app.services.income import INCOME, INCOME_CATEGORIES
from app.services.savings import TRANSFER
from app.services.export import build_statement, to_csv, to_pdf
from app.services.ingest import NewTransaction, ingest_transaction
from app.services.reconciler import summarize_labels, try_match_transaction, unmatch

router = APIRouter(prefix="/transactions", tags=["transactions"])

ANDROID_PUSH = "android_push"
# Entered by hand: here, a bill marked paid, or income added by hand.
MANUAL = "manual"
# Rules saved from manual edits outrank the seeded starter rules (priority 1).
REMEMBERED_RULE_PRIORITY = 10
RECATEGORIZE_BATCH = 200


@router.post("/push", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def push_transaction(body: TransactionPush, response: Response, db: Session = Depends(get_db)) -> Transaction:
    if body.account_id and db.get(Account, body.account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {body.account_id!r}")
    txn, created = ingest_transaction(
        db,
        NewTransaction(
            source=ANDROID_PUSH,
            source_ref=body.source_ref,
            account_id=body.account_id,
            timestamp=body.timestamp,
            amount=body.amount,
            merchant=body.merchant,
            currency=body.currency,
        ),
        raw_payload=body.model_dump(mode="json"),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return txn


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def add_transaction(
    body: ManualTransactionIn, db: Session = Depends(get_db), user: str = Depends(require_token)
) -> Transaction:
    """Record a purchase by hand (cash, or anything no feed will report), on any day."""
    if body.account_id and db.get(Account, body.account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {body.account_id!r}")
    require_category(db, body.category)
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz)
    # Today's entry keeps the time it was made, so it sorts with the day's other charges; past days get noon.
    when = now if body.spent_on == now.date() else datetime.combine(body.spent_on, time(12, 0), tzinfo=tz)
    if when > now:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That day hasn't happened yet")
    txn = Transaction(
        source=MANUAL, source_ref=None, account_id=body.account_id, timestamp=when, amount=body.amount,
        txn_type="PURCHASE", merchant_raw=body.merchant, category_is_manual=False, purpose_is_manual=False,
        note=clean_note(body.note), edited_by=user,
    )
    apply_classification(txn, categorize(
        db, merchant=body.merchant, account_id=body.account_id, amount=body.amount, timestamp=when,
    ))
    if body.category is not None:
        txn.category, txn.category_is_manual, txn.categorized_by = body.category, True, "manual"
    if body.purpose is not None:
        txn.purpose, txn.purpose_is_manual = body.purpose, True
    db.add(txn)
    db.commit()
    db.refresh(txn)
    try_match_transaction(db, txn)  # a receipt photo may already be waiting for it
    check_transaction(db, txn)
    return txn


@router.post("/recategorize", response_model=RecategorizeOut)
def recategorize(
    include_categorized: bool = Query(False, description="Also re-run on already-categorized, non-manual rows"),
    db: Session = Depends(get_db),
) -> RecategorizeOut:
    """Re-run the categorizer (e.g. after adding rules or an API key). Manual choices are never overwritten."""
    query = select(Transaction).where(Transaction.category_is_manual.is_(False))
    if not include_categorized:
        query = query.where(Transaction.category == UNCATEGORIZED)
    txns = list(db.scalars(query.order_by(Transaction.timestamp.desc()).limit(RECATEGORIZE_BATCH)))

    changed = 0
    for txn in txns:
        before = (txn.category, txn.purpose)
        result = categorize(
            db,
            merchant=txn.merchant_raw,
            account_id=txn.account_id,
            amount=txn.amount,
            timestamp=txn.timestamp,
            currency=txn.currency,
            exclude_id=txn.id,
        )
        apply_classification(txn, result)
        changed += (txn.category, txn.purpose) != before
    db.commit()
    return RecategorizeOut(examined=len(txns), changed=changed)


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    start: datetime | None = None,
    end: datetime | None = None,
    category: str | None = None,
    purpose: str | None = None,
    account_id: str | None = None,
    budget_line: str | None = Query(None, description="Only what counts toward this budget line (excludes non-expenses)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[Transaction]:
    query = select(Transaction)
    if start:
        query = query.where(Transaction.timestamp >= start)
    if end:
        query = query.where(Transaction.timestamp < end)
    if category:
        query = query.where(Transaction.category == category)
    if purpose:
        query = query.where(Transaction.purpose == purpose)
    if account_id:
        query = query.where(Transaction.account_id == account_id)
    if budget_line:
        # Receipt-itemized charges count by their lines, so they appear under each line they touch.
        item_in_line = exists().where(
            TransactionItem.transaction_id == Transaction.id,
            budget_line_condition(budget_line, TransactionItem.category, TransactionItem.purpose),
        )
        query = query.where(
            Transaction.excluded.is_(False),
            or_(
                and_(Transaction.is_itemized.is_(False),
                     budget_line_condition(budget_line, Transaction.category, Transaction.purpose)),
                and_(Transaction.is_itemized.is_(True), item_in_line),
            ),
        )
    query = query.order_by(Transaction.timestamp.desc(), Transaction.id).limit(limit).offset(offset)
    return list(db.scalars(query))


@router.get("/export", response_class=Response)
def export_transactions(
    start: date = Query(..., description="First day, in the household's time zone"),
    end: date = Query(..., description="Last day, included"),
    format: Literal["csv", "pdf"] = "csv",
    account_id: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    """Every transaction from start to end (both days included), as a statement: CSV for a spreadsheet, or a PDF."""
    if account_id and db.get(Account, account_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {account_id!r}")
    tz = ZoneInfo(get_settings().timezone)
    try:
        statement = build_statement(db, start, end, tz, account_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    if format == "pdf":
        content, media = to_pdf(statement, datetime.now(tz)), "application/pdf"
    else:
        content, media = to_csv(statement), "text/csv; charset=utf-8"
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{statement.filename_stem}.{format}"',
    })


def _get_or_404(db: Session, txn_id: uuid.UUID) -> Transaction:
    txn = db.scalar(select(Transaction).where(Transaction.id == txn_id).options(selectinload(Transaction.items)))
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    return txn


def _receipt_id(db: Session, txn_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(select(Receipt.id).where(Receipt.matched_transaction_id == txn_id))


def _detail(db: Session, txn_id: uuid.UUID) -> TransactionDetail:
    detail = TransactionDetail.model_validate(_get_or_404(db, txn_id))
    detail.receipt_id = _receipt_id(db, txn_id)
    return detail


@router.get("/{txn_id}", response_model=TransactionDetail)
def get_transaction(txn_id: uuid.UUID, db: Session = Depends(get_db)) -> TransactionDetail:
    return _detail(db, txn_id)


def _remember(db: Session, txn: Transaction, purpose_set: bool) -> None:
    pattern = txn.merchant_normalized or txn.merchant_raw
    rule = db.scalar(
        select(CategorizationRule).where(
            CategorizationRule.merchant_pattern == pattern, CategorizationRule.account_id.is_(None)
        )
    ) or CategorizationRule(merchant_pattern=pattern, priority=REMEMBERED_RULE_PRIORITY)
    rule.target_category = txn.category
    if purpose_set:
        rule.target_purpose = txn.purpose
    rule.priority = max(rule.priority or 0, REMEMBERED_RULE_PRIORITY)
    db.add(rule)


def _check_income_patch(body: TransactionPatch) -> None:
    """Income stays income: never counted as spending, filed as an income kind, earned by a person."""
    if body.excluded is False:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Income isn't spending, so it can't be counted as an expense")
    if body.category is not None and body.category not in INCOME_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Income is one of {', '.join(INCOME_CATEGORIES)}")
    if body.purpose is not None and body.purpose not in earners():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Income is earned by one of {', '.join(earners())}")
    if body.remember:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Rules are for spending; edit the income source instead")


@router.patch("/{txn_id}", response_model=TransactionDetail)
def update_transaction(
    txn_id: uuid.UUID, body: TransactionPatch, db: Session = Depends(get_db), user: str = Depends(require_token)
) -> TransactionDetail:
    txn = _get_or_404(db, txn_id)
    if txn.txn_type == INCOME:
        _check_income_patch(body)
    if any(v is not None for v in (body.category, body.purpose, body.excluded, body.note)):
        txn.edited_by = user
    if body.note is not None:
        txn.note = clean_note(body.note)
    if txn.txn_type != INCOME:
        require_category(db, body.category)
    if body.category is not None:
        txn.category = body.category
        txn.category_is_manual = True
    if body.purpose is not None:
        txn.purpose = body.purpose
        txn.purpose_is_manual = True
    if body.excluded is not None and body.excluded != txn.excluded:
        txn.excluded = body.excluded
        if txn.txn_type == TRANSFER and not body.excluded:
            # Not a move between own accounts after all: money that left this account is spending.
            if txn.account_id is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Money coming in can't be counted as an expense")
            txn.txn_type, txn.counterpart_account_id = "PURCHASE", None
        # An excluded charge can't own a receipt; free it to match the right one.
        if body.excluded and (receipt := db.scalar(select(Receipt).where(Receipt.matched_transaction_id == txn.id))):
            unmatch(db, receipt)
    if body.remember:
        if txn.category == UNCATEGORIZED:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a category before remembering it")
        _remember(db, txn, purpose_set=body.purpose is not None)
    db.commit()
    if txn.txn_type != INCOME and (body.category is not None or body.purpose is not None or body.excluded is False):
        check_transaction(db, txn)
    return _detail(db, txn_id)


@router.delete("/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(txn_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """Remove a transaction entered by hand (a typo, or one a statement later reported). Feed rows stay:
    deleting them would let the next sync bring them back; mark them not an expense instead."""
    txn = _get_or_404(db, txn_id)
    if txn.source != MANUAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only transactions added by hand can be deleted")
    if receipt := db.scalar(select(Receipt).where(Receipt.matched_transaction_id == txn.id)):
        unmatch(db, receipt)
    db.delete(txn)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{txn_id}/items", response_model=TransactionDetail)
def split_transaction(
    txn_id: uuid.UUID, body: SplitIn, db: Session = Depends(get_db), user: str = Depends(require_token)
) -> TransactionDetail:
    """Split one charge into lines with their own what and who (e.g. an Amazon order); [] removes the split."""
    txn = _get_or_404(db, txn_id)
    if txn.txn_type == INCOME:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Income can't be split")
    if _receipt_id(db, txn.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "These lines come from a receipt: edit them there, or unmatch the receipt first"
        )
    if body.items:
        if len(body.items) < 2:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A split needs at least two lines")
        for line in body.items:
            require_category(db, line.category)
            if line.price == 0:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{line.sku_name!r} needs an amount")
        total = sum((line.price for line in body.items), Decimal("0"))
        if total != txn.amount:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Lines add up to {total}, but the charge is {txn.amount}"
            )
    txn.items.clear()
    txn.items.extend(
        TransactionItem(
            sku_name=line.sku_name,
            category=line.category,
            category_is_manual=True,
            purpose=line.purpose,
            purpose_is_manual=True,
            price=line.price,
            note=clean_note(line.note),
        )
        for line in body.items
    )
    txn.is_itemized = bool(body.items)
    txn.edited_by = user
    summarize_labels(txn, keep_manual=False)
    db.commit()
    check_transaction(db, txn)
    return _detail(db, txn_id)
