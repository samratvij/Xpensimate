"""The savings target and goals.

Target: an amount to move into savings and investments each month (the household's, "household"). Progress is
what was actually moved (app/services/savings.py, transfers between own accounts), with income less spending shown
next to it. It works like a budget in reverse: on or after the target's alert day, an alert fires once that month
if less than warning_percent of the target has been moved ("BEHIND"), and once a month has ended short of the
target, a "SHORT" note for it. Alerts are stored with the budget alerts (line "Savings target") and pushed.

Goal: an amount to have by a date, counted from the latest balances of the accounts it covers (app/services/
balances.py). It shows what is there, what's left, and the monthly amount that would reach it: facts, not advice.
"""
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import money
from app.core.config import get_settings
from app.db.base import SessionLocal
from app.db.models import Account, BudgetAlert, SavingsGoal, SavingsTarget, Transaction
from app.services import balances, income, savings
from app.services.push import send_to_devices
from app.services.spending import month_bounds, spend_by

log = logging.getLogger(__name__)

HOUSEHOLD = "household"
TARGET_LINE = "Savings target"
BEHIND, SHORT = "BEHIND", "SHORT"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# --- the monthly target ---------------------------------------------------------------------------

@dataclass
class TargetProgress:
    month: str
    target: Decimal
    # Moved into savings and investments (net), and income less spending, this month.
    saved: Decimal
    kept: Decimal
    percent: float
    remaining: Decimal
    # met | on_track (before the alert day, or at least warning_percent) | behind
    status: str


def target_progress(db: Session, month: str, today: date) -> TargetProgress | None:
    target = db.get(SavingsTarget, HOUSEHOLD)
    if target is None or target.monthly_amount <= 0:
        return None
    start, end = month_bounds(month, _tz())
    got = sum(income.income_by(db, start, end).values(), ZERO)
    spent = sum(spend_by(db, start, end, None, "budget_line").values(), ZERO)
    flows = savings.totals(db, start, end, got, spent)
    saved = flows["moved"]
    percent = round(float(saved / target.monthly_amount * 100), 1)
    finished = end.date() <= today
    if saved >= target.monthly_amount:
        status = "met"
    elif finished or (today.day >= target.alert_day and percent < float(target.warning_percent)):
        status = "behind"
    else:
        status = "on_track"
    return TargetProgress(month, target.monthly_amount, saved, flows["kept"], percent,
                          max(ZERO, target.monthly_amount - saved), status)


def _describe(level: str, p: TargetProgress) -> tuple[str, str]:
    month_name = datetime.strptime(p.month, "%Y-%m").strftime("%B")
    moved = f"{money(p.saved)} moved to savings and investments in {month_name}"
    if level == BEHIND:
        return (f"Behind on savings: {round(p.percent)}% of {money(p.target, whole_from=Decimal(0))}",
                f"{moved}; {money(p.remaining)} to go to reach the target.")
    return f"{month_name} savings finished short", f"{moved}, of the {money(p.target, whole_from=Decimal(0))} target."


def _fire(db: Session, level: str, p: TargetProgress) -> BudgetAlert | None:
    alert = BudgetAlert(month=p.month, budget_line=TARGET_LINE, level=level, monthly_limit=p.target, spent=p.saved)
    db.add(alert)
    try:
        db.commit()
    except IntegrityError:  # already sent this month
        db.rollback()
        return None
    title, body = _describe(level, p)
    data = {"event": f"SAVINGS_{level}", "alert_id": str(alert.id), "budget_line": TARGET_LINE, "month": p.month,
            "spent": str(p.saved), "monthly_limit": str(p.target)}
    if send_to_devices(db, title, body, data):
        alert.sent_at = datetime.now(timezone.utc)
        db.commit()
    log.info("Savings alert %s %s: %s", level, p.month, body)
    return alert


