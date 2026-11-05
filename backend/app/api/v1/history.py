"""Importing past spending from a CSV: preview it, map its columns, import (see app/services/history_import.py)."""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.services import history_import as history
from app.services.history_import import HistoryImportError

router = APIRouter(prefix="/history", tags=["history"])


class MappingIn(BaseModel):
    date: str
    merchant: str
    amount: str
    date_format: str
    category: str | None = None
    who: str | None = None
    note: str | None = None
    spending_is: str = "positive"
    categories: dict[str, str | None] = {}
    who_values: dict[str, str | None] = {}
    add_as_spending: bool = True


async def _table(file: UploadFile) -> history.Table:
    try:
        return history.read_table(await file.read(history.MAX_BYTES + 1))
    except HistoryImportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("")
def history_status(db: Session = Depends(get_db)) -> dict:
    """What the last history import left: examples, rules, and rows added as spending."""
    return history.status(db)


@router.post("/preview")
async def preview(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    """Read a CSV and suggest how its columns and values map onto Xpensimate's. Nothing is saved."""
    return history.preview(db, await _table(file))


@router.post("/import")
async def import_history(file: UploadFile = File(...), mapping: str = Form(...), db: Session = Depends(get_db)) -> dict:
    """Import the CSV with the household's mapping, replacing any earlier history import."""
    try:
        m = MappingIn(**json.loads(mapping))
    except (json.JSONDecodeError, TypeError, ValidationError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bad mapping: {e}")
    if m.spending_is not in ("positive", "negative"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "spending_is must be positive or negative")
    table = await _table(file)
    try:
        rows, skipped = history.build_rows(table, history.Mapping(**m.model_dump()))
    except HistoryImportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "No rows could be read with this mapping; check the date format and columns")
    return {**history.import_rows(db, rows, m.add_as_spending), "skipped": skipped}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_history(db: Session = Depends(get_db)) -> None:
    """Undo the history import: its examples, rules and the spending it added."""
    history.clear(db)
    db.commit()
