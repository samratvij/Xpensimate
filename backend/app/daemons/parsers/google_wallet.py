"""Google Wallet (Google Pay) tap-to-pay notifications, captured on the phone.

Wallet names the merchant in the title and the amount and card in the text ("$12.34 with Mastercard ••1234"),
though wording varies; anything with an amount that can't be read is kept as "unparsed" to tune this.
The card's last four digits pick the account.
"""
import re

from app.daemons.parsers.common import ParsedAlert, clean_merchant, parse_amount

PACKAGE = "com.google.android.apps.walletnfcrel"

GENERIC_TITLES = re.compile(r"^(google (wallet|pay)|payment( made| sent| complete[d]?)?|purchase|transaction)$", re.IGNORECASE)
NOT_SPENDING = re.compile(r"\b(declined|failed|couldn.t|could not|added|set up|ready to use|verify|refund requested)\b",
                          re.IGNORECASE)
REFUND = re.compile(r"\b(refund(?:ed)?|returned|reversed)\b", re.IGNORECASE)
LAST4 = re.compile(r"(?:[•·*]{2,}\s?|ending (?:in|with)\s+)(\d{4})\b")
MERCHANT_IN_TEXT = re.compile(r"\b(?:at|to)\s+(.+?)(?=\s+(?:with|using|on)\b|[.!]?\s*$)", re.IGNORECASE)


def card_last4(text: str) -> str | None:
    match = LAST4.search(text)
    return match[1] if match else None


def parse(title: str, text: str) -> ParsedAlert | None:
    body = f"{title}. {text}"
    if NOT_SPENDING.search(body):
        return None
    amount = parse_amount(text) or parse_amount(title)
    if amount is None:
        return None
    merchant = ""
    if title.strip() and not GENERIC_TITLES.match(title.strip()) and parse_amount(title) is None:
        merchant = title
    elif match := MERCHANT_IN_TEXT.search(text):
        merchant = match[1]
    merchant = clean_merchant(merchant)
    if not merchant:
        return None
    if REFUND.search(body):
        amount = -abs(amount)
    # The account is resolved by the caller from the card's last four digits.
    return ParsedAlert(amount=amount, merchant=merchant, account_id="", card_suffix=card_last4(body))
