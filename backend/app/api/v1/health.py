"""Health checks for the app's Settings screen: is each moving part of the backend working?

Each check is a name, a status (ok | warning | error | off) and a sentence, so new checks show up in the
app without an app update.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.notifications import ANDROID_PUSH, PHONE_NOTIFICATION
from app.core.config import get_settings
from app.daemons.gmail_sync import GMAIL_SOURCE, POLL_SECONDS
from app.db.base import get_db
from app.db.models import Device, GmailSyncState, RawIngestLog, Transaction
from app.schemas import HealthCheck, HealthOut

router = APIRouter(tags=["health"])

# Three missed polls before calling the Gmail worker stopped.
GMAIL_STALE_AFTER = timedelta(seconds=POLL_SECONDS * 3)
UNPARSED_WINDOW = timedelta(days=7)


def ago(when: datetime, now: datetime) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def _last_transaction(db: Session, source: str) -> datetime | None:
    return db.scalar(select(func.max(Transaction.created_at)).where(Transaction.source == source))


def database_check(db: Session) -> HealthCheck:
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        return HealthCheck(name="Database", status="error", detail=f"Not answering: {type(e).__name__}")
    return HealthCheck(name="Database", status="ok", detail="Connected.")


def gmail_check(db: Session, now: datetime) -> HealthCheck:
    name = "Gmail bank alerts"
    if not get_settings().gcp_project_id:
        return HealthCheck(name=name, status="off", detail="Not set up on the server (docs/gmail-setup.md).")
    state = db.scalars(select(GmailSyncState).order_by(GmailSyncState.updated_at.desc())).first()
    if state is None:
        return HealthCheck(name=name, status="error", detail="The Gmail worker has never run.")

    last_txn = _last_transaction(db, GMAIL_SOURCE)
    tail = f" Last transaction from email {ago(last_txn, now)}." if last_txn else " No transactions from email yet."
    checked = state.last_checked_at
    if state.last_error_at and (checked is None or state.last_error_at > checked):
        return HealthCheck(
            name=name, status="error", detail=f"Last check failed {ago(state.last_error_at, now)}: {state.last_error}"
        )
    if checked is None:
        return HealthCheck(name=name, status="warning", detail=f"Watching {state.email}, but no check recorded yet.")
    if now - checked > GMAIL_STALE_AFTER:
        return HealthCheck(
            name=name, status="error",
            detail=f"Last checked {state.email} {ago(checked, now)}. The Gmail worker may have stopped.{tail}",
        )
    if state.watch_expires_at and state.watch_expires_at < now:
        return HealthCheck(
            name=name, status="warning",
            detail=f"Push from Gmail expired {ago(state.watch_expires_at, now)}; new alerts arrive by polling "
                   f"(up to {POLL_SECONDS // 60} min late).{tail}",
        )
    return HealthCheck(name=name, status="ok", detail=f"Checked {state.email} {ago(checked, now)}.{tail}")


def parsing_check(db: Session, now: datetime) -> HealthCheck:
    name = "Reading bank emails"
    unread = db.scalar(
        select(func.count()).select_from(RawIngestLog).where(
            RawIngestLog.source == GMAIL_SOURCE,
            RawIngestLog.payload["parse_status"].astext.in_(("unparsed", "unknown_card")),
            RawIngestLog.created_at > now - UNPARSED_WINDOW,
            # A later backfill may have read it after a parser or card fix.
            ~select(Transaction.id).where(
                Transaction.source == GMAIL_SOURCE, Transaction.source_ref == RawIngestLog.payload["gmail_id"].astext
            ).exists(),
        )
    )
    if unread:
        return HealthCheck(
            name=name, status="warning",
            detail=f"{unread} bank email(s) in the last 7 days couldn't be turned into a transaction "
                   "(new wording or an unknown card).",
        )
    return HealthCheck(name=name, status="ok", detail="Every bank alert in the last 7 days was read.")


def phone_capture_check(db: Session, now: datetime) -> HealthCheck:
    name = "Phone notification capture"
    # Purchases become android_push transactions; money-looking text that isn't a purchase (e.g. Wealthsimple's
    # crypto price alerts) is logged as android_notification. Promos are dropped and leave no trace.
    heard = db.scalar(
        select(func.max(RawIngestLog.created_at)).where(RawIngestLog.source.in_((ANDROID_PUSH, PHONE_NOTIFICATION)))
    )
    if heard is None:
        return HealthCheck(name=name, status="off", detail="Nothing received from a phone yet.")
    last = db.scalars(
        select(Transaction).where(Transaction.source == ANDROID_PUSH).order_by(Transaction.created_at.desc())
    ).first()
    purchase = (f"Last purchase captured {ago(last.created_at, now)} ({last.merchant_raw})." if last
                else "No purchase captured yet.")
    return HealthCheck(name=name, status="ok", detail=f"{purchase} Last heard from a phone {ago(heard, now)}.")


def ai_check() -> HealthCheck:
    settings = get_settings()
    if not settings.ai_enabled():
        return HealthCheck(
            name="AI model", status="warning",
            detail="No Claude API key or model server: categorizing uses rules only and receipt photos can't be read.",
        )
    where = "Claude" if settings.llm_provider == "anthropic" else f"Model server {settings.llm_base_url}"
    return HealthCheck(
        name="AI model", status="ok",
        detail=f"{where}. Categorizing with {settings.categorizer()}, receipts with {settings.receipt_reader()}.",
    )


def push_check(db: Session) -> HealthCheck:
    name = "Push notifications"
    if not Path(get_settings().fcm_credentials).is_file():
        return HealthCheck(name=name, status="off", detail="No Firebase key on the server, so budget alerts aren't sent.")
    devices = db.scalar(select(func.count()).select_from(Device))
    if not devices:
        return HealthCheck(name=name, status="warning", detail="Firebase is set up, but no phone is registered.")
    return HealthCheck(name=name, status="ok", detail=f"{devices} phone(s) registered for budget alerts.")


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    now = datetime.now(timezone.utc)
    database = database_check(db)
    if database.status != "ok":
        return HealthOut(checked_at=now, checks=[database, ai_check()])
    return HealthOut(
        checked_at=now,
        checks=[
            database,
            gmail_check(db, now),
            parsing_check(db, now),
            phone_capture_check(db, now),
            ai_check(),
            push_check(db),
        ],
    )
