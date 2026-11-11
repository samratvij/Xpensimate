"""Expense insights: the few things on the Budget screen worth acting on this month.

Each detector looks for one kind of signal and returns Insights with a dollar impact (per month); the
endpoint ranks them by severity, then impact, and leaves out dismissed ones. Detection is all rules, no
LLM, so every number in a card is exact. Thresholds live here, so tuning them needs no app release.

A dismissal is by key. Keys that include the month (or a transaction) come back next time there's
something new; keys without one ("new_sub:<name>") stay dismissed.
"""
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import money as format_money
from app.core.taxonomy import budget_line
from app.db.models import BudgetEnvelope, InsightDismissal, RecurringExpense, Transaction, TransactionItem
from app.services.adjustments import suspected_holds
from app.services.merchant import matches_pattern, merchant_key, normalize_merchant
from app.services.recurring import detect, expected_spend, occurrences

ZERO = Decimal("0.00")
HISTORY_MONTHS = 3

# Pace: flag a line when the month is on course to finish this far over its limit, and this month is running
# ahead of the usual by at least PACE_MIN_AHEAD (a line that's always over budget isn't news every month).
PACE_MIN_OVER = Decimal("25")
PACE_MIN_OVER_SHARE = Decimal("0.05")
PACE_MIN_AHEAD = Decimal("25")
PACE_MIN_AHEAD_RATIO = Decimal("1.10")
# Over budget already: worth a card from this much over.
OVER_MIN = Decimal("10")
# Spike: spending so far this month vs. the same days of the last three months.
SPIKE_MIN_INCREASE = Decimal("50")
SPIKE_MIN_RATIO = Decimal("1.30")
DRIVERS = 2
# Subscriptions.
STILL_CHARGING_DAYS = 60
PRICE_STABLE_SHARE = Decimal("0.02")
PRICE_MIN_CHANGE = Decimal("1.00")
PRICE_MIN_CHANGE_SHARE = Decimal("0.03")
PRICE_RECENT_DAYS = 45
NEW_SUB_MAX_TOLERANCE = Decimal("0.15")
NEW_SUB_MIN_AMOUNT = Decimal("5")
# Bills.
UPCOMING_DAYS = 7
# Holds: how far back to look for a charge the bank may have replaced with the order's final amount.
HOLD_LOOKBACK_DAYS = 35

PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12}
PER_MONTH = {"weekly": Decimal("4.33"), "biweekly": Decimal("2.17"), "monthly": Decimal("1")}

Severity = Literal["alert", "warn", "info"]
SEVERITY_ORDER = {"alert": 0, "warn": 1, "info": 2}


@dataclass
class Insight:
    key: str
    # pace | spike | still_charging | price_change | new_subscription | overdue_bill | upcoming_bills | replaced_hold
    kind: str
    severity: Severity
    title: str
    body: str
    impact: Decimal
    action: str  # open_line | open_transaction | open_bills | track_recurring | remove_hold
    line: str | None = None
    transaction_id: Any = None
    recurring: dict[str, Any] | None = None


def money(amount: Decimal) -> str:
    return format_money(amount, whole_from=Decimal(100))


def _shift(first: date, months: int) -> date:
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


@dataclass
class Row:
    """One piece of spending on a budget line: a whole transaction, or one item of an itemized one."""

    line: str
    merchant: str
    amount: Decimal
    day: date
    txn: Transaction
    recurring: bool


@dataclass
class Context:
    db: Session
    tz: ZoneInfo
    today: date
    purpose: str | None
    month_start: date
    month_end: date
    rows: list[Row] = field(default_factory=list)  # this month and the HISTORY_MONTHS before it

    @property
    def elapsed(self) -> int:
        return self.today.day

    @property
    def days_left(self) -> int:
        return (self.month_end - self.today).days - 1

    def this_month(self) -> list[Row]:
        return [r for r in self.rows if r.day >= self.month_start and r.day <= self.today]

    def history(self) -> list[list[Row]]:
        """The rows of each earlier month, most recent first; only months that have any spending."""
        months = []
        for k in range(1, HISTORY_MONTHS + 1):
            start, end = _shift(self.month_start, -k), _shift(self.month_start, -k + 1)
            rows = [r for r in self.rows if start <= r.day < end]
            if rows:
                months.append(rows)
        return months


