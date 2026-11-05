"""Import past spending from any CSV (a budgeting spreadsheet, another app's export, a bank's history).

The household maps the file's columns (date, merchant, amount, and optionally category, who and note) and its
own category and "who" names onto Xpensimate's. Then every row:
- becomes a categorization example: Claude sees how the household filed that merchant before, an incoming
  charge that is this very purchase takes its category, and bill detection sees the history;
- feeds rules: a merchant filed the same way (at least MIN_OCCURRENCES times, MIN_AGREEMENT of the time)
  becomes a rule with origin "history";
- if it's dated before the first charge Xpensimate has, can also be added as spending (source "history"), so
  trends and month reviews have a past. Later rows only teach, so nothing is counted twice.

Importing again replaces the previous history import.
"""
import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.taxonomy import DEFAULT_PURPOSE, UNCATEGORIZED, purposes
from app.db.models import CategorizationExample, CategorizationRule, Transaction
from app.services.categories import category_names
from app.services.merchant import merchant_key, normalize_merchant

SOURCE = "history"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
SAMPLE_ROWS = 8
MAX_DISTINCT = 100
HISTORY_RULE_PRIORITY = 5
MIN_OCCURRENCES = 2
MIN_AGREEMENT = 0.8
# Merchant names too vague to become rules (hand-typed entries like "Rent" or "Groceries").
GENERIC_KEYS = {
    "rent", "groceries", "grocery", "gas", "coffee", "restaurant", "restaurants", "shopping", "transfer",
    "payment", "misc", "other", "cash", "atm", "fee", "fees", "interest", "the", "services", "bill", "bills",
}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y",
                "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%m/%d/%y", "%d/%m/%y")
# Common spreadsheet labels -> default categories, for the first guess at the category mapping.
CATEGORY_SYNONYMS = {
    "groceries": "Grocery", "food": "Grocery", "supermarket": "Grocery",
    "dining": "Eating Out", "restaurants": "Eating Out", "restaurant": "Eating Out", "takeout": "Eating Out",
    "coffee": "Eating Out", "fast food": "Eating Out",
    "gas": "Gas & Parking", "fuel": "Gas & Parking", "parking": "Gas & Parking", "transit": "Gas & Parking",
    "transportation": "Gas & Parking", "rent": "Rent + Parking", "mortgage": "Rent + Parking",
    "utilities": "Hydro", "electricity": "Hydro", "internet": "Wifi + 5G", "phone": "Wifi + 5G",
    "home": "Household", "household": "Household", "entertainment": "Entertainment", "movies": "Entertainment",
    "travel": "Travel", "gifts": "Gifting", "gift": "Gifting", "health": "Health & Fitness",
    "fitness": "Health & Fitness", "pharmacy": "Health & Fitness", "personal care": "Personal Care",
    "beauty": "Personal Care", "clothing": "Shopping", "clothes": "Shopping", "shopping": "Shopping",
    "subscriptions": "Subscriptions", "streaming": "Subscriptions", "fees": "Government & Fees",
    "taxes": "Government & Fees", "transfer": "Transfer", "insurance": "Insurance", "car": "Car",
    "auto": "Car", "laundry": "Laundry", "loan": "Loan", "misc": "Misc", "other": "Misc",
}
SHARED_WORDS = {"shared", "joint", "both", "household", "family", "us", "we", "home", "everyone"}
# Header words -> the field they probably are.
HEADER_HINTS = {
    "date": ("date", "day", "when", "posted", "transaction date"),
    "merchant": ("merchant", "description", "payee", "name", "store", "where", "details", "vendor", "item"),
    "amount": ("amount", "cost", "price", "spent", "total", "value", "debit"),
    "category": ("category", "what", "type", "group"),
    "who": ("who", "person", "for", "owner", "purpose", "member"),
    "note": ("note", "notes", "memo", "comment", "comments"),
}


class HistoryImportError(ValueError):
    pass


@dataclass
class Table:
    headers: list[str]
    rows: list[dict[str, str]]


@dataclass
class Mapping:
    date: str
    merchant: str
    amount: str
    date_format: str
    category: str | None = None
    who: str | None = None
    note: str | None = None
    # Whether spending shows as positive or negative numbers in the file (the other sign is a refund).
    spending_is: str = "positive"
    # The file's category and "who" values -> Xpensimate's (missing or None: no category / the shared purpose).
    categories: dict[str, str | None] = field(default_factory=dict)
    who_values: dict[str, str | None] = field(default_factory=dict)
    add_as_spending: bool = True


@dataclass
class HistoryRow:
    spent_on: date
    merchant: str
    amount: Decimal  # spending positive, refunds negative
    category: str | None
    purpose: str
    note: str | None


