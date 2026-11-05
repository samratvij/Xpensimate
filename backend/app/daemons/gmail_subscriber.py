"""Gmail ingestion daemon: Pub/Sub streaming pull -> Gmail history -> parsed bank alerts.

Run as the `gmail-worker` compose service (profile "gmail"). Outbound connections only.
"""
import logging
import signal
import threading
import time

from google.cloud import pubsub_v1

from app.core.config import get_settings
from app.daemons.gmail_client import build_gmail, load_credentials
from app.daemons.gmail_sync import POLL_SECONDS, backfill, process_history, record_error, start_watch
from app.db.base import SessionLocal

log = logging.getLogger("gmail_subscriber")

# Gmail watches expire after 7 days; renew daily.
WATCH_RENEWAL_SECONDS = 24 * 60 * 60
# Pause before nacking so a persistent failure (e.g. DB down) doesn't spin.
FAILURE_BACKOFF_SECONDS = 30


def _record_error(email: str, error: BaseException) -> None:
    # A fresh session: the failing one may be mid-transaction. Best effort; the DB itself may be the problem.
    try:
        with SessionLocal() as db:
            record_error(db, email, error)
    except Exception:
        log.exception("Could not record the error for the health screen")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.gcp_project_id:
        raise SystemExit("GCP_PROJECT_ID is not set in .env (docs/gmail-setup.md)")

    creds = load_credentials()
    gmail = build_gmail(creds)
    email = gmail.users().getProfile(userId="me").execute()["emailAddress"]
    topic = f"projects/{settings.gcp_project_id}/topics/{settings.pubsub_topic}"
    subscription = f"projects/{settings.gcp_project_id}/subscriptions/{settings.pubsub_subscription}"
    # The Gmail client isn't thread-safe: one lock serializes Pub/Sub callbacks and watch renewal.
    lock = threading.Lock()
    stop = threading.Event()

    with lock, SessionLocal() as db:
        if start_watch(db, gmail, email, topic):
            log.info("First run for %s; backfilling the last day: %s", email, backfill(db, gmail, "1d"))
        log.info("Catch-up since last run: %s", process_history(db, gmail, email))

    def on_message(message: pubsub_v1.subscriber.message.Message) -> None:
        with lock, SessionLocal() as db:
            try:
                counts = process_history(db, gmail, email)
            except Exception as e:
                log.exception("Processing Gmail history failed; will retry")
                _record_error(email, e)
                time.sleep(FAILURE_BACKOFF_SECONDS)
                message.nack()
                return
        if counts:
            log.info("Processed: %s", counts)
        message.ack()

    def renew_watch() -> None:
        while not stop.wait(WATCH_RENEWAL_SECONDS):
            try:
                with lock, SessionLocal() as db:
                    start_watch(db, gmail, email, topic)
            except Exception as e:
                log.exception("Watch renewal failed; retrying next cycle")
                _record_error(email, e)

    def poll_history() -> None:
        while not stop.wait(POLL_SECONDS):
            try:
                with lock, SessionLocal() as db:
                    counts = process_history(db, gmail, email)
                if counts:
                    log.info("Poll picked up: %s", counts)
            except Exception as e:
                log.exception("Polling Gmail history failed; retrying next cycle")
                _record_error(email, e)

    threading.Thread(target=renew_watch, name="watch-renewal", daemon=True).start()
    threading.Thread(target=poll_history, name="history-poll", daemon=True).start()

    subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    future = subscriber.subscribe(
        subscription, callback=on_message, flow_control=pubsub_v1.types.FlowControl(max_messages=1)
    )

    def shutdown(*_):
        stop.set()
        future.cancel()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("Listening on %s for %s", subscription, email)
    with subscriber:
        try:
            future.result()
        except Exception:
            if not stop.is_set():
                raise
    log.info("Stopped")


if __name__ == "__main__":
    main()
