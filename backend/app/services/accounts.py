"""Which account an alert or notification belongs to."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.accounts import normalize_kind
from app.core.taxonomy import DEFAULT_PURPOSE, people
from app.db.models import Account

WORD = re.compile(r"[a-z0-9]+")


def names_issuer(card_name: str, account: Account) -> bool:
    """Whether a card name ("Wealthsimple VIP", "PC Financial Mastercard") is this account's bank."""
    word = account.institution.split()[0]
    return re.search(rf"\b{re.escape(word)}\b", card_name, re.IGNORECASE) is not None


def person_named(first_name: str | None) -> str | None:
    """The household person whose first name this is ("Sam" for "Sam Lee made a transfer"), if any."""
    if not first_name:
        return None
    matches = [p for p in people() if p.split()[0].lower() == first_name.lower()]
    return matches[0] if len(matches) == 1 else None


def account_named(db: Session, institution: str, shown: str, person: str | None = None) -> str | None:
    """The household's account at `institution` that a bank names `shown` ("TFSA", "Joint Chequing", "Savings"),
    for `person` when the move was theirs: matched on the words of its name, its kind and whose it is. None when
    nothing fits or two fit equally."""
    words = set(WORD.findall(shown.lower()))
    kinds = {k for w in words if (k := normalize_kind(w))}
    bank_words = set(WORD.findall(institution.lower()))
    best, best_score = [], 0
    for account in db.scalars(select(Account)):
        if not names_issuer(institution, account):
            continue
        own = set(WORD.findall(account.account_name.lower())) - bank_words
        score = 2 * len(words & own) + (2 if account.kind in kinds else 0)
        if "joint" in words:
            score += 2 if account.owner == DEFAULT_PURPOSE else -2
        elif person:
            # Their own account first, then a shared one; never someone else's.
            score += 2 if account.owner == person else -1 if account.owner == DEFAULT_PURPOSE else -4
        if score > best_score:
            best, best_score = [account.id], score
        elif score == best_score and score > 0:
            best.append(account.id)
    return best[0] if len(best) == 1 else None


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
