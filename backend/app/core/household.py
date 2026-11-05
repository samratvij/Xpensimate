"""The household's own setup: who spending is for, how the categorizer should think about them, and their
accounts. Read once at startup from household.toml (HOUSEHOLD_FILE); see household.example.toml.
"""
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ICONS = ("person", "abroad", "family")
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "household.toml"


class HouseholdConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PurposeDef:
    """One "who it was for"."""

    name: str
    # How the categorizer recognises it (finishes the sentence '- "<name>": ...').
    guide: str
    # The budget line its spending counts against; None means budgeted by category (shared spending).
    line: str | None = None
    # When set, money sent to them (category Transfer) is budgeted on this line instead of `line`.
    transfer_line: str | None = None
    # How the apps draw its budget lines: person | abroad | family.
    icon: str = "person"


@dataclass(frozen=True)
class AccountDef:
    id: str
    institution: str
    name: str
    currency: str = "CAD"
    # Last four digits of the card, as bank alerts and Google Wallet show them; picks the account for an alert.
    card_last4: str | None = None


@dataclass(frozen=True)
class Household:
    # Who the household is, for the categorizer prompt ("two people in Toronto, Canada, ...").
    description: str
    purposes: tuple[PurposeDef, ...]
    accounts: tuple[AccountDef, ...]


def _require(table: dict, key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HouseholdConfigError(f"{where}: '{key}' is required")
    return value.strip()


def parse(data: dict) -> Household:
    description = _require(data, "description", "household")
    purposes = []
    for i, p in enumerate(data.get("purposes", [])):
        where = f"purposes[{i}]"
        icon = p.get("icon", "person")
        if icon not in ICONS:
            raise HouseholdConfigError(f"{where}: icon must be one of {', '.join(ICONS)}")
        purposes.append(PurposeDef(
            name=_require(p, "name", where),
            guide=_require(p, "guide", where),
            line=p.get("budget_line") or None,
            transfer_line=p.get("transfer_budget_line") or None,
            icon=icon,
        ))
    if not purposes:
        raise HouseholdConfigError("household needs at least one purpose")
    if purposes[0].line is not None:
        raise HouseholdConfigError("the first purpose is the shared default and can't have a budget_line")
    names = [p.name for p in purposes]
    lines = [line for p in purposes for line in (p.line, p.transfer_line) if line]
    if len(set(names)) != len(names):
        raise HouseholdConfigError("purpose names must be different")
    if len(set(lines)) != len(lines):
        raise HouseholdConfigError("budget lines must be different")
    if any(p.transfer_line and not p.line for p in purposes):
        raise HouseholdConfigError("transfer_budget_line needs a budget_line too")

    accounts = []
    for i, a in enumerate(data.get("accounts", [])):
        where = f"accounts[{i}]"
        last4 = a.get("card_last4")
        if last4 is not None and not (isinstance(last4, str) and len(last4) == 4 and last4.isdigit()):
            raise HouseholdConfigError(f'{where}: card_last4 must be four digits in quotes, e.g. "1234"')
        accounts.append(AccountDef(
            id=_require(a, "id", where),
            institution=_require(a, "institution", where),
            name=_require(a, "name", where),
            currency=a.get("currency", "CAD"),
            card_last4=last4,
        ))
    if len({a.id for a in accounts}) != len(accounts):
        raise HouseholdConfigError("account ids must be different")
    return Household(description=description, purposes=tuple(purposes), accounts=tuple(accounts))


@lru_cache
def load_household() -> Household:
    path = Path(os.environ.get("HOUSEHOLD_FILE") or DEFAULT_FILE)
    if not path.exists():
        raise HouseholdConfigError(
            f"{path} not found: copy household.example.toml to household.toml and describe your household"
        )
    with path.open("rb") as f:
        try:
            return parse(tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            raise HouseholdConfigError(f"{path}: {e}") from e
