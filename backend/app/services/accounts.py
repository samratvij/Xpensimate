"""Which account an alert or notification belongs to."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account


def account_for_alert(db: Session, card_suffix: str | None, default: str | None) -> str | None:
    """The account whose card ends in `card_suffix`, else the parser's `default` (None if neither).

    The card number wins over the parser's guess, so a new or second card at a bank is a data change
    (set accounts.card_last4), not a parser change. If two accounts share those digits, the default
    breaks the tie when it is one of them; otherwise the alert falls back to the default.
    """
    # Parsers name a well-known account id (e.g. tangerine_mastercard); it only counts if the household has it.
    default = default if default and db.get(Account, default) is not None else None
    if not card_suffix:
        return default
    matches = list(db.scalars(select(Account.id).where(Account.card_last4 == card_suffix)))
    if len(matches) == 1:
        return matches[0]
    return default
