import uuid
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.budget_evaluator import check_transaction
from app.core.config import get_settings
from app.core.security import require_token
from app.db.base import get_db
from app.db.models import RawIngestLog, Receipt, ReceiptItem, Transaction
from app.schemas import CandidateOut, MatchIn, ReceiptDetail, ReceiptItemPatch, ReceiptOut
from app.services.categories import category_names, require_category
from app.services.notes import clean_note
from app.services.ocr_service import (
    OcrError,
    extract_receipt,
    is_balanced,
    money,
    normalize_image,
    now_utc,
    receipt_timestamp,
)
from app.services.reconciler import apply_match, transaction_candidates, try_match_receipt, unmatch

router = APIRouter(prefix="/receipts", tags=["receipts"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
RECEIPT_OCR = "receipt_ocr"


def _image_path(receipt_id: uuid.UUID) -> Path:
    return Path(get_settings().receipts_dir) / f"{receipt_id}.jpg"


def _get_or_404(db: Session, receipt_id: uuid.UUID) -> Receipt:
    receipt = db.scalar(select(Receipt).where(Receipt.id == receipt_id).options(selectinload(Receipt.items)))
    if receipt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return receipt


@router.post("/ocr", response_model=ReceiptDetail, status_code=status.HTTP_201_CREATED)
def upload_receipt(
    file: UploadFile = File(...), db: Session = Depends(get_db), user: str = Depends(require_token)
) -> Receipt:
    settings = get_settings()
    if not settings.ai_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Receipt reading needs ANTHROPIC_API_KEY, or LLM_PROVIDER=openai and a model server")

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is larger than 15 MB")
    try:
        jpeg = normalize_image(data)
    except ValueError:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a JPEG, PNG or WebP photo")

    try:
        ocr = extract_receipt(jpeg, category_names(db))
    except OcrError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not read the receipt: {e}")
    if not ocr.is_receipt:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That doesn't look like a receipt")

    timestamp = receipt_timestamp(ocr, ZoneInfo(settings.timezone))
    receipt = Receipt(
        id=uuid.uuid4(),
        merchant=ocr.merchant.strip()[:255] or "Unknown merchant",
        timestamp=(timestamp or now_utc()).astimezone(timezone.utc),
        total=money(ocr.total),
        tax=money(ocr.tax),
        raw_ocr_response=ocr.model_dump(mode="json"),
        needs_review=timestamp is None or not ocr.lines or not is_balanced(ocr),
        created_by=user,
        items=[
            ReceiptItem(
                position=i,
                sku_name=(line.name or line.raw_text).strip()[:255],
                raw_text=line.raw_text.strip()[:255] or None,
                category=line.category,
                purpose=line.purpose,
                price=money(line.line_total),
                quantity=money(line.quantity or 1),
            )
            for i, line in enumerate(ocr.lines)
        ],
    )
    path = _image_path(receipt.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)
    receipt.image_path = str(path)

    db.add(RawIngestLog(source=RECEIPT_OCR, payload={"receipt_id": str(receipt.id), "ocr": receipt.raw_ocr_response}))
    db.add(receipt)
    try:
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if matched := try_match_receipt(db, _get_or_404(db, receipt.id)):
        check_transaction(db, matched)
    return _get_or_404(db, receipt.id)


@router.get("", response_model=list[ReceiptOut])
def list_receipts(
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)
) -> list[Receipt]:
    query = select(Receipt).order_by(Receipt.timestamp.desc(), Receipt.id).limit(limit).offset(offset)
    return list(db.scalars(query))


@router.get("/{receipt_id}", response_model=ReceiptDetail)
def get_receipt(receipt_id: uuid.UUID, db: Session = Depends(get_db)) -> Receipt:
    return _get_or_404(db, receipt_id)


@router.get("/{receipt_id}/image", response_class=FileResponse)
def get_receipt_image(receipt_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    _get_or_404(db, receipt_id)
    path = _image_path(receipt_id)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/{receipt_id}/items/{item_id}", response_model=ReceiptDetail)
def update_receipt_item(
    receipt_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ReceiptItemPatch,
    db: Session = Depends(get_db),
    user: str = Depends(require_token),
) -> Receipt:
    require_category(db, body.category)
    item = db.get(ReceiptItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt item not found")
    if body.category is not None:
        item.category = body.category
        item.category_is_manual = True
    if body.purpose is not None:
        item.purpose = body.purpose
        item.purpose_is_manual = True
    if body.note is not None:
        item.note = clean_note(body.note)
    db.commit()
    receipt = _get_or_404(db, receipt_id)
    # Already matched: rebuild the transaction's lines so the edit reaches the summary.
    if receipt.matched_transaction_id and (txn := db.get(Transaction, receipt.matched_transaction_id)):
        apply_match(db, receipt, txn)
        txn.edited_by = user
        db.commit()
        check_transaction(db, txn)
    return _get_or_404(db, receipt_id)


@router.get("/{receipt_id}/candidates", response_model=list[CandidateOut])
def match_candidates(receipt_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    """Card transactions this receipt could belong to, best first (for the app's manual match)."""
    receipt = _get_or_404(db, receipt_id)
    return [
        {"transaction": c.transaction, "amount_diff": c.amount_diff, "hours_apart": c.hours_apart,
         "merchant_score": c.merchant_score, "tip": c.tip}
        for c in transaction_candidates(db, receipt)
    ]


@router.post("/{receipt_id}/match", response_model=ReceiptDetail)
def match_receipt(receipt_id: uuid.UUID, body: MatchIn, db: Session = Depends(get_db)) -> Receipt:
    """Match by hand. Any unmatched transaction is allowed, even outside the automatic window and tolerance."""
    receipt = _get_or_404(db, receipt_id)
    if receipt.matched_transaction_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Receipt is already matched; unmatch it first")
    txn = db.get(Transaction, body.transaction_id)
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    claimed = db.scalar(select(Receipt.id).where(Receipt.matched_transaction_id == txn.id))
    if txn.is_itemized or claimed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transaction already has a receipt")
    apply_match(db, receipt, txn)
    db.commit()
    check_transaction(db, txn)
    return _get_or_404(db, receipt_id)


@router.post("/{receipt_id}/unmatch", response_model=ReceiptDetail)
def unmatch_receipt(receipt_id: uuid.UUID, db: Session = Depends(get_db)) -> Receipt:
    receipt = _get_or_404(db, receipt_id)
    unmatch(db, receipt)
    db.commit()
    return _get_or_404(db, receipt_id)


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(receipt_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    receipt = _get_or_404(db, receipt_id)
    unmatch(db, receipt)
    db.delete(receipt)
    db.commit()
    _image_path(receipt_id).unlink(missing_ok=True)
