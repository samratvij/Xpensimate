"""Bank statement CSV parsers.

Each bank exports differently (sign of spending, date format, payments). parse_statement() detects the
format from the header row and returns rows in one shape: spending positive, refunds and payments
negative, plus a kind (purchase | refund | payment | interest | deposit | transfer). A deposit is money into a
bank account (pay, an e-transfer received, a transfer in); its amount is negative like a refund. A transfer is
money sent out of a bank account (an e-transfer, a move to another account); positive like a purchase.
"""
import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

PURCHASE, REFUND, PAYMENT, INTEREST, DEPOSIT = "purchase", "refund", "payment", "interest", "deposit"
TRANSFER_OUT = "transfer"


@dataclass
class StatementRow:
    index: int
    spent_on: date
    merchant: str
    amount: Decimal
    kind: str
    at: time | None = None
    bank_category: str | None = None
    ref: str | None = None  # the bank's own transaction id, when the export has one
    pending: bool = False  # screenshots: not yet posted by the bank


@dataclass
class ParsedStatement:
    bank_format: str
    account_id: str
    rows: list[StatementRow] = field(default_factory=list)


_PAYMENT_REVERSAL = re.compile(r"PAYMENT.*(REVERS|RETURN)|(REVERS|RETURN).*PAYMENT", re.IGNORECASE)


def is_payment_reversal(merchant: str) -> bool:
    return bool(_PAYMENT_REVERSAL.search(merchant))


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("The file isn't readable text")


def _money(value: str) -> Decimal:
    try:
        return Decimal(value.replace("$", "").replace(",", "").strip())
    except InvalidOperation as e:
        raise ValueError(f"Not an amount: {value!r}") from e


def _records(lines: list[str], start: int) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader if any((v or "").strip() for v in r.values())]


def _ctm(lines: list[str], start: int) -> ParsedStatement:
    """Canadian Tire (Triangle): summary lines first; spending positive; has REF and posted date."""
    out = ParsedStatement("ctm", "ctfs_triangle")
    for i, r in enumerate(_records(lines, start)):
        amount = _money(r["AMOUNT"])
        kind_raw = r["TYPE"].upper()
        if "PAYMENT" in kind_raw or is_payment_reversal(r["DESCRIPTION"]):
            kind = PAYMENT
        elif "INTEREST" in kind_raw:
            kind = INTEREST
        elif amount < 0:
            kind = REFUND
        else:
            kind = PURCHASE
        out.rows.append(StatementRow(
            index=i, spent_on=date.fromisoformat(r["TRANSACTION DATE"]), merchant=r["DESCRIPTION"], amount=amount,
            kind=kind, bank_category=r.get("Category") or None, ref=r["REF"].lstrip("#") or None,
        ))
    return out


def _pcf(lines: list[str], start: int) -> ParsedStatement:
    """PC Financial Mastercard: spending negative; MM/DD/YYYY plus a time of day."""
    out = ParsedStatement("pcf", "pc_mastercard")
    kinds = {"PURCHASE": PURCHASE, "REFUND": REFUND, "PAYMENT": PAYMENT, "INTEREST": INTEREST}
    for i, r in enumerate(_records(lines, start)):
        at = datetime.strptime(r["Time"], "%I:%M %p").time() if r.get("Time") else None
        kind = PAYMENT if is_payment_reversal(r["Description"]) else kinds.get(r["Type"].upper(), PURCHASE)
        out.rows.append(StatementRow(
            index=i, spent_on=datetime.strptime(r["Date"], "%m/%d/%Y").date(), merchant=r["Description"],
            amount=-_money(r["Amount"]), kind=kind, at=at,
        ))
    return out


_TANGERINE_CATEGORY = re.compile(r"Category:\s*(.+)$")


