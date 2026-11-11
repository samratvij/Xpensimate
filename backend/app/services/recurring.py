"""Recurring expenses: detection from history, linking charges to them, and what's still expected.

A recurring expense has a cadence and an anchor date; its due dates repeat from the anchor (monthly keeps
the anchor's day of month, clamped to short months). A charge whose merchant matches the pattern and whose
amount is within tolerance is linked to it as a payment. Each payment pays the unpaid due date nearest to
it, even across a month edge (rent paid on Oct 30 pays Nov 1); unpaid due dates are "expected".
"""
import calendar
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import money
from app.core.config import get_settings
from app.core.taxonomy import budget_line
from app.db.models import CategorizationExample, RecurringExpense, Transaction
from app.services.categories import categories_with
from app.services.merchant import matches_pattern, merchant_key

# Typical gap between charges and the range a single gap may fall in.
CADENCES = {
    "weekly": (7, (5, 9)),
    "biweekly": (14, (11, 18)),
    "monthly": (30, (24, 37)),
}
MIN_OCCURRENCES = 3
MIN_REGULAR_SHARE = 0.7
MAX_AMOUNT_SPREAD = Decimal("0.25")
# A series not seen for this many cadence periods has probably stopped.
STALE_PERIODS = Decimal("2.5")
# A due date this many days in the past without a payment is overdue rather than upcoming.
OVERDUE_GRACE_DAYS = 3
# A payment pays a due date at most this share of a period away from it (early or late).
MAX_PAYMENT_OFFSET = 0.6
# Hand-typed names (from imported history) that are recurring but say nothing about the merchant on a card statement.
NO_PATTERN_KEYS = {
    "rent", "mortgage", "hydro", "electricity", "utilities", "parking", "internet", "phone", "internet and phone",
    "insurance", "car insurance", "gym", "daycare", "tuition", "rent + parking",
}


def add_months(day: date, months: int, anchor_day: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def due_dates(item: RecurringExpense, start: date, end: date) -> list[date]:
    """Due dates in [start, end)."""
    anchor = item.anchor_date
    if item.cadence == "monthly":
        first = date(start.year, start.month, 1)
        dates, months = [], 0
        while (due := add_months(first, months, anchor.day)) < end:
            if due >= start:
                dates.append(due)
            months += 1
        return dates
    step = CADENCES[item.cadence][0]
    offset = (start - anchor).days % step
    due = start + timedelta(days=(step - offset) % step)
    dates = []
    while due < end:
        dates.append(due)
        due += timedelta(days=step)
    return dates


def _merchant_matches(pattern: str, merchant_raw: str) -> bool:
    # "*" and runs of spaces separate words too: "GOOGLE *GOOGLE*ONE 01M" is a "Google One" charge.
    loose = re.sub(r"\s+", " ", merchant_raw.replace("*", " ")).strip()
    return matches_pattern(pattern, merchant_key(merchant_raw), merchant_raw, loose)


def _amount_matches(item: RecurringExpense, amount: Decimal) -> bool:
    return abs(amount - item.amount) <= item.amount * item.amount_tolerance


def matches(item: RecurringExpense, merchant_raw: str, amount: Decimal) -> bool:
    if not item.active or not item.merchant_pattern or amount <= 0:
        return False
    return _merchant_matches(item.merchant_pattern, merchant_raw) and _amount_matches(item, amount)


def link_transaction(db: Session, txn: Transaction) -> RecurringExpense | None:
    """Mark a new charge as a payment of the recurring expense it belongs to (closest amount wins).

    The recurring expense is the most specific thing known about the charge, so it also sets the category
    and purpose, except where the user chose them by hand.
    """
    if txn.recurring_id is not None:
        return None
    candidates = [
        item for item in db.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True)))
        if matches(item, txn.merchant_raw, txn.amount)
    ]
    if not candidates:
        return None
    item = min(candidates, key=lambda i: abs(txn.amount - i.amount))
    txn.recurring_id = item.id
    if not txn.category_is_manual:
        txn.category = item.category
    if not txn.purpose_is_manual:
        txn.purpose = item.purpose
    txn.inference_confidence = Decimal("1.00")
    txn.categorized_by = "recurring"
    return item


