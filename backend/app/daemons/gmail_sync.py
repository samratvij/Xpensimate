import logging
from datetime import datetime, timezone

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.daemons.gmail_client import header, message_text
from app.daemons.parsers import gmail_query, issuer_for_sender
from app.db.models import GmailSyncState, RawIngestLog
from app.services.accounts import account_for_alert
from app.services.ingest import NewTransaction, find_by_source_ref, ingest_transaction

log = logging.getLogger(__name__)

GMAIL_SOURCE = "gmail_pubsub"
SKIP_LABELS = {"DRAFT", "SENT", "SPAM", "TRASH"}
MAX_BODY_CHARS = 20_000
# Safety net: the Pub/Sub stream can die silently (server closes it, client never notices), so also
# poll Gmail history on a timer. process_history is a no-op when nothing new has arrived.
POLL_SECONDS = 10 * 60


def process_message(db: Session, gmail, message_id: str) -> str:
    """Fetch one message and ingest it if it is a bank alert. Returns created|duplicate|ignored|unparsed|gone."""
    if find_by_source_ref(db, GMAIL_SOURCE, message_id):
        return "duplicate"
    try:
        message = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    except HttpError as e:
        if e.resp.status == 404:
            # Deleted (or a draft that was replaced) between the notification and now: nothing to read.
            return "gone"
        raise
    if SKIP_LABELS & set(message.get("labelIds", [])):
        return "ignored"
    sender = header(message, "From")
    issuer = issuer_for_sender(sender)
    subject = header(message, "Subject")
    # Statements, offers and one-time codes are never stored.
    if issuer is None or not issuer.is_transaction(subject):
        return "ignored"

    body = message_text(message.get("payload", {}))
    received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc)
    payload = {
        "gmail_id": message_id,
        "issuer": issuer.NAME,
        "from": sender,
        "subject": subject,
        "received_at": received.isoformat(),
        "body": body[:MAX_BODY_CHARS],
    }

    parsed = issuer.parse(subject, body)
    # The card's last four digits pick the account; the parser's account is the fallback.
    account_id = parsed and account_for_alert(db, parsed.card_suffix, parsed.account_id)
    if parsed is None or not account_id:
        # Keep the email so the parser (or the account's card_last4) can be fixed, then a backfill retries it.
        status = "unparsed" if parsed is None else "unknown_card"
        db.add(RawIngestLog(source=GMAIL_SOURCE, payload={**payload, "parse_status": status}))
        db.commit()
        log.warning("Could not ingest %s alert %s (%r): %s", issuer.NAME, message_id, subject, status)
        return "unparsed"

    payload["parsed"] = {
        "amount": str(parsed.amount),
        "merchant": parsed.merchant,
        "account_id": account_id,
        "card_suffix": parsed.card_suffix,
    }
    _, created = ingest_transaction(
        db,
        NewTransaction(
            source=GMAIL_SOURCE,
            source_ref=message_id,
            account_id=account_id,
            timestamp=received,
            amount=parsed.amount,
            merchant=parsed.merchant,
            reconciliation_status="PENDING_MATCH",
        ),
        raw_payload=payload,
    )
    return "created" if created else "duplicate"


def backfill(db: Session, gmail, newer_than: str = "7d") -> dict[str, int]:
    """Search-based catch-up, used on first start and when Gmail history has expired."""
    counts: dict[str, int] = {}
    page_token = None
    while True:
        resp = gmail.users().messages().list(userId="me", q=gmail_query(newer_than), pageToken=page_token).execute()
        for ref in resp.get("messages", []):
            outcome = process_message(db, gmail, ref["id"])
            counts[outcome] = counts.get(outcome, 0) + 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            return counts


def _set_history_id(db: Session, email: str, history_id: int) -> None:
    state = db.get(GmailSyncState, email)
    if state is None:
        db.add(GmailSyncState(email=email, history_id=history_id))
    else:
        state.history_id = max(state.history_id, history_id)
    state.last_checked_at = datetime.now(timezone.utc)
    db.commit()


def record_error(db: Session, email: str, error: BaseException) -> None:
    """Remember the worker's latest failure so the app's health screen can show it."""
    state = db.get(GmailSyncState, email)
    if state is not None:
        state.last_error = f"{type(error).__name__}: {error}"[:500]
        state.last_error_at = datetime.now(timezone.utc)
        db.commit()


def process_history(db: Session, gmail, email: str) -> dict[str, int]:
    """Ingest every message added since the stored historyId, then advance it."""
    state = db.get(GmailSyncState, email)
    if state is None:
        raise RuntimeError(f"No Gmail sync state for {email}; start_watch() first")

    message_ids: list[str] = []
    latest = state.history_id
    page_token = None
    try:
        while True:
            resp = (
                gmail.users()
                .history()
                .list(userId="me", startHistoryId=state.history_id, historyTypes="messageAdded", pageToken=page_token)
                .execute()
            )
            for entry in resp.get("history", []):
                for added in entry.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])
            latest = max(latest, int(resp.get("historyId", latest)))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        if e.resp.status != 404:
            raise
        # startHistoryId is too old (Gmail keeps roughly a week): fall back to a search.
        log.warning("Gmail history %s expired; backfilling by search", state.history_id)
        counts = backfill(db, gmail)
        _set_history_id(db, email, int(gmail.users().getProfile(userId="me").execute()["historyId"]))
        return counts

    counts: dict[str, int] = {}
    for message_id in dict.fromkeys(message_ids):
        outcome = process_message(db, gmail, message_id)
        counts[outcome] = counts.get(outcome, 0) + 1
    _set_history_id(db, email, latest)
    return counts


def start_watch(db: Session, gmail, email: str, topic: str) -> bool:
    """(Re)register the Gmail push watch. Returns True when this mailbox had no sync state yet."""
    resp = gmail.users().watch(
        userId="me", body={"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "include"}
    ).execute()
    expires = datetime.fromtimestamp(int(resp["expiration"]) / 1000, tz=timezone.utc)
    state = db.get(GmailSyncState, email)
    first_run = state is None
    if first_run:
        state = GmailSyncState(email=email, history_id=int(resp["historyId"]))
        db.add(state)
    state.watch_expires_at = expires
    db.commit()
    log.info("Gmail watch on %s active until %s", topic, expires.isoformat())
    return first_run
