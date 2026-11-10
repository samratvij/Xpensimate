"""Which account an alert or notification belongs to."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account


def names_issuer(card_name: str, account: Account) -> bool:
    """Whether a card name ("Wealthsimple VIP", "PC Financial Mastercard") is this account's bank."""
    word = account.institution.split()[0]
    return re.search(rf"\b{re.escape(word)}\b", card_name, re.IGNORECASE) is not None


def account_for_alert(db: Session, card_suffix: str | None, default: str | None, card_name: str | None = None) -> str | None:
    """The account with a card ending in `card_suffix`, else the parser's `default` (None if neither).

    The card number wins over the parser's guess, so a new or second card at a bank is a data change
    (card_last4 in household.toml, one or several per account), not a parser change. If two accounts share
    those digits, the default breaks the tie when it is one of them; otherwise the alert falls back to the default.

    When the alert names the card's bank (`card_name`), only that bank's accounts are considered: a wallet's
    token digits can match another bank's card, and the bank's name is the surer of the two.
    """
    accounts = list(db.scalars(select(Account)))
    # Parsers name a well-known account id (e.g. tangerine_mastercard); it only counts if the household has it.
    default = default if default and any(a.id == default for a in accounts) else None
    if card_name and (named := [a for a in accounts if names_issuer(card_name, a)]):
        ids = {a.id for a in named}
        matches = [a.id for a in named if card_suffix and card_suffix in a.card_digits]
        if len(matches) == 1:
            return matches[0]
        if default in ids:
            return default
        return named[0].id if len(named) == 1 else None
    if not card_suffix:
        return default
    matches = [a.id for a in accounts if card_suffix in a.card_digits]
    if len(matches) == 1:
        return matches[0]
    return default
