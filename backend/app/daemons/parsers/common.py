import re
from dataclasses import dataclass
from decimal import Decimal

AMOUNT = re.compile(r"(-)?\$\s?(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})")
CARD_SUFFIX = re.compile(r"(?:ending (?:in|with)\s*:?\s*\**\s*|x{4}\s+)(\d{4})\b", re.IGNORECASE)
REFUND_WORDS = re.compile(r"\b(refund|refunded|return|credit(?:ed)? (?:to|back)|reversal)\b", re.IGNORECASE)
# Labelled "Name: value" on one line; value may be empty. [ \t] keeps it from running onto the next line.
LABEL = r"^[ \t]*{label}[ \t]*:[ \t]*(.*)$"

UNKNOWN_MERCHANT = "Unknown merchant"


@dataclass(frozen=True)
class ParsedAlert:
    amount: Decimal
    merchant: str
    account_id: str
    card_suffix: str | None = None
    # The card as the alert names it ("Wealthsimple VIP"), for when the last four digits are not the card's own.
    card_name: str | None = None
    # Money in (a deposit; `merchant` is the payer, `amount` positive). Stored as income only if it is pay
    # (app/services/income.py).
    income: bool = False
    # A move between two of the household's own accounts, from account_id to transfer_to.
    transfer: bool = False
    transfer_to: str | None = None
    # When the alert names the accounts rather than their ids ("TFSA", "Joint Chequing") and who made the move,
    # the household's accounts are matched by name, kind and owner (app/services/accounts.py: account_named).
    from_name: str | None = None
    to_name: str | None = None
    person: str | None = None


def parse_amount(text: str) -> Decimal | None:
    match = AMOUNT.search(text)
    if not match:
        return None
    value = Decimal(f"{match[2].replace(',', '')}.{match[3]}")
    return -value if match[1] else value


def labelled(text: str, label: str) -> str | None:
    """Value of a 'Label: value' line ('' if present but empty, None if absent)."""
    match = re.search(LABEL.format(label=label), text, re.IGNORECASE | re.MULTILINE)
    return match[1].strip() if match else None


def clean_merchant(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .,:;-\"'")
    return name[:255]


def card_suffix(text: str) -> str | None:
    match = CARD_SUFFIX.search(text)
    return match[1] if match else None


def signed(amount: Decimal, text: str) -> Decimal:
    return -amount if amount > 0 and REFUND_WORDS.search(text) else amount
