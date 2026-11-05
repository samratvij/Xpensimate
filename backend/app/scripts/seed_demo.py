"""Fill an empty Xpensimate with six months of made-up spending, to try the apps or take screenshots.

    docker compose exec api python -m app.scripts.seed_demo

Works with any household.toml (it uses your purposes and accounts), and reads best with the example one.
It refuses to touch a database that already has transactions, so it can't mix into real data.
"""
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.budget_evaluator import evaluate
from app.core.config import get_settings
from app.core.household import load_household
from app.core.taxonomy import DEFAULT_PURPOSE, PERSON_LINES, PURPOSE_DEFS, UNCATEGORIZED
from app.db.base import SessionLocal
from app.db.models import BudgetEnvelope, Receipt, ReceiptItem, RecurringExpense, Transaction
from app.services.merchant import normalize_merchant
from app.services.reconciler import apply_match
from app.services.recurring import due_dates

SOURCE = "demo"
MONTHS = 6


class DemoRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Spend:
    """A kind of purchase: where, what, for whom, how often a month and roughly how much."""

    merchants: tuple[str, ...]
    category: str
    who: str  # "shared", "person:0", "person:1", "family"
    per_month: float
    low: float
    high: float


# Card descriptors as banks print them (normalize_merchant tidies them for display).
SPENDING = (
    Spend(("LOBLAWS #1032 TORONTO ON", "METRO 412 TORONTO ON", "NO FRILLS #3121", "FARM BOY #27"), "Grocery",
          "shared", 8, 35, 190),
    Spend(("TIM HORTONS #2210", "STARBUCKS #4471", "PIZZA PIZZA #118", "UBER* EATS", "THE BURGER'S PRIEST",
           "PAI NORTHERN THAI"), "Eating Out", "shared", 11, 9, 95),
    Spend(("PETRO-CANADA 4410", "SHELL C01234", "ESSO 7720"), "Gas & Parking", "shared", 3, 48, 82),
    Spend(("GREEN P PARKING", "PRESTO FARE"), "Gas & Parking", "shared", 2, 6, 22),
    Spend(("CANADIAN TIRE #087", "IKEA ETOBICOKE", "DOLLARAMA #540"), "Household", "shared", 2, 12, 140),
    Spend(("CINEPLEX #4410", "LCBO/RAO #217"), "Entertainment", "shared", 2, 18, 70),
    Spend(("SHOPPERS DRUG MART #0412",), "Health & Fitness", "shared", 1, 14, 60),
    Spend(("INDIGO #102",), "Gifting", "shared", 0.4, 25, 80),
    Spend(("BARBER & CO",), "Personal Care", "person:0", 1, 30, 45),
    Spend(("UNIQLO CANADA", "SPORT CHEK #318"), "Shopping", "person:0", 0.8, 35, 140),
    Spend(("SEPHORA #091",), "Personal Care", "person:1", 1, 28, 90),
    Spend(("ZARA #1180", "WINNERS #221"), "Shopping", "person:1", 0.9, 30, 160),
    Spend(("SHOPPERS DRUG MART #0219",), "Shopping", "family", 1, 20, 75),
)

# name, merchant pattern (None = paid outside the cards, marked paid by hand), category, who, amount, day.
BILLS = (
    ("Rent", None, "Rent + Parking", "shared", "2150.00", 1),
    ("Toronto Hydro", "Toronto Hydro", "Hydro", "shared", "92.00", 18),
    ("Rogers", "Rogers", "Wifi + 5G", "shared", "118.65", 10),
    ("Car insurance", "Belair", "Insurance", "shared", "164.20", 5),
    ("Netflix", "Netflix", "Subscriptions", "shared", "20.99", 14),
    ("Spotify", "Spotify", "Subscriptions", "person:1", "11.99", 22),
    ("GoodLife Fitness", "GoodLife", "Health & Fitness", "person:0", "54.99", 3),
    ("Money for the parents", None, "Transfer", "family", "400.00", 25),
)
BILL_DESCRIPTORS = {
    "Toronto Hydro": "TORONTO HYDRO-ELECTRIC", "Rogers": "ROGERS ******0000", "Car insurance": "BELAIR DIRECT",
    "Netflix": "NETFLIX.COM", "Spotify": "SPOTIFY P1A2B3C4", "GoodLife Fitness": "GOODLIFE CLUBS",
}

BUDGETS = {
    "Rent + Parking": "2150", "Hydro": "120", "Wifi + 5G": "130", "Insurance": "170", "Subscriptions": "45",
    "Grocery": "950", "Eating Out": "380", "Gas & Parking": "300", "Household": "200", "Entertainment": "120",
    "Health & Fitness": "120", "Gifting": "80",
}
PERSON_BUDGET = "300"
FAMILY_BUDGETS = {"transfer": "400", "line": "150"}

