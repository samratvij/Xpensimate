import re
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.taxonomy import people
from app.core.config import get_settings
from app.core.security import require_token
from app.db.base import get_db
from app.db.models import Account, BalanceSnapshot
from app.schemas import (
    AccountBalanceOut,
    BalanceBatchIn,
    BalanceIn,
    BalanceReadOut,
    BalanceReadRow,
    BalancesOut,
    BalanceSnapshotOut,
)
from app.services import balances
from app.services.notes import clean_note
from app.services.ocr_service import OcrError
from app.statements.balance_screenshot import UNKNOWN_ACCOUNT, parse_balance_screenshot

router = APIRouter(prefix="/balances", tags=["balances"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def _account(db: Session, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown account {account_id!r}")
    return account


@router.get("", response_model=BalancesOut)
def month_balances(month: str | None = None, db: Session = Depends(get_db)) -> BalancesOut:
    """Every account's balance at the end of a month (the latest one recorded by then), net worth, and for savings
    and investments how much of the change was contributed vs earned."""
    month = month or _today().strftime("%Y-%m")
    if not re.match(MONTH, month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "month must be YYYY-MM")
    m = balances.month_balances(db, month)
    return BalancesOut(
        month=m.month, as_of=m.as_of, assets=m.assets, liabilities=m.liabilities, net_worth=m.net_worth,
        previous_net_worth=m.previous_net_worth,
        change=m.net_worth - m.previous_net_worth if m.previous_net_worth is not None else None,
        groups=m.groups, contributions=m.contributions, market_return=m.market_return, missing=m.missing,
        accounts=[
            AccountBalanceOut(
                account_id=r.account.id, account_name=r.account.account_name, institution=r.account.institution,
                kind=r.account.kind, owner=r.account.owner, group=balances.group_of(r.account.kind),
                balance=r.balance, as_of=r.as_of, stale=r.stale, source=r.source,
                previous_balance=r.previous_balance, previous_as_of=r.previous_as_of, change=r.change,
                contributions=r.contributions, market_return=r.market_return,
            )
            for r in m.accounts
        ],
    )


@router.put("/{account_id}", response_model=BalanceSnapshotOut)
def set_balance(account_id: str, body: BalanceIn, db: Session = Depends(get_db),
                user: str = Depends(require_token)) -> BalanceSnapshot:
    """Record an account's balance on a day (typed in); replaces one already recorded for that day."""
    _account(db, account_id)
    if body.as_of > _today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A balance can't be dated in the future")
    snap = balances.save_snapshot(db, account_id, body.as_of, body.balance, "manual", clean_note(body.note) or "", user)
    db.commit()
    db.refresh(snap)
    return snap


@router.get("/{account_id}/history", response_model=list[BalanceSnapshotOut])
def history(account_id: str, db: Session = Depends(get_db)) -> list[BalanceSnapshot]:
    _account(db, account_id)
    return list(db.scalars(select(BalanceSnapshot).where(BalanceSnapshot.account_id == account_id)
                           .order_by(BalanceSnapshot.as_of.desc())))


@router.delete("/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snapshot(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    snap = db.get(BalanceSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Balance not found")
    db.delete(snap)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/read", response_model=BalanceReadOut)
def read_screenshot(
    file: UploadFile = File(...),
    owner: str | None = Form(None, description="Whose phone the screenshot is from (one of the household's people)"),
    as_of: date | None = Form(None),
    db: Session = Depends(get_db),
) -> BalanceReadOut:
    """Read the balances on a screenshot of a bank app and match them to accounts. Nothing is saved: confirm with
    POST /balances/batch."""
    if not get_settings().ai_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Reading screenshots needs ANTHROPIC_API_KEY, or LLM_PROVIDER=openai and a model server")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is larger than 15 MB")
    accounts = list(db.scalars(select(Account).order_by(Account.id)))
    try:
        read = parse_balance_screenshot(content, accounts, owner if owner in people() else None)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    except OcrError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not read the screenshot: {e}")
    names = {a.id: a.account_name for a in accounts}
    by_id = {a.id: a for a in accounts}
    rows = []
    for row in read.rows:
        # Only balances in the account's own currency count (no conversion).
        account = by_id.get(row.account_id)
        account_id = account.id if account and row.currency.upper() == account.currency else None
        rows.append(BalanceReadRow(shown_name=row.shown_name, balance=round(abs(row.balance), 2), owed=row.owed,
                                   currency=row.currency.upper(), account_id=account_id,
                                   account_name=names.get(account_id)))
    return BalanceReadOut(as_of=as_of or _today(), rows=rows)


@router.post("/batch", response_model=list[BalanceSnapshotOut], status_code=status.HTTP_201_CREATED)
def save_batch(body: BalanceBatchIn, db: Session = Depends(get_db), user: str = Depends(require_token)) -> list[BalanceSnapshot]:
    """Record several balances on one day (what a screenshot showed, once confirmed)."""
    if body.as_of > _today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A balance can't be dated in the future")
    ids = [r.account_id for r in body.rows]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Each account can appear once")
    for account_id in ids:
        _account(db, account_id)
    snaps = [balances.save_snapshot(db, r.account_id, body.as_of, r.balance, body.source, None, user) for r in body.rows]
    db.commit()
    for snap in snaps:
        db.refresh(snap)
    return snaps