def read_table(content: bytes) -> Table:
    if len(content) > MAX_BYTES:
        raise HistoryImportError("The file is too big (5 MB at most); split it or export fewer columns")
    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        raise HistoryImportError("The file needs a header row and at least one row of spending")
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(lines, dialect)
    raw_headers = next(reader)
    headers, seen = [], Counter()
    for i, h in enumerate(raw_headers):
        name = " ".join(h.split()) or f"Column {i + 1}"
        seen[name] += 1
        headers.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rows.append({h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(headers)})
        if len(rows) > MAX_ROWS:
            raise HistoryImportError(f"The file has more than {MAX_ROWS:,} rows; split it into smaller files")
    if not rows:
        raise HistoryImportError("The file has no rows under its header")
    return Table(headers, rows)


def parse_amount(text: str) -> Decimal | None:
    t = text.strip().replace("−", "-")
    if not t:
        return None
    negative = t.startswith("(") and t.endswith(")") or t.startswith("-") or t.endswith("-")
    digits = re.sub(r"[^\d.,]", "", t)
    if "," in digits and "." in digits:
        digits = digits.replace(",", "") if digits.rfind(".") > digits.rfind(",") else digits.replace(".", "").replace(",", ".")
    elif "," in digits:
        # "1,234" is thousands; "12,34" is a decimal comma.
        digits = digits.replace(",", ".") if re.search(r",\d{1,2}$", digits) else digits.replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def _reads(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value.split("T")[0].strip(), fmt)
        return True
    except ValueError:
        return False


def date_formats(values: list[str]) -> list[str]:
    """The formats that read the most values, if that's at least 80% of them (a stray typo row is skipped later).

    More than one when the dates are ambiguous, like 03/04/2026.
    """
    values = [v for v in values if v][:500]
    if not values:
        return []
    counts = {fmt: sum(_reads(v, fmt) for v in values) for fmt in DATE_FORMATS}
    best = max(counts.values())
    if best < 0.8 * len(values):
        return []
    return [fmt for fmt, n in counts.items() if n == best]


def guess_category(value: str, categories: list[str]) -> str | None:
    by_lower = {c.lower(): c for c in categories}
    v = value.strip().lower()
    if v in by_lower:
        return by_lower[v]
    guess = CATEGORY_SYNONYMS.get(v)
    return guess if guess in categories else None


def guess_purpose(value: str, names: tuple[str, ...]) -> str | None:
    v = value.strip().lower()
    for name in names:
        if name.lower() == v:
            return name
    return DEFAULT_PURPOSE if v in SHARED_WORDS else None


def suggest(table: Table, categories: list[str], names: tuple[str, ...]) -> dict:
    """A first guess at the column mapping, from header names and what the columns hold."""
    used: set[str] = set()
    out: dict[str, str | None] = {}
    for fieldname, hints in HEADER_HINTS.items():
        pick = None
        for h in table.headers:
            if h in used:
                continue
            low = h.lower()
            if low in hints or any(re.search(rf"\b{re.escape(w)}\b", low) for w in hints):
                pick = h
                break
        if fieldname == "date" and pick is None:
            pick = next((h for h in table.headers if h not in used and date_formats([r[h] for r in table.rows])), None)
        if pick:
            used.add(pick)
        out[fieldname] = pick
    amounts = [parse_amount(r[out["amount"]]) for r in table.rows] if out["amount"] else []
    signs = Counter((a > 0) for a in amounts if a)
    out["spending_is"] = "negative" if signs[False] > signs[True] else "positive"
    out["date_format"] = (date_formats([r[out["date"]] for r in table.rows]) or [None])[0] if out["date"] else None
    return out


def distinct_values(table: Table) -> dict[str, list[str]]:
    """Each column's values, for columns with few enough to map one by one."""
    out = {}
    for h in table.headers:
        counts = Counter(r[h] for r in table.rows if r[h])
        if 0 < len(counts) <= MAX_DISTINCT:
            out[h] = [v for v, _ in counts.most_common()]
    return out


def build_rows(table: Table, m: Mapping) -> tuple[list[HistoryRow], dict[str, int]]:
    for name, column in (("date", m.date), ("merchant", m.merchant), ("amount", m.amount)):
        if column not in table.headers:
            raise HistoryImportError(f"Pick the file's {name} column")
    for column in (m.category, m.who, m.note):
        if column and column not in table.headers:
            raise HistoryImportError(f"The file has no column {column!r}")
    names = purposes()
    rows, skipped = [], Counter()
    for r in table.rows:
        merchant = " ".join(r[m.merchant].split())[:255]
        amount = parse_amount(r[m.amount])
        try:
            spent_on = datetime.strptime(r[m.date].split("T")[0].strip(), m.date_format).date()
        except ValueError:
            spent_on = None
        if not merchant:
            skipped["no merchant"] += 1
            continue
        if spent_on is None:
            skipped["unreadable date"] += 1
            continue
        if not amount:
            skipped["no amount"] += 1
            continue
        category = m.categories.get(r[m.category]) if m.category and r[m.category] else None
        purpose = m.who_values.get(r[m.who]) if m.who and r[m.who] else None
        rows.append(HistoryRow(
            spent_on=spent_on,
            merchant=merchant,
            amount=amount if m.spending_is == "positive" else -amount,
            category=category,
            purpose=purpose if purpose in names else DEFAULT_PURPOSE,
            note=(r[m.note][:1000] or None) if m.note else None,
        ))
    return rows, dict(skipped)


