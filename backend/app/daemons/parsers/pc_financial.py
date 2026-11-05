import re

from app.daemons.parsers.common import (
    UNKNOWN_MERCHANT,
    ParsedAlert,
    card_suffix,
    clean_merchant,
    labelled,
    parse_amount,
    signed,
)

NAME = "PC Financial"
SENDER_DOMAINS = ("pcfinancial.ca",)

# "PC® Mastercard® purchase notice", "PC Money™ Account purchase notice"
SUBJECTS = re.compile(r"purchase notice|transaction notice", re.IGNORECASE)


def is_transaction(subject: str) -> bool:
    return bool(SUBJECTS.search(subject))


def parse(subject: str, body: str) -> ParsedAlert | None:
    amount_text = labelled(body, "Purchase amount") or labelled(body, "Amount")
    amount = parse_amount(amount_text) if amount_text else None
    if amount is None:
        return None
    # The Merchant line exists but is sometimes blank (seen on PC Money Account debit purchases).
    merchant = clean_merchant(labelled(body, "Merchant") or "") or UNKNOWN_MERCHANT
    account = "pc_money" if "pc money" in subject.lower() else "pc_mastercard"
    return ParsedAlert(amount=signed(amount, body), merchant=merchant, account_id=account, card_suffix=card_suffix(body))