# How far back a newly created or detected item looks for charges that already paid it.
BACKFILL_DAYS = 45


def link_existing(db: Session, item: RecurringExpense) -> int:
    """Claim recent, unlinked charges for a recurring expense that was just added or edited."""
    since = datetime.now(ZoneInfo(get_settings().timezone)) - timedelta(days=BACKFILL_DAYS)
    linked = 0
    for txn in db.scalars(
        select(Transaction).where(
            Transaction.recurring_id.is_(None), Transaction.excluded.is_(False), Transaction.timestamp >= since
        )
    ):
        if matches(item, txn.merchant_raw, txn.amount):
            txn.recurring_id = item.id
            if not txn.category_is_manual:
                txn.category = item.category
            if not txn.purpose_is_manual:
                txn.purpose = item.purpose
            txn.inference_confidence = Decimal("1.00")
            txn.categorized_by = "recurring"
            linked += 1
    return linked


# --- new subscriptions ---------------------------------------------------------------------------

SUBSCRIPTION_TOLERANCE = Decimal("0.10")
# Earlier charges this recent decide whether a merchant is a subscription; older ones mean it lapsed and restarted.
SUBSCRIPTION_LOOKBACK_DAYS = 75
# App stores and processors that bill on a service's behalf: "GOOGLE *GOOGLE*ONE 01M" is Google One.
_BILLER_PREFIX = re.compile(r"^(GOOGLE|APPLE\.COM/BILL|PAYPAL|PP|SQ|SP)\s*\*\s*", re.IGNORECASE)


# A company suffix ends the name: "GITHUB  INC. SAN FRANCISCO CA US" is GitHub.
_COMPANY_SUFFIX = re.compile(r"\s+(INC|LLC|LTD|CORP|GMBH)\b.*$", re.IGNORECASE)


def subscription_name(merchant_raw: str) -> str:
    name = _BILLER_PREFIX.sub("", merchant_raw.strip()).replace("*", " ")
    return merchant_key(_COMPANY_SUFFIX.sub("", name))


def track_new_subscription(db: Session, txn: Transaction) -> RecurringExpense | None:
    """Start a monthly bill for a subscription charge (a category switched to "subscriptions") nothing tracks yet, so its renewal shows in Bills.

    It's a subscription if the merchant hasn't charged lately, or charged about the same a month earlier (one
    that was never tracked); other recent repeats are irregular purchases. Anything the user stopped stays
    stopped. For a charge no recurring expense claimed; links it to the new one.
    """
    if (txn.recurring_id is not None or txn.excluded or txn.amount <= 0
            or txn.category not in categories_with(db, "subscriptions")):
        return None
    name = subscription_name(txn.merchant_raw)
    if not name:
        return None
    known = list(db.scalars(select(RecurringExpense)))
    if any(
        r.merchant_pattern and _merchant_matches(r.merchant_pattern, txn.merchant_raw) and _amount_matches(r, txn.amount)
        for r in known
    ):
        return None  # tracked (or stopped on purpose)
    tz = ZoneInfo(get_settings().timezone)
    day = txn.timestamp.astimezone(tz).date()
    earlier = [
        (t.timestamp.astimezone(tz).date(), t.amount)
        for t in db.scalars(select(Transaction).where(
            Transaction.excluded.is_(False), Transaction.amount > 0,
            Transaction.timestamp < txn.timestamp,
            Transaction.timestamp >= txn.timestamp - timedelta(days=SUBSCRIPTION_LOOKBACK_DAYS),
        ))
        if t.id != txn.id and _merchant_matches(name, t.merchant_raw)
    ]
    lo, hi = CADENCES["monthly"][1]
    if earlier and not any(
        lo <= (day - d).days <= hi and abs(a - txn.amount) <= txn.amount * SUBSCRIPTION_TOLERANCE for d, a in earlier
    ):
        return None
    names = {r.name.lower() for r in known}
    if name.lower() in names:  # another plan from the same service
        name = f"{name} {money(txn.amount)}"
        if name.lower() in names:
            return None
    item = RecurringExpense(
        name=name, merchant_pattern=subscription_name(txn.merchant_raw), category=txn.category, purpose=txn.purpose,
        amount=txn.amount, amount_tolerance=SUBSCRIPTION_TOLERANCE, cadence="monthly", anchor_date=day,
        origin="detected",
    )
    db.add(item)
    db.flush()
    txn.recurring_id = item.id
    return item


