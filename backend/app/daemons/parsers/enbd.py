"""Emirates NBD card purchase SMS, captured on the phone as a messaging-app notification.

The text never names the bank, so this sentence is the filter (the phone applies the same one):
"Purchase of AED 12.34 with Debit Card ending 4242 at SOME SHOP, CITY. Avl Balance is AED 1,000.00."
The card's last four digits pick the account.
"""
import re
from decimal import Decimal

from app.daemons.parsers.common import ParsedAlert, clean_merchant

# Messaging apps whose notifications are SMS (Google Messages, AOSP, Samsung).
SMS_PACKAGES = frozenset({
    "com.google.android.apps.messaging",
    "com.android.mms",
    "com.samsung.android.messaging",
    "com.android.messaging",
})
REF_PREFIX = "enbd"

PURCHASE = re.compile(
    r"Purchase of AED\s*(?P<amount>[\d,]+\.\d{2})\s+with\s+(?P<card>Debit|Credit)\s+Card\s+ending\s+(?P<last4>\d{4})"
    r"\s+at\s+(?P<merchant>.+?)\.\s+Avl\b",
    re.IGNORECASE | re.DOTALL,
)
OTP = re.compile(r"\b(?:otp|one[- ]time|verification code)\b", re.IGNORECASE)


def has_amount(text: str) -> bool:
    return bool(re.search(r"\bPurchase of AED\b", text, re.IGNORECASE)) and not OTP.search(text)


def parse(title: str, text: str) -> ParsedAlert | None:
    match = PURCHASE.search(text)
    if not match or OTP.search(text):
        return None
    merchant = clean_merchant(match["merchant"])
    if not merchant:
        return None
    return ParsedAlert(
        amount=Decimal(match["amount"].replace(",", "")),
        merchant=merchant,
        # Only the debit card has a well-known account; a credit card is found by its digits.
        account_id="enbd_debit" if match["card"].lower() == "debit" else "",
        card_suffix=match["last4"],
        currency="AED",
    )
