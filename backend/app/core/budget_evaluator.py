"""Budget threshold alerts (PRD 6.4), delivered as Android push notifications.

After anything that changes spending, the affected budget lines are re-totalled for the month. A line at
or above its warning threshold gets a WARNING alert; one over its limit gets EXCEEDED. Each fires at most
once per line per month (enforced by a unique key), and only for the current month, so importing old
transactions never pages anyone.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import money
from app.core.config import get_settings
from app.core.taxonomy import budget_line
from app.db.models import BudgetAlert, BudgetEnvelope, Transaction
from app.services.merchant import merchant_key
from app.services.push import send_to_devices
from app.services.spending import month_bounds, month_of, spend_by

log = logging.getLogger(__name__)

WARNING = "WARNING"
EXCEEDED = "EXCEEDED"


def lines_for(txn: Transaction) -> set[str]:
    if txn.is_itemized and txn.items:
        return {budget_line(i.category, i.purpose) for i in txn.items}
    return {budget_line(txn.category, txn.purpose)}


def evaluate(
    db: Session, lines: set[str], *, when: datetime | None = None, trigger: Transaction | None = None
) -> list[BudgetAlert]:
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(timezone.utc)
    month = month_of(when or now, tz)
    if month != month_of(now, tz):
        return []
    budgets = {
        b.category: b for b in db.scalars(select(BudgetEnvelope).where(BudgetEnvelope.category.in_(lines)))
    }
    if not budgets:
        return []

    spent = spend_by(db, *month_bounds(month, tz), None, "budget_line")
    fired = {
        (a.budget_line, a.level)
        for a in db.scalars(select(BudgetAlert).where(BudgetAlert.month == month, BudgetAlert.budget_line.in_(budgets)))
    }
    alerts = []
    for line, budget in budgets.items():
        amount = spent.get(line, Decimal("0"))
        if amount > budget.monthly_limit:
            level = EXCEEDED
        elif amount >= budget.monthly_limit * budget.warning_threshold_percent / 100:
            level = WARNING
        else:
            continue
        # A warning is moot once the line is already over.
        if (line, level) in fired or (line, EXCEEDED) in fired:
            continue
        alerts.append(
            BudgetAlert(
                month=month, budget_line=line, level=level, monthly_limit=budget.monthly_limit, spent=amount,
                transaction_id=trigger.id if trigger else None,
            )
        )
    if not alerts:
        return []
    db.add_all(alerts)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent evaluation already recorded these.
        db.rollback()
        return []
    for alert in alerts:
        notify(db, alert, trigger)
    return alerts


def check_transaction(db: Session, txn: Transaction) -> list[BudgetAlert]:
    return evaluate(db, lines_for(txn), when=txn.timestamp, trigger=txn)


def describe(alert: BudgetAlert, trigger: Transaction | None) -> tuple[str, str]:
    month_name = datetime.strptime(alert.month, "%Y-%m").strftime("%B")
    if alert.level == EXCEEDED:
        title = f"Over budget: {alert.budget_line}"
    else:
        percent = round(alert.spent / alert.monthly_limit * 100)
        title = f"{alert.budget_line} at {percent}% of budget"
    body = f"{money(alert.spent)} of {money(alert.monthly_limit)} spent in {month_name}"
    if trigger is not None:
        body += f" · {merchant_key(trigger.merchant_raw)} {money(trigger.amount)}"
    return title, body


def notify(db: Session, alert: BudgetAlert, trigger: Transaction | None) -> None:
    title, body = describe(alert, trigger)
    data = {
        "event": f"BUDGET_{alert.level}",
        "alert_id": str(alert.id),
        "budget_line": alert.budget_line,
        "month": alert.month,
        "spent": str(alert.spent),
        "monthly_limit": str(alert.monthly_limit),
        "transaction_id": str(trigger.id) if trigger else "",
    }
    if send_to_devices(db, title, body, data):
        alert.sent_at = datetime.now(timezone.utc)
        db.commit()
    log.info("Budget alert %s %s: %s", alert.level, alert.budget_line, body)