NOTES = {
    "PAI NORTHERN THAI": "Anniversary dinner",
    "IKEA ETOBICOKE": "Bookshelf for the office",
    "INDIGO #102": "Birthday present",
}


def _purposes() -> dict[str, str]:
    """"shared", "person:0", "person:1", "family" -> this household's purpose names (missing ones fall back)."""
    people = [p.name for p in PURPOSE_DEFS if p.line and p.icon == "person"]
    family = [p.name for p in PURPOSE_DEFS if p.line and p.icon == "family"]
    out = {"shared": DEFAULT_PURPOSE, "family": (family or [DEFAULT_PURPOSE])[0]}
    for i in range(2):
        out[f"person:{i}"] = people[i] if i < len(people) else (people[0] if people else DEFAULT_PURPOSE)
    return out


def _money(rng: random.Random, low: float, high: float) -> Decimal:
    return Decimal(f"{rng.uniform(low, high):.2f}")


class Seeder:
    def __init__(self, db: Session, today: date, seed: int = 42):
        self.db = db
        self.today = today
        self.rng = random.Random(seed)
        self.tz = ZoneInfo(get_settings().timezone)
        self.who = _purposes()
        accounts = [a.id for a in load_household().accounts]
        if not accounts:
            raise DemoRefused("household.toml has no accounts; add at least one")
        self.accounts = accounts
        self.count = 0
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        for _ in range(MONTHS - 2):
            first = (first - timedelta(days=1)).replace(day=1)
        self.start = first

    def at(self, day: date) -> datetime:
        when = datetime.combine(day, time(self.rng.randint(8, 21), self.rng.randint(0, 59)), tzinfo=self.tz)
        # Nothing from later today: charges can't arrive before they happen.
        now = datetime.now(self.tz)
        return min(when, now - timedelta(minutes=self.rng.randint(5, 90))) if day == now.date() else when

    def charge(self, day: date, merchant: str, amount: Decimal, category: str, purpose: str, **extra) -> Transaction:
        self.count += 1
        how = self.rng.choices(("rule", "history", "llm"), weights=(5, 4, 2))[0]
        txn = Transaction(
            source=SOURCE, source_ref=f"demo:{self.count}", account_id=self.rng.choice(self.accounts),
            timestamp=self.at(day), amount=amount, merchant_raw=merchant,
            merchant_normalized=normalize_merchant(merchant), category=category, purpose=purpose,
            categorized_by=how, inference_confidence=Decimal("0.92") if how != "llm" else Decimal("0.81"),
            note=NOTES.get(merchant),
        )
        for key, value in extra.items():
            setattr(txn, key, value)
        self.db.add(txn)
        return txn

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.today - self.start).days + 1)]

    def spending(self) -> None:
        days = self.days()
        for kind in SPENDING:
            # Per month, so the trend has some life: busier Decembers, quieter Februaries, a bit of noise.
            for month_start in {d.replace(day=1) for d in days}:
                month_days = [d for d in days if d.replace(day=1) == month_start]
                share = len(month_days) / 30
                n = int(round(kind.per_month * share * self.rng.uniform(0.7, 1.3)))
                if kind.per_month < 1:
                    n = int(self.rng.random() < kind.per_month * share)
                for _ in range(n):
                    self.charge(self.rng.choice(month_days), self.rng.choice(kind.merchants),
                                _money(self.rng, kind.low, kind.high), kind.category, self.who[kind.who])

    def trip(self) -> None:
        """One weekend away, three months ago."""
        when = (self.today.replace(day=1) - timedelta(days=75)).replace(day=12)
        self.charge(when, "AIR CANADA 0142112345678", Decimal("612.40"), "Travel", DEFAULT_PURPOSE,
                    note="Flights to Montreal")
        self.charge(when + timedelta(days=21), "AIRBNB * HM4ZK2", Decimal("486.00"), "Travel", DEFAULT_PURPOSE)
        self.charge(when + timedelta(days=22), "LE PETIT ALEP MONTREAL QC", Decimal("88.35"), "Eating Out",
                    DEFAULT_PURPOSE)

    def bills(self) -> None:
        for name, pattern, category, who, amount, day in BILLS:
            item = RecurringExpense(
                name=name, merchant_pattern=pattern, category=category, purpose=self.who[who],
                amount=Decimal(amount), amount_tolerance=Decimal("0.35") if category == "Hydro" else Decimal("0.10"),
                cadence="monthly", anchor_date=self.start.replace(day=day), origin="user",
            )
            self.db.add(item)
            self.db.flush()
            for due in due_dates(item, self.start, self.today + timedelta(days=1)):
                # Bills paid by hand (rent, e-transfers) sometimes go out a day late.
                paid = due if pattern else min(due + timedelta(days=self.rng.randint(0, 1)), self.today)
                if paid > self.today:
                    continue
                price = item.amount
                if category == "Hydro":
                    price = _money(self.rng, 68, 125)
                if pattern:
                    self.charge(paid, BILL_DESCRIPTORS[name], price, category, item.purpose,
                                recurring_id=item.id, categorized_by="recurring", inference_confidence=Decimal("1.00"))
                else:
                    self.count += 1
                    self.db.add(Transaction(
                        source="manual", source_ref=f"recurring:{item.id}:{paid.isoformat()}", timestamp=self.at(paid),
                        amount=price, merchant_raw=name, merchant_normalized=name, category=category,
                        category_is_manual=True, purpose=item.purpose, purpose_is_manual=True,
                        categorized_by="manual", recurring_id=item.id,
                    ))

    def receipts(self) -> None:
        """Two big-box runs read from receipts, each line on its own budget line."""
        lines = (
            ("Bananas", "Grocery", "shared", "3.49"), ("Chicken thighs 2kg", "Grocery", "shared", "21.99"),
            ("Greek yogurt", "Grocery", "shared", "8.99"), ("Paper towels 12pk", "Household", "shared", "24.99"),
            ("Laundry detergent", "Household", "shared", "19.99"), ("Running socks", "Shopping", "person:0", "16.99"),
            ("Face moisturizer", "Personal Care", "person:1", "27.99"), ("Coffee beans 1kg", "Grocery", "shared", "18.49"),
        )
        for weeks_ago, picks in ((1, range(8)), (6, (0, 1, 2, 3, 7))):
            day = self.today - timedelta(weeks=weeks_ago)
            items = [lines[i] for i in picks]
            subtotal = sum(Decimal(p) for *_, p in items)
            tax = (sum(Decimal(p) for _, c, _, p in items if c != "Grocery") * Decimal("0.13")).quantize(Decimal("0.01"))
            total = subtotal + tax
            txn = self.charge(day, "COSTCO WHOLESALE W521", total, "Grocery", DEFAULT_PURPOSE)
            receipt = Receipt(merchant="Costco Wholesale", timestamp=txn.timestamp, total=total, tax=tax)
            receipt.items = [
                ReceiptItem(position=i, sku_name=name, raw_text=name.upper()[:24], category=category,
                            purpose=self.who[who], price=Decimal(price))
                for i, (name, category, who, price) in enumerate(items)
            ]
            self.db.add(receipt)
            self.db.flush()
            apply_match(self.db, receipt, txn)

    def to_review(self) -> None:
        """A couple of fresh charges nobody has categorized yet."""
        for merchant, amount in (("SQ *MAPLE LEAF FLOWERS", "42.00"), ("PAYPAL *ETSYCANADA", "36.50")):
            self.charge(self.today - timedelta(days=self.rng.randint(0, 2)), merchant, Decimal(amount),
                        UNCATEGORIZED, DEFAULT_PURPOSE, categorized_by="none", inference_confidence=Decimal("0.00"))

    def budgets(self) -> None:
        limits = dict(BUDGETS)
        family = next((p for p in PURPOSE_DEFS if p.line and p.icon == "family"), None)
        for line, icon in PERSON_LINES.items():
            if icon != "family":
                limits[line] = PERSON_BUDGET
        if family:
            if family.transfer_line:
                limits[family.transfer_line] = FAMILY_BUDGETS["transfer"]
            limits[family.line] = FAMILY_BUDGETS["line"]
        for line, limit in limits.items():
            self.db.add(BudgetEnvelope(category=line, monthly_limit=Decimal(limit)))

    def run(self) -> int:
        self.bills()
        self.spending()
        self.trip()
        self.receipts()
        self.to_review()
        self.budgets()
        self.db.commit()
        # This month's budget alerts, as if the charges had arrived live.
        evaluate(self.db, set(BUDGETS) | set(PERSON_LINES))
        return self.count


def seed_demo(db: Session, today: date | None = None, seed: int = 42) -> int:
    if db.scalar(select(func.count()).select_from(Transaction)):
        raise DemoRefused("this database already has transactions; demo data only goes into an empty one")
    today = today or datetime.now(ZoneInfo(get_settings().timezone)).date()
    return Seeder(db, today, seed).run()


if __name__ == "__main__":
    with SessionLocal() as session:
        try:
            n = seed_demo(session)
        except DemoRefused as e:
            sys.exit(f"Not seeding: {e}")
    print(f"Added {n} demo transactions, bills, budgets and receipts.")