def _is_recurring_charge(items: list[RecurringExpense], line: str, raw: str, amount: Decimal) -> bool:
    """Whether a charge looks like one of the active bills, linked or not (older charges predate linking)."""
    for item in items:
        if budget_line(item.category, item.purpose) != line:
            continue
        if item.merchant_pattern and not matches_pattern(item.merchant_pattern, merchant_key(raw), raw):
            continue
        if abs(amount - item.amount) <= item.amount * item.amount_tolerance:
            return True
    return False


def _load_rows(ctx: Context, items: list[RecurringExpense]) -> list[Row]:
    def at(d: date) -> datetime:
        return datetime.combine(d, datetime.min.time(), tzinfo=ctx.tz)

    window = (Transaction.timestamp >= at(_shift(ctx.month_start, -HISTORY_MONTHS)),
              Transaction.timestamp < at(ctx.month_end), Transaction.excluded.is_(False))
    rows = []

    def add(line: str, amount: Decimal, txn: Transaction) -> None:
        recurring = txn.recurring_id is not None or _is_recurring_charge(items, line, txn.merchant_raw, amount)
        rows.append(Row(line, normalize_merchant(txn.merchant_raw), amount, txn.timestamp.astimezone(ctx.tz).date(),
                        txn, recurring))

    whole = select(Transaction).where(*window, Transaction.is_itemized.is_(False))
    if ctx.purpose:
        whole = whole.where(Transaction.purpose == ctx.purpose)
    for txn in ctx.db.scalars(whole):
        add(budget_line(txn.category, txn.purpose), txn.amount, txn)

    parts = select(TransactionItem, Transaction).join(Transaction).where(*window, Transaction.is_itemized.is_(True))
    if ctx.purpose:
        parts = parts.where(TransactionItem.purpose == ctx.purpose)
    for item, txn in ctx.db.execute(parts):
        add(budget_line(item.category, item.purpose), item.price, txn)
    return rows