def check_target(db: Session, today: date) -> list[BudgetAlert]:
    """Fire this month's "behind" alert and last month's "finished short" note, each at most once."""
    target = db.get(SavingsTarget, HOUSEHOLD)
    if target is None or target.monthly_amount <= 0:
        return []
    fired = []
    this_month = target_progress(db, _month(today), today)
    if this_month and this_month.status == "behind" and today.day >= target.alert_day:
        fired.append(_fire(db, BEHIND, this_month))
    last = _month(today.replace(day=1) - timedelta(days=1))
    # Only months the target was already set for (no note for months before it existed).
    last_end = datetime.combine(today.replace(day=1), time.min, tzinfo=_tz())
    if target.created_at < last_end:
        previous = target_progress(db, last, today)
        if previous and previous.status == "behind":
            fired.append(_fire(db, SHORT, previous))
    return [a for a in fired if a]


# --- goals ----------------------------------------------------------------------------------------

@dataclass
class GoalProgress:
    goal: SavingsGoal
    current: Decimal
    remaining: Decimal
    percent: float
    # Month-ends left until the target date, counting this month's (0 once past it).
    months_left: int
    # What would reach the goal in the months left (None once reached or past the date).
    required_monthly: Decimal | None
    # Moved into the goal's accounts this month (net), for comparison.
    moved_this_month: Decimal
    accounts: list[tuple[Account, Decimal | None, date | None]]
    # The oldest balance counted, when some are from an earlier day.
    oldest_balance: date | None


def goal_accounts(db: Session, goal: SavingsGoal) -> list[Account]:
    return list(db.scalars(select(Account).where(
        or_(Account.kind.in_(goal.account_kinds or []), Account.id.in_(goal.account_ids or []))
    ).order_by(Account.account_name)))


def goal_progress(db: Session, goal: SavingsGoal, today: date) -> GoalProgress:
    accounts = goal_accounts(db, goal)
    latest = balances.latest_on_or_before(db, today)
    rows, current, dates = [], ZERO, []
    for account in accounts:
        snap = latest.get(account.id)
        rows.append((account, snap.balance if snap else None, snap.as_of if snap else None))
        if snap:
            current += snap.balance
            dates.append(snap.as_of)
    remaining = max(ZERO, goal.target_amount - current)
    months_left = max(0, (goal.target_date.year - today.year) * 12 + goal.target_date.month - today.month + 1)
    if today > goal.target_date:
        months_left = 0
    required = (remaining / months_left).quantize(CENT, ROUND_HALF_UP) if remaining > 0 and months_left else None
    start, end = month_bounds(_month(today), _tz())
    ids = [a.id for a in accounts]
    moved = ZERO
    for txn in db.scalars(select(Transaction).where(
        Transaction.txn_type == savings.TRANSFER, Transaction.timestamp >= start, Transaction.timestamp < end,
        or_(Transaction.account_id.in_(ids), Transaction.counterpart_account_id.in_(ids)),
    )):
        into, out_of = txn.counterpart_account_id in ids, txn.account_id in ids
        if into and not out_of:
            moved += txn.amount
        elif out_of and not into:
            moved -= txn.amount
    percent = round(float(current / goal.target_amount * 100), 1) if goal.target_amount > 0 else 0.0
    return GoalProgress(goal, current, remaining, percent, months_left, required, moved, rows,
                        min(dates) if dates and min(dates) != max(dates) else None)


# --- background check -----------------------------------------------------------------------------

CHECK_EVERY_SECONDS = 60 * 60
# Alerts go out in waking hours only (local time).
FIRST_HOUR, LAST_HOUR = 9, 21


def _loop(stop: threading.Event) -> None:
    while not stop.is_set():
        now = datetime.now(_tz())
        if FIRST_HOUR <= now.hour < LAST_HOUR:
            try:
                with SessionLocal() as db:
                    check_target(db, now.date())
            except Exception:
                log.exception("Savings target check failed")
        stop.wait(CHECK_EVERY_SECONDS)


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="savings-target", daemon=True).start()
    return stop
