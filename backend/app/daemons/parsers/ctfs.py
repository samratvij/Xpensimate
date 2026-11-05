import re

from app.daemons.parsers.common import (
    ParsedAlert,
    card_suffix,
    clean_merchant,
    labelled,
    parse_amount,
    signed,
)

NAME = "Canadian Tire Financial Services"
SENDER_DOMAINS = ("ctfs.com",)
ACCOUNT_ID = "ctfs_triangle"

# No real CTFS purchase alert seen yet: this is a best guess at NotifyMe wording.
# Excludes "Your One Time Code", statements and CT Money promotions.
SUBJECTS = re.compile(r"\b(purchase|transaction)\b", re.IGNORECASE)
PROSE = re.compile(r"\$\s?[\d,]+\.\d{2}.*?\bat\s+(?P<merchant>.+?)(?=\s+on\b|\s*[.,;]\s|\s*$)", re.IGNORECASE)


def is_transaction(subject: str) -> bool:
    return bool(SUBJECTS.search(subject))


def parse(subject: str, body: str) -> ParsedAlert | None:
    amount_text = labelled(body, r"(?:Purchase |Transaction )?Amount")
    amount = parse_amount(amount_text or body)
    merchant = labelled(body, r"Merchant(?: name)?")
    if not merchant and (match := PROSE.search(body)):
        merchant = match["merchant"]
    if amount is None or not merchant:
        return None
    return ParsedAlert(
        amount=signed(amount, body),
        merchant=clean_merchant(merchant),
        account_id=ACCOUNT_ID,
        card_suffix=card_suffix(body),
    )