def derive_rules(rows: list[HistoryRow]) -> list[dict]:
    by_key: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row.category and row.amount > 0:
            by_key[merchant_key(row.merchant)][(row.category, row.purpose)] += 1
    rules = []
    for key, pairs in by_key.items():
        total = sum(pairs.values())
        (category, purpose), count = pairs.most_common(1)[0]
        if key.lower() in GENERIC_KEYS or len(key) < 3 or total < MIN_OCCURRENCES or count / total < MIN_AGREEMENT:
            continue
        rules.append({
            "merchant_pattern": key[:128], "target_category": category, "target_purpose": purpose,
            "priority": HISTORY_RULE_PRIORITY, "confidence": Decimal(str(round(count / total, 2))), "origin": "history",
        })
    return rules


def first_live_charge(db: Session) -> date | None:
    """The first charge from anything but a history import: older history rows can be added as spending."""
    tz = ZoneInfo(get_settings().timezone)
    first = db.scalar(select(func.min(Transaction.timestamp)).where(Transaction.source != SOURCE))
    return first.astimezone(tz).date() if first else None


def clear(db: Session) -> None:
    db.execute(delete(CategorizationExample))
    db.execute(delete(CategorizationRule).where(CategorizationRule.origin == "history"))
    db.execute(delete(Transaction).where(Transaction.source == SOURCE))


def import_rows(db: Session, rows: list[HistoryRow], add_as_spending: bool) -> dict[str, int]:
    clear(db)
    cutoff = first_live_charge(db)
    tz = ZoneInfo(get_settings().timezone)
    db.add_all(
        CategorizationExample(
            merchant_key=merchant_key(r.merchant), merchant_raw=r.merchant, category=r.category, purpose=r.purpose,
            note=r.note, amount=-r.amount, spent_on=r.spent_on, trip=False,
        )
        for r in rows
    )
    rules = derive_rules(rows)
    db.add_all(CategorizationRule(**rule) for rule in rules)
    added = 0
    if add_as_spending:
        for i, r in enumerate(rows):
            if cutoff and r.spent_on >= cutoff:
                continue
            ref = hashlib.sha1(f"{i}|{r.spent_on}|{r.amount}|{r.merchant}".encode()).hexdigest()[:24]
            db.add(Transaction(
                source=SOURCE, source_ref=f"history:{ref}",
                timestamp=datetime.combine(r.spent_on, time(12, 0), tzinfo=tz),
                amount=r.amount, txn_type="PURCHASE" if r.amount > 0 else "REFUND",
                merchant_raw=r.merchant, merchant_normalized=normalize_merchant(r.merchant),
                category=r.category or UNCATEGORIZED, category_is_manual=r.category is not None,
                purpose=r.purpose, purpose_is_manual=True, categorized_by="sheet",
                inference_confidence=Decimal("1.00") if r.category else Decimal("0.00"), note=r.note,
            ))
            added += 1
    db.commit()
    return {"rows": len(rows), "examples": len(rows), "rules": len(rules), "added_as_spending": added}


def status(db: Session) -> dict:
    return {
        "examples": db.scalar(select(func.count()).select_from(CategorizationExample)),
        "rules": db.scalar(select(func.count()).where(CategorizationRule.origin == "history")),
        "added_as_spending": db.scalar(select(func.count()).where(Transaction.source == SOURCE)),
        "first_live_charge": first_live_charge(db),
    }


def preview(db: Session, table: Table) -> dict:
    categories = category_names(db)
    names = purposes()
    values = distinct_values(table)
    return {
        "headers": table.headers,
        "sample": table.rows[:SAMPLE_ROWS],
        "row_count": len(table.rows),
        "suggested": suggest(table, categories, names),
        "date_formats": {h: f for h in table.headers if (f := date_formats([r[h] for r in table.rows]))},
        "values": values,
        "category_guesses": {v: g for vs in values.values() for v in vs if (g := guess_category(v, categories))},
        "who_guesses": {v: g for vs in values.values() for v in vs if (g := guess_purpose(v, names))},
        "categories": categories,
        "purposes": list(names),
        "first_live_charge": first_live_charge(db),
    }
