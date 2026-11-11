"""Push notifications for subscriptions: when a new one starts, and a week before each renewal (time to cancel)."""
import logging
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.money import money
from app.db.base import SessionLocal
from app.db.models import RecurringExpense
from app.services.push import send_to_devices
from app.services.categories import categories_with
from app.services.recurring import next_due, occurrences

log = logging.getLogger(__name__)

NOTICE_DAYS = 7
# Reminders go out in waking hours only (local time).
FIRST_HOUR, LAST_HOUR = 9, 21
CHECK_EVERY_SECONDS = 30 * 60


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def announce_new_subscription(db: Session, item: RecurringExpense) -> None:
    due = next_due(item, item.anchor_date)
    send_to_devices(
        db, f"New subscription: {item.name}",
        f"{money(item.amount)} a month, next on {_day(due)}. It's in Bills; you'll get a reminder a week before.",
        {"event": "NEW_SUBSCRIPTION", "alert_id": f"sub:{item.id}", "recurring_id": str(item.id)},
    )


def send_renewal_reminders(db: Session, today: date) -> list[RecurringExpense]:
    """Remind once about each subscription renewing within NOTICE_DAYS that isn't paid yet."""
    reminded = []
    subscriptions = set(categories_with(db, "subscriptions"))
    for o in occurrences(db, today, today + timedelta(days=NOTICE_DAYS + 1), today):
        item = o.item
        if o.status != "upcoming" or item.category not in subscriptions or item.reminded_due == o.due:
            continue
        when = "today" if o.due == today else "tomorrow" if o.due == today + timedelta(days=1) else f"on {_day(o.due)}"
        send_to_devices(
            db, f"{item.name} renews {when}",
            f"{money(item.amount)}. Cancel before then if you don't want it.",
            {"event": "SUBSCRIPTION_RENEWAL", "alert_id": f"renewal:{item.id}:{o.due}", "recurring_id": str(item.id),
             "due": o.due.isoformat()},
        )
        item.reminded_due = o.due
        db.commit()
        reminded.append(item)
        log.info("Renewal reminder: %s due %s", item.name, o.due)
    return reminded


def _loop(stop: threading.Event) -> None:
    tz = ZoneInfo(get_settings().timezone)
    while not stop.is_set():
        now = datetime.now(tz)
        if FIRST_HOUR <= now.hour < LAST_HOUR:
            try:
                with SessionLocal() as db:
                    send_renewal_reminders(db, now.date())
            except Exception:
                log.exception("Renewal reminders failed")
        stop.wait(CHECK_EVERY_SECONDS)


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="renewal-reminders", daemon=True).start()
    return stop
