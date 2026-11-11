"""Wealthsimple app notifications (captured on the phone and sent here as raw text).

No real sample has been seen yet, so this accepts the common phrasings ("You spent $12.34 at Tim Hortons",
"Purchase of $12.34 at TIM HORTONS", "$12.34 at Tim Hortons") and anything else with an amount is kept in
raw_ingest_log as "unparsed" so the patterns can be fixed against the real text.

Real samples seen so far:
- title "NORTHSIDE BOXING ", text "You made a recurring payment for $75.40 with your credit card." (the merchant is
  the title)

Health-insurance claim payouts deposited into the Cash account count as a refund (negative amount) under the
insurer's name, so a full-price therapy charge minus the claim nets to what was really paid. Other deposits from
a named payer ("A direct deposit of $3,250.40 from NORTHWIND has arrived") come back as income (income=True); whether
it is really pay is decided on ingest (app/services/income.py). E-transfers received are not.
"""
import re
from decimal import Decimal

from app.daemons.parsers.common import ParsedAlert, card_suffix, clean_merchant, parse_amount

PACKAGE = "com.wealthsimple.trade"

# Things that move money but aren't card spending.
NOT_SPENDING = re.compile(
    r"\b(declined|failed|deposit(?:ed)?|received|e-?transfer|interest|dividend|bought|sold|order|cash ?back|"
    r"payment (?:received|of .* (?:was )?(?:made|received))|direct deposit|contribution|withdrawal)\b",
    re.IGNORECASE,
)
REFUND = re.compile(r"\b(refund(?:ed)?|returned|reversed|credit(?:ed)? (?:back|to))\b", re.IGNORECASE)
SPEND = re.compile(
    r"(?:spent|purchase of|charged|paid)?\s*(?P<amount>\$\s?[\d,]+\.\d{2})\s+(?:at|to|from)\s+"
    r"(?P<merchant>.+?)(?=\s+(?:with|using|on your|on)\b|[.!]?\s*$)",
    re.IGNORECASE,
)

# "You made a recurring payment for $75.40 with your credit card." / "You spent $12.34" - merchant in the title.
TITLE_MERCHANT_SPEND = re.compile(
    r"\b(?:made an? (?:recurring |pre-?authori[sz]ed )?(?:payment|purchase)|spent|paid|purchase)\s+(?:for|of)?\s*"
    r"(?P<amount>\$\s?[\d,]+\.\d{2})(?!\s+(?:at|to|from)\b)",
    re.IGNORECASE,
)
GENERIC_TITLE = re.compile(
    r"^\s*(?:wealthsimple|credit card|cash card|card|card purchase|purchase|payment|recurring payment|spending|"
    r"transaction|refund|new transaction)?\s*$",
    re.IGNORECASE,
)
# Group-benefits insurers whose claim payouts land in the Cash account: canonical name -> how it may be written.
INSURERS = {
    "Manulife": r"manulife|manufacturers life",
    "Sun Life": r"sun ?life",
    "Canada Life": r"canada life|great-?west life",
    "Green Shield": r"green ?shield",
    "Blue Cross": r"blue cross",
    "Desjardins Insurance": r"desjardins (?:insurance|assurance|financial)",
    "Equitable Life": r"equitable life",
    "iA Financial": r"industrial alliance|\bia financial",
    "Medavie": r"medavie",
}
INSURER = re.compile("|".join(f"(?P<i{n}>{pat})" for n, pat in enumerate(INSURERS.values())), re.IGNORECASE)
FAILED = re.compile(r"\b(declined|failed|rejected)\b", re.IGNORECASE)
# "A direct deposit of $3,250.40 from NORTHWIND has arrived" / "You received $3,250.40 from NORTHWIND in your Cash account" /
# "Your payroll deposit of $3,250.40 from NORTHWIND is here".
DEPOSIT = re.compile(
    r"\b(?:direct deposit|payroll|deposit(?:ed)?|received)\b.*?(?P<amount>\$\s?[\d,]+\.\d{2})\s+from\s+"
    r"(?P<payer>.+?)(?=\s+(?:has|is|was|in|into|to)\b|[.!]?\s*$)",
    re.IGNORECASE,
)
E_TRANSFER = re.compile(r"\be-?transfer\b", re.IGNORECASE)


def deposit(body: str) -> ParsedAlert | None:
    """Money arriving in the Cash account from a named payer (pay, most likely)."""
    if FAILED.search(body) or E_TRANSFER.search(body) or not (match := DEPOSIT.search(body)):
        return None
    amount, payer = parse_amount(match["amount"]), clean_merchant(match["payer"])
    if amount is None or amount <= 0 or not payer:
        return None
    return ParsedAlert(amount=amount, merchant=payer, account_id="ws_cash", income=True)


def insurer_in(text: str) -> str | None:
    match = INSURER.search(text)
    return list(INSURERS)[int(match.lastgroup[1:])] if match else None


def has_amount(text: str) -> bool:
    return parse_amount(text) is not None


def account_for(text: str) -> str:
    return "ws_cash" if re.search(r"\bcash card\b|\bcash account\b", text, re.IGNORECASE) else "ws_credit"


def parse(title: str, text: str) -> ParsedAlert | None:
    body = f"{title}. {text}".strip(". ")
    insurer, amount = insurer_in(body), parse_amount(body)
    if insurer and amount is not None and not FAILED.search(body):
        # An insurance claim paid out: money back against the original full-price charge.
        return ParsedAlert(amount=-abs(amount), merchant=insurer, account_id="ws_cash")
    if income := deposit(body):
        return income
    if NOT_SPENDING.search(body) and not REFUND.search(body):
        return None
    if match := SPEND.search(text) or SPEND.search(body):
        merchant = clean_merchant(match["merchant"])
    elif (match := TITLE_MERCHANT_SPEND.search(text)) and not GENERIC_TITLE.match(title):
        merchant = clean_merchant(title)
    else:
        return None
    amount = parse_amount(match["amount"])
    if amount is None or not merchant:
        return None
    if REFUND.search(body):
        amount = -abs(amount)
    return ParsedAlert(amount=amount, merchant=merchant, account_id=account_for(body), card_suffix=card_suffix(body))