def _tangerine(lines: list[str], start: int) -> ParsedStatement:
    """Tangerine Mastercard: DEBIT negative, CREDIT positive (payments and refunds); category in the memo."""
    out = ParsedStatement("tangerine", "tangerine_mastercard")
    for i, r in enumerate(_records(lines, start)):
        amount = -_money(r["Amount"])
        if r["Transaction"].upper() == "CREDIT" or is_payment_reversal(r["Name"]):
            kind = PAYMENT if "PAYMENT" in r["Name"].upper() else REFUND
        else:
            kind = INTEREST if "INTEREST" in r["Name"].upper() else PURCHASE
        category = _TANGERINE_CATEGORY.search(r.get("Memo", ""))
        out.rows.append(StatementRow(
            index=i, spent_on=datetime.strptime(r["Transaction date"], "%m/%d/%Y").date(), merchant=r["Name"],
            amount=amount, kind=kind, bank_category=category[1].strip() if category else None,
        ))
    return out


def _wealthsimple(lines: list[str], start: int) -> ParsedStatement:
    """Wealthsimple credit card: spending negative; payments have no merchant; only completed rows count."""
    out = ParsedStatement("wealthsimple", "ws_credit")
    kinds = {"purchase": PURCHASE, "refund": REFUND, "payment": PAYMENT, "interest": INTEREST}
    rows = [r for r in _records(lines, start) if r.get("status", "Completed").lower() == "completed"]
    for i, r in enumerate(rows):
        kind = PAYMENT if is_payment_reversal(r["merchant"]) else kinds.get(r["transaction_type"].lower(), PURCHASE)
        merchant = r["merchant"] or {PAYMENT: "Payment", INTEREST: "Interest charge"}.get(kind, r["transaction_type"])
        out.rows.append(StatementRow(
            index=i, spent_on=date.fromisoformat(r["transaction_date"]), merchant=merchant,
            amount=-_money(r["amount"]), kind=kind, bank_category=r.get("category") or None,
        ))
    return out


# (format, header test, parser). Headers are matched case-insensitively on their column names.
_FORMATS = [
    ("ctm", {"ref", "transaction date", "posted date", "description", "amount"}, _ctm),
    ("pcf", {"description", "type", "card holder name", "date", "time", "amount"}, _pcf),
    ("tangerine", {"transaction date", "transaction", "name", "memo", "amount"}, _tangerine),
    ("wealthsimple", {"transaction_date", "transaction_type", "status", "merchant", "amount"}, _wealthsimple),
]
SUPPORTED = "Canadian Tire, PC Financial, Tangerine and Wealthsimple CSV exports, and RBC PDF statements"


def parse_statement(content: bytes) -> ParsedStatement:
    if content.startswith(b"%PDF"):
        from app.statements.pdf import parse_pdf_statement  # pdfplumber is only needed for PDFs

        return parse_pdf_statement(content)
    lines = _decode(content).splitlines()
    for start, line in enumerate(lines[:15]):
        columns = {c.strip().strip('"').lower() for c in next(csv.reader([line]), [])}
        for _, required, parser in _FORMATS:
            if required <= columns:
                parsed = parser(lines, start)
                if not parsed.rows:
                    raise ValueError("The statement has no transactions")
                return parsed
    raise ValueError(f"Unrecognized statement format. Supported: {SUPPORTED}.")


def source_refs(account_id: str, rows: list[StatementRow]) -> list[str]:
    """A stable id per row, so importing the same statement again finds what it already added.

    Rows without a bank reference are keyed by account, date, amount and merchant, numbered when the same
    charge appears twice on one day (two identical coffees are two rows, not a duplicate).
    """
    seen: Counter = Counter()
    refs = []
    for row in rows:
        if row.ref:
            refs.append(f"stmt:{account_id}:{row.ref}")
            continue
        key = f"{account_id}|{row.spent_on}|{row.amount}|{row.merchant.upper()}"
        seen[key] += 1
        digest = hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()[:24]
        refs.append(f"stmt:{account_id}:{digest}")
    return refs