def next_due(item: RecurringExpense, after: date) -> date:
    return due_dates(item, after + timedelta(days=1), after + timedelta(days=CADENCES[item.cadence][0] * 2))[0]


@dataclass
class Occurrence:
    item: RecurringExpense
    due: date
    status: str  # paid | upcoming | overdue

    @property
    def budget_line(self) -> str:
        return budget_line(self.item.category, self.item.purpose)


def paid_due_dates(item: RecurringExpense, payments: list[date], start: date, end: date) -> set[date]:
    """The due dates in [start, end) that `payments` (local dates of the item's linked charges) paid.

    Each payment, in date order, pays the unpaid due date nearest to it (the earlier one on a tie), if that
    due date is within MAX_PAYMENT_OFFSET of a period. Due dates are considered for a period beyond both
    ends, so a payment made early or late lands on the right side of a month edge.
    """
    period = CADENCES[item.cadence][0]
    reach = timedelta(days=period * MAX_PAYMENT_OFFSET)
    margin = timedelta(days=period * 2)
    open_dues = due_dates(item, start - margin, end + margin)
    paid: set[date] = set()
    for day in sorted(payments):
        candidates = [d for d in open_dues if abs(day - d) <= reach]
        if not candidates:
            continue
        due = min(candidates, key=lambda d: (abs((day - d).days), d))
        open_dues.remove(due)
        paid.add(due)
    return {d for d in paid if start <= d < end}


def occurrences(db: Session, start: date, end: date, today: date) -> list[Occurrence]:
    """Every due date of every active recurring expense in [start, end), with whether it's been paid."""
    tz = ZoneInfo(get_settings().timezone)
    # Payments a period either side of the window can still pay a due date inside it.
    longest = timedelta(days=max(c[0] for c in CADENCES.values()))
    window = (
        datetime.combine(start - longest, datetime.min.time(), tzinfo=tz),
        datetime.combine(end + longest, datetime.min.time(), tzinfo=tz),
    )
    payments: dict = defaultdict(list)
    for recurring_id, timestamp in db.execute(
        select(Transaction.recurring_id, Transaction.timestamp).where(
            Transaction.recurring_id.is_not(None),
            Transaction.excluded.is_(False),
            Transaction.timestamp >= window[0],
            Transaction.timestamp < window[1],
        )
    ):
        payments[recurring_id].append(timestamp.astimezone(tz).date())
    result = []
    for item in db.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True))):
        paid = paid_due_dates(item, payments.get(item.id, []), start, end)
        for due in due_dates(item, start, end):
            if due in paid:
                status = "paid"
            elif due < today - timedelta(days=OVERDUE_GRACE_DAYS):
                status = "overdue"
            else:
                status = "upcoming"
            result.append(Occurrence(item, due, status))
    return sorted(result, key=lambda o: (o.due, o.item.name))


def expected_spend(
    db: Session, start: date, end: date, today: date, group_by: str, purpose: str | None = None
) -> dict[str, Decimal]:
    """Unpaid recurring amounts still due, by budget line or category. Nothing is expected of a past month."""
    if end <= today:
        return {}
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for occ in occurrences(db, start, end, today):
        if occ.status != "paid" and purpose in (None, occ.item.purpose):
            key = occ.budget_line if group_by == "budget_line" else occ.item.category
            totals[key] += occ.item.amount
    return dict(totals)


