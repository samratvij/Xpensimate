import re

from app.daemons.parsers.common import ParsedAlert, card_suffix, clean_merchant, parse_amount, signed

NAME = "Tangerine"
SENDER_DOMAINS = ("tangerine.ca",)

SUBJECTS = re.compile(r"transaction has been made|purchase", re.IGNORECASE)
# "... a transaction of $11.29 was made on your Tangerine ® Money-Back World Mastercard ®* 5000 xxxx xxxx 1234
#  at DOORDASHDASHPASS on November 01, 2026."
PROSE = re.compile(
    r"transaction of\s+(?P<amount>-?\$\s?[\d,]+\.\d{2}).*?\bat\s+(?P<merchant>.+?)\s+on\s+[A-Z][a-z]+ \d{1,2}, \d{4}",
    re.IGNORECASE | re.DOTALL,
)


def is_transaction(subject: str) -> bool:
    return bool(SUBJECTS.search(subject))


def parse(subject: str, body: str) -> ParsedAlert | None:
    match = PROSE.search(body)
    if not match:
        return None
    amount = parse_amount(match["amount"])
    if amount is None:
        return None
    account = "tangerine_mastercard" if "mastercard" in body[: match.end()].lower() else "tangerine_cheq"
    return ParsedAlert(
        amount=signed(amount, body),
        merchant=clean_merchant(match["merchant"]),
        account_id=account,
        card_suffix=card_suffix(body),
    )
