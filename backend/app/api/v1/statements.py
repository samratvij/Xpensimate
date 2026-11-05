import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Account, StatementImport
from app.schemas import StatementCommitIn, StatementImportOut, StatementPreviewOut
from app.statements.parsers import parse_statement
from app.statements.reconcile import apply_import, counts, plan

router = APIRouter(prefix="/statements", tags=["statements"])

MAX_STATEMENT_BYTES = 5 * 1024 * 1024
DECISIONS = ("add", "skip")


def _out(imp: StatementImport, model=StatementImportOut):
    return model.model_validate({**{c: getattr(imp, c) for c in model.model_fields if hasattr(imp, c)},
                                 "counts": counts(imp.rows)})


def _get_or_404(db: Session, import_id: uuid.UUID) -> StatementImport:
    imp = db.get(StatementImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement import not found")
    return imp


@router.post("/preview", response_model=StatementPreviewOut, status_code=status.HTTP_201_CREATED)
def preview_statement(
    file: UploadFile = File(...),
    account_id: str | None = Form(None),
    month: str | None = Form(None, pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
) -> StatementPreviewOut:
    """Parse a bank CSV (or RBC PDF) and show what importing it would do. Nothing changes until /commit."""
    content = file.file.read(MAX_STATEMENT_BYTES + 1)
    if len(content) > MAX_STATEMENT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Statement is larger than 5 MB")
    try:
        parsed = parse_statement(content)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    account = account_id or parsed.account_id
    if db.get(Account, account) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown account_id {account!r}")

    # Previews that were never imported are only useful for a while.
    db.execute(delete(StatementImport).where(StatementImport.status == "preview",
                                             StatementImport.created_at < func.now() - timedelta(days=1)))
    rows, missing, start, end = plan(db, parsed, account, month)
    imp = StatementImport(filename=(file.filename or "statement")[:255], bank_format=parsed.bank_format,
                          account_id=account, period_start=start, period_end=end, rows=rows, missing=missing)
    db.add(imp)
    db.commit()
    db.refresh(imp)
    return _out(imp, StatementPreviewOut)


@router.get("", response_model=list[StatementImportOut])
def list_imports(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)) -> list[StatementImportOut]:
    query = select(StatementImport).where(StatementImport.status != "preview").order_by(StatementImport.created_at.desc())
    return [_out(i) for i in db.scalars(query.limit(limit))]


@router.get("/{import_id}", response_model=StatementPreviewOut)
def get_import(import_id: uuid.UUID, db: Session = Depends(get_db)) -> StatementPreviewOut:
    """The preview, or the progress and outcome of an import (poll while status is "importing")."""
    return _out(_get_or_404(db, import_id), StatementPreviewOut)


@router.post("/{import_id}/commit", response_model=StatementImportOut, status_code=status.HTTP_202_ACCEPTED)
def commit_import(
    import_id: uuid.UUID, body: StatementCommitIn, background: BackgroundTasks, db: Session = Depends(get_db)
) -> StatementImportOut:
    imp = _get_or_404(db, import_id)
    if imp.status != "preview":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This statement is already {imp.status}")
    by_index = {str(r["index"]): r for r in imp.rows}
    for index, decision in body.decisions.items():
        row = by_index.get(index)
        if row is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No row {index}")
        if decision in DECISIONS:
            continue
        if decision.startswith("match:") and decision[6:] in {c["id"] for c in row.get("candidates", [])}:
            continue
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Row {index}: invalid decision {decision!r}")
    undecided = [r["index"] for r in imp.rows if r["action"] == "unclear" and str(r["index"]) not in body.decisions]
    if undecided:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Decide the unclear rows first: {undecided}")

    imp.status = "importing"
    db.commit()
    background.add_task(apply_import, imp.id, body.decisions)
    return _out(imp)