# --- detection ------------------------------------------------------------------------------------

@dataclass
class Detected:
    name: str
    merchant_pattern: str | None
    category: str
    purpose: str
    amount: Decimal
    amount_tolerance: Decimal
    cadence: str
    anchor_date: date
    occurrences: int


def _history_points(db: Session) -> dict[str, list[tuple[date, Decimal, str | None, str]]]:
    """(date, amount spent, category, purpose) per merchant key, from imported history and from transactions."""
    tz = ZoneInfo(get_settings().timezone)
    points: dict[str, list] = defaultdict(list)
    for e in db.scalars(select(CategorizationExample).where(CategorizationExample.trip.is_(False))):
        if e.spent_on and e.amount is not None and e.amount < 0:
            points[e.merchant_key].append((e.spent_on, -e.amount, e.category, e.purpose))
    # Spending added by a history import is already here as its examples.
    for t in db.scalars(select(Transaction).where(Transaction.amount > 0, Transaction.excluded.is_(False),
                                              Transaction.source != "history")):
        points[merchant_key(t.merchant_raw)].append(
            (t.timestamp.astimezone(tz).date(), t.amount, t.category, t.purpose)
        )
    return points


def detect(db: Session, today: date) -> list[Detected]:
    found = []
    for key, raw_points in _history_points(db).items():
        by_day = {}
        for day, amount, category, purpose in sorted(raw_points, key=lambda p: (p[0], p[1])):
            by_day[day] = (amount, category, purpose)  # one charge per day is enough to see a rhythm
        days = sorted(by_day)
        if len(days) < MIN_OCCURRENCES:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        typical_gap = statistics.median(gaps)
        cadence = next((name for name, (_, (lo, hi)) in CADENCES.items() if lo <= typical_gap <= hi), None)
        if cadence is None:
            continue
        lo, hi = CADENCES[cadence][1]
        if sum(lo <= g <= hi for g in gaps) / len(gaps) < MIN_REGULAR_SHARE:
            continue
        if (today - days[-1]).days > CADENCES[cadence][0] * STALE_PERIODS:
            continue

        amounts = [by_day[d][0] for d in days]
        typical = Decimal(statistics.median(amounts))
        spread = max(abs(a - typical) for a in amounts) / typical if typical else Decimal("1")
        if statistics.median([abs(a - typical) for a in amounts]) / typical > MAX_AMOUNT_SPREAD:
            continue
        recent = Decimal(statistics.median(amounts[-3:])).quantize(Decimal("0.01"))
        labels = Counter((by_day[d][1], by_day[d][2]) for d in days if by_day[d][1])
        if not labels:
            continue
        (category, purpose), _ = labels.most_common(1)[0]
        found.append(Detected(
            name=key,
            merchant_pattern=None if key.lower() in NO_PATTERN_KEYS else key,
            category=category,
            purpose=purpose,
            amount=recent,
            amount_tolerance=min(max(spread.quantize(Decimal("0.01")) + Decimal("0.05"), Decimal("0.10")), Decimal("0.50")),
            cadence=cadence,
            anchor_date=days[-1],
            occurrences=len(days),
        ))
    return sorted(found, key=lambda d: -d.amount)


def save_detected(db: Session, detected: list[Detected]) -> list[RecurringExpense]:
    """Add newly detected series; anything already known (by name) is left as the user has it."""
    known = {r.name.lower() for r in db.scalars(select(RecurringExpense))}
    added = []
    for d in detected:
        if d.name.lower() in known:
            continue
        item = RecurringExpense(
            name=d.name, merchant_pattern=d.merchant_pattern, category=d.category, purpose=d.purpose,
            amount=d.amount, amount_tolerance=d.amount_tolerance, cadence=d.cadence, anchor_date=d.anchor_date,
            origin="detected",
        )
        db.add(item)
        db.flush()
        link_existing(db, item)
        added.append(item)
    db.commit()
    return added