def _by_line(rows: list[Row]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        totals[r.line] += r.amount
    return totals


def _upto(rows: list[Row], day: int) -> list[Row]:
    return [r for r in rows if r.day.day <= day]


def _usual(months: list[dict[str, Decimal]], key: str) -> Decimal:
    """The median month, so one unusual month (a big one-off) doesn't set the baseline."""
    if not months:
        return ZERO
    return Decimal(statistics.median(m.get(key, ZERO) for m in months)).quantize(Decimal("0.01"))


# --- spending lines: pace and spikes ---------------------------------------------------------------

def _drivers(ctx: Context, line: str) -> list[tuple[str, Decimal, int]]:
    """The merchants behind a line running high: (merchant, spent so far, charges), biggest increase first."""
    now: dict[str, list[Decimal]] = defaultdict(list)
    for r in ctx.this_month():
        if r.line == line and not r.recurring and r.amount > 0:
            now[r.merchant].append(r.amount)
    before = []
    for rows in ctx.history():
        month: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r in _upto(rows, ctx.elapsed):
            if r.line == line and not r.recurring:
                month[r.merchant] += r.amount
        before.append(month)
    increases = sorted(((m, sum(a), len(a), sum(a) - _usual(before, m)) for m, a in now.items()), key=lambda x: -x[3])
    return [(m, spent, count) for m, spent, count, up in increases[:DRIVERS] if up > 0]


def _drivers_text(drivers: list[tuple[str, Decimal, int]]) -> str:
    parts = [f"{m} ({money(spent)}{f', {count} charges' if count > 1 else ''})" for m, spent, count in drivers]
    return "Mostly " + " and ".join(parts) + "." if parts else ""


def spending_lines(ctx: Context) -> list[Insight]:
    """Pace (heading over budget) and spikes (well above usual so far), one card per line at most."""
    history = ctx.history()
    if not history:
        return []
    this_rows = ctx.this_month()
    spent = _by_line(this_rows)
    variable_now = _by_line([r for r in this_rows if not r.recurring])
    variable_before = [_by_line([r for r in _upto(rows, ctx.elapsed) if not r.recurring]) for rows in history]
    rest_before = [_by_line([r for r in rows if r.day.day > ctx.elapsed and not r.recurring]) for rows in history]
    expected = expected_spend(ctx.db, ctx.month_start, ctx.month_end, ctx.today, "budget_line", ctx.purpose)
    budgets = {b.category: b.monthly_limit for b in ctx.db.scalars(select(BudgetEnvelope))}
    month_name = f"{ctx.today:%B}"

    out = []
    for line in sorted(spent.keys() | expected.keys()):
        so_far, due = spent.get(line, ZERO), expected.get(line, ZERO)
        usual_so_far = _usual(variable_before, line)
        now = variable_now.get(line, ZERO)
        increase = now - usual_so_far
        # Needs a baseline: lines that are usually empty by now (Travel, a bimonthly Hydro bill) aren't drifting.
        spiking = usual_so_far > 0 and increase >= SPIKE_MIN_INCREASE and now >= usual_so_far * SPIKE_MIN_RATIO
        ahead = increase >= PACE_MIN_AHEAD and now >= usual_so_far * PACE_MIN_AHEAD_RATIO
        limit = budgets.get(line)

        if limit is not None:
            # The rest of the month, if it goes like it usually does; bills still due are counted separately.
            projected = so_far + due + _usual(rest_before, line)
            over = projected - limit
            if so_far - limit >= OVER_MIN or (ahead and over >= max(PACE_MIN_OVER, limit * PACE_MIN_OVER_SHARE)):
                room = limit - so_far - due
                if so_far - limit >= OVER_MIN:
                    severity, title = "alert", f"{line} is {money(so_far - limit)} over budget"
                    advice = f"{money(so_far)} spent of {money(limit)}."
                    over = max(over, so_far - limit)
                else:
                    severity, title = "warn", f"{line} is heading for {money(projected)} of {money(limit)}"
                    if room > 0 and ctx.days_left > 0:
                        advice = (f"To stay within budget, keep to {money(room / ctx.days_left)}/day "
                                  f"for the {ctx.days_left} days left.")
                    elif due > 0:
                        advice = f"Bills still due take it past the limit ({money(due)} to come)."
                    else:
                        advice = f"Already {money(so_far)} spent; anything more goes over."
                drivers = _drivers_text(_drivers(ctx, line)) if spiking else ""
                out.append(Insight(
                    key=f"pace:{ctx.month_start:%Y-%m}:{line}", kind="pace", severity=severity, title=title,
                    body=" ".join(p for p in (advice, drivers) if p), impact=over, action="open_line", line=line,
                ))
                continue

        if spiking:
            out.append(Insight(
                key=f"spike:{ctx.month_start:%Y-%m}:{line}", kind="spike", severity="warn",
                title=f"{line} is {money(increase)} above usual so far",
                body=" ".join(p for p in (
                    f"{money(variable_now[line])} by {month_name} {ctx.elapsed}, against a usual {money(usual_so_far)}.",
                    _drivers_text(_drivers(ctx, line)),
                ) if p),
                impact=increase, action="open_line", line=line,
            ))
    return out


# --- subscriptions ---------------------------------------------------------------------------------

def _charges(ctx: Context, pattern: str, since: datetime) -> list[Transaction]:
    found = ctx.db.scalars(select(Transaction).where(
        Transaction.timestamp >= since, Transaction.excluded.is_(False), Transaction.amount > 0,
    ).order_by(Transaction.timestamp))
    return [t for t in found if matches_pattern(pattern, merchant_key(t.merchant_raw), t.merchant_raw)]


def _wants(ctx: Context, item: RecurringExpense) -> bool:
    return ctx.purpose in (None, item.purpose)


def subscriptions(ctx: Context) -> list[Insight]:
    """Stopped bills that still charge, price changes, and repeating charges that aren't tracked yet."""
    out = []
    items = list(ctx.db.scalars(select(RecurringExpense)))
    recent = datetime.combine(ctx.today - timedelta(days=STILL_CHARGING_DAYS), datetime.min.time(), tzinfo=ctx.tz)

    for item in items:
        if not item.merchant_pattern or not _wants(ctx, item):
            continue
        if not item.active:
            linked = ctx.db.scalar(select(Transaction.timestamp).where(Transaction.recurring_id == item.id)
                                   .order_by(Transaction.timestamp.desc()).limit(1))
            stopped = item.deactivated_at or linked or item.created_at
            for txn in _charges(ctx, item.merchant_pattern, max(stopped, recent)):
                if txn.timestamp <= stopped:
                    continue
                out.append(Insight(
                    key=f"still_charging:{txn.id}", kind="still_charging", severity="alert",
                    title=f"{item.name} charged {money(txn.amount)} after you stopped it",
                    body=f"On {_day(txn.timestamp.astimezone(ctx.tz).date())}. If you cancelled it, ask for a refund.",
                    impact=txn.amount, action="open_transaction", transaction_id=txn.id,
                ))
            continue

        # Price change: a fixed price that moved, seen in linked charges or a matching charge over tolerance.
        payments = list(ctx.db.scalars(select(Transaction).where(Transaction.recurring_id == item.id,
                                                                 Transaction.excluded.is_(False))
                                       .order_by(Transaction.timestamp.desc()).limit(4)))
        since = datetime.combine(ctx.today - timedelta(days=PRICE_RECENT_DAYS), datetime.min.time(), tzinfo=ctx.tz)
        latest = next((t for t in reversed(_charges(ctx, item.merchant_pattern, since))
                       if t.recurring_id in (None, item.id)), None)
        if latest is None:
            continue
        earlier = [t.amount for t in payments if t.id != latest.id][:3]
        if len(earlier) < 2:
            continue  # not enough payments to know its price
        usual = Decimal(statistics.median(earlier))
        if max(earlier) - min(earlier) > usual * PRICE_STABLE_SHARE:
            continue  # a varying bill (Fido, Hydro): no fixed price to compare with
        change = latest.amount - usual
        if abs(change) < max(PRICE_MIN_CHANGE, usual * PRICE_MIN_CHANGE_SHARE):
            continue
        yearly = change * PER_YEAR[item.cadence]
        went = "up" if change > 0 else "down"
        out.append(Insight(
            key=f"price:{item.id}:{latest.id}", kind="price_change", severity="warn" if change > 0 else "info",
            title=f"{item.name} went {went} to {money(latest.amount)}",
            body=f"Was {money(usual)}; that's {money(abs(yearly))} a year {'more' if change > 0 else 'less'}.",
            impact=change * PER_MONTH[item.cadence], action="open_transaction", transaction_id=latest.id,
        ))

    known_names = {i.name.lower() for i in items}
    patterns = [i.merchant_pattern for i in items if i.merchant_pattern]
    for d in detect(ctx.db, ctx.today):
        if d.name.lower() in known_names or any(matches_pattern(p, d.name) for p in patterns):
            continue
        if d.merchant_pattern is None or d.amount_tolerance > NEW_SUB_MAX_TOLERANCE or d.amount < NEW_SUB_MIN_AMOUNT:
            continue
        if ctx.purpose not in (None, d.purpose):
            continue
        monthly = (d.amount * PER_MONTH[d.cadence]).quantize(Decimal("0.01"))
        out.append(Insight(
            key=f"new_sub:{d.name.lower()}", kind="new_subscription", severity="info",
            title=f"{d.name} looks like a {d.cadence} charge",
            body=f"{money(d.amount)} {d.occurrences} times so far, about {money(monthly * 12)} a year. "
                 "Track it as a bill, or cancel it if you don't use it.",
            impact=monthly, action="track_recurring",
            recurring={"name": d.name, "merchant_pattern": d.merchant_pattern, "category": d.category,
                       "purpose": d.purpose, "amount": str(d.amount), "amount_tolerance": str(d.amount_tolerance),
                       "cadence": d.cadence, "anchor_date": d.anchor_date.isoformat()},
        ))
    return out


# --- bills -----------------------------------------------------------------------------------------

def _paid_unlinked(ctx: Context, item: RecurringExpense, due: date) -> bool:
    """A payment that matches the bill but was never linked to it (charges imported from the sheet)."""
    line = budget_line(item.category, item.purpose)
    return any(
        r.txn.recurring_id is None and due - timedelta(days=10) <= r.day <= ctx.today
        and _is_recurring_charge([item], line, r.txn.merchant_raw, r.amount)
        for r in ctx.rows if r.line == line
    )


def bills(ctx: Context) -> list[Insight]:
    """Overdue bills this month, and what's due in the next week."""
    horizon = ctx.today + timedelta(days=UPCOMING_DAYS)
    occs = occurrences(ctx.db, ctx.month_start, ctx.month_end, ctx.today)
    if horizon >= ctx.month_end:
        occs += occurrences(ctx.db, ctx.month_end, _shift(ctx.month_end, 1), ctx.today)
    occs = [o for o in occs if _wants(ctx, o.item)]

    out = []
    for o in occs:
        if o.status == "overdue" and o.due >= ctx.month_start and not _paid_unlinked(ctx, o.item, o.due):
            how = ("Mark it paid if you paid it another way." if not o.item.merchant_pattern
                   else "No matching charge has come in yet.")
            out.append(Insight(
                key=f"overdue:{o.item.id}:{o.due}", kind="overdue_bill", severity="warn",
                title=f"{o.item.name} was due {_day(o.due)}", body=f"{money(o.item.amount)}. {how}",
                impact=o.item.amount, action="open_bills",
            ))
    coming = [o for o in occs if o.status == "upcoming" and ctx.today <= o.due <= horizon]
    if coming:
        total = sum((o.item.amount for o in coming), ZERO)
        week = ctx.today.isocalendar()
        out.append(Insight(
            key=f"upcoming:{week.year}-W{week.week:02d}", kind="upcoming_bills", severity="info",
            title=f"{len(coming)} bill{'s' if len(coming) > 1 else ''} in the next {UPCOMING_DAYS} days · {money(total)}",
            body=", ".join(f"{o.item.name} {money(o.item.amount)} on {_day(o.due)}" for o in coming) + ".",
            impact=total, action="open_bills",
        ))
    return out


def replaced_holds(ctx: Context) -> list[Insight]:
    """A first charge that was probably only a hold: the same merchant then charged a different amount."""
    since = datetime.combine(ctx.today - timedelta(days=HOLD_LOOKBACK_DAYS), datetime.min.time(), tzinfo=ctx.tz)
    recent = ctx.db.scalars(select(Transaction).where(
        Transaction.timestamp >= since, Transaction.excluded.is_(False), Transaction.amount > 0,
    ))
    out = []
    for held, final in suspected_holds(list(recent)):
        if ctx.purpose and held.purpose != ctx.purpose:
            continue
        name = held.merchant_normalized or normalize_merchant(held.merchant_raw)
        held_day, final_day = held.timestamp.astimezone(ctx.tz).date(), final.timestamp.astimezone(ctx.tz).date()
        when = "the same day" if held_day == final_day else _day(final_day)
        out.append(Insight(
            key=f"hold:{held.id}", kind="replaced_hold", severity="warn",
            title=f"{name} {money(held.amount)} may have been replaced",
            body=f"{_day(held_day)}: {money(held.amount)}, then {money(final.amount)} {when}. If the order changed, "
                 f"the first was only a hold and counts it twice. Dismiss if both are real.",
            impact=held.amount, action="remove_hold", transaction_id=held.id,
        ))
    return out


DETECTORS = (spending_lines, subscriptions, bills, replaced_holds)


def insights(db: Session, tz: ZoneInfo, today: date, purpose: str | None = None) -> list[Insight]:
    month_start = today.replace(day=1)
    month_end = _shift(month_start, 1)
    ctx = Context(db, tz, today, purpose, month_start, month_end)
    ctx.rows = _load_rows(ctx, list(db.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True)))))
    dismissed = set(db.scalars(select(InsightDismissal.key)))
    found = [i for detector in DETECTORS for i in detector(ctx) if i.key not in dismissed]
    return sorted(found, key=lambda i: (SEVERITY_ORDER[i.severity], -i.impact))


def dismiss(db: Session, key: str, user: str | None) -> None:
    if db.get(InsightDismissal, key) is None:
        db.add(InsightDismissal(key=key, dismissed_by=user))
        db.commit()
