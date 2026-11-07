"""The household's own setup: who spending is for, how the categorizer should think about them, and their
accounts. Kept in household.toml (HOUSEHOLD_FILE), written by the setup wizard (PUT /api/v1/household) or
by hand (see household.example.toml), and re-read whenever the file changes.

Without the file the server runs in setup mode: only the shared purpose and no accounts, until the
household is saved.
"""
import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

ICONS = ("person", "abroad", "family")
SHARED = "Shared"
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "household.toml"
SHARED_GUIDE = "joint household spending (groceries, rent, utilities, eating out, household items). The default."


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


DEFAULT_CURRENCY = "CAD"


def check_currency(value: object, where: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise HouseholdConfigError(f"{where}: currency must be a 3-letter code such as CAD, USD, EUR or INR")
    return code


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
    # ISO 4217 code the apps show amounts in, and new charges get when their source doesn't say.
    currency: str = DEFAULT_CURRENCY


# The household before setup: shared spending only.
SETUP_HOUSEHOLD = Household(description="a household", purposes=(PurposeDef(SHARED, SHARED_GUIDE),), accounts=())

# Accounts the bank parsers fall back to when an alert has no card digits, offered by the setup wizard.
KNOWN_ACCOUNTS = (
    AccountDef("tangerine_mastercard", "Tangerine", "Tangerine Money-Back World Mastercard"),
    AccountDef("tangerine_cheq", "Tangerine", "Tangerine Chequing"),
    AccountDef("pc_mastercard", "PC Financial", "PC Financial Mastercard"),
    AccountDef("pc_money", "PC Financial", "PC Money Account"),
    AccountDef("ctfs_triangle", "Canadian Tire Financial Services", "Triangle Mastercard"),
    AccountDef("ws_credit", "Wealthsimple", "Wealthsimple Credit Card"),
    AccountDef("ws_cash", "Wealthsimple", "Wealthsimple Cash"),
)


def default_guide(name: str, icon: str) -> str:
    """What the categorizer is told about a purpose when the household didn't describe it."""
    if icon == "family":
        return f"spending for {name} (money sent to them, shopping or appointments for them)."
    if icon == "abroad":
        return "spending abroad (foreign merchants or currencies)."
    return (f"{name}'s own spending (their gym or classes, haircuts, health appointments, personal subscriptions, "
            "clothes).")


def _text(table: dict, key: str, where: str, required: bool = True) -> str | None:
    value = table.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise HouseholdConfigError(f"{where}: '{key}' is required")
        return None
    if not isinstance(value, str):
        raise HouseholdConfigError(f"{where}: '{key}' must be text")
    value = " ".join(value.split())
    if len(value) > (500 if key in ("description", "guide") else 64):
        raise HouseholdConfigError(f"{where}: '{key}' is too long")
    return value


def parse(data: dict) -> Household:
    description = _text(data, "description", "household")
    currency = check_currency(data.get("currency") or DEFAULT_CURRENCY, "currency")
    purposes = []
    for i, p in enumerate(data.get("purposes", [])):
        where = f"purposes[{i}]"
        icon = p.get("icon", "person")
        if icon not in ICONS:
            raise HouseholdConfigError(f"{where}: icon must be one of {', '.join(ICONS)}")
        name = _text(p, "name", where)
        if "/" in name:
            raise HouseholdConfigError(f"{where}: a name can't contain '/'")
        purposes.append(PurposeDef(
            name=name,
            guide=_text(p, "guide", where, required=False) or (SHARED_GUIDE if i == 0 else default_guide(name, icon)),
            line=_text(p, "budget_line", where, required=False),
            transfer_line=_text(p, "transfer_budget_line", where, required=False),
            icon=icon,
        ))
    if not purposes:
        raise HouseholdConfigError("household needs at least one purpose")
    if purposes[0].name != SHARED or purposes[0].line is not None:
        raise HouseholdConfigError(f"the first purpose must be '{SHARED}', with no budget_line (it's budgeted by category)")
    if any(not p.line for p in purposes[1:]):
        raise HouseholdConfigError("every purpose after the first needs a budget_line")
    names = [p.name for p in purposes]
    lines = [line for p in purposes for line in (p.line, p.transfer_line) if line]
    if len(set(names)) != len(names):
        raise HouseholdConfigError("purpose names must be different")
    if len(set(lines)) != len(lines):
        raise HouseholdConfigError("budget lines must be different")

    accounts = []
    for i, a in enumerate(data.get("accounts", [])):
        where = f"accounts[{i}]"
        last4 = a.get("card_last4") or None
        if last4 is not None and not (isinstance(last4, str) and len(last4) == 4 and last4.isdigit()):
            raise HouseholdConfigError(f'{where}: card_last4 must be four digits in quotes, e.g. "1234"')
        account_id = _text(a, "id", where)
        if not all(c.isalnum() or c == "_" for c in account_id):
            raise HouseholdConfigError(f"{where}: an account id can only have letters, digits and _")
        accounts.append(AccountDef(
            id=account_id,
            institution=_text(a, "institution", where),
            name=_text(a, "name", where),
            currency=check_currency(a.get("currency") or currency, f"{where}.currency"),
            card_last4=last4,
        ))
    if len({a.id for a in accounts}) != len(accounts):
        raise HouseholdConfigError("account ids must be different")
    return Household(description=description, purposes=tuple(purposes), accounts=tuple(accounts), currency=currency)


def household_path() -> Path:
    return Path(os.environ.get("HOUSEHOLD_FILE") or DEFAULT_FILE)


_cache: tuple[Path, tuple[int, int], Household] | None = None


def current_household() -> Household | None:
    """The saved household, re-read when the file changes; None before setup."""
    global _cache
    path = household_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    version = (stat.st_mtime_ns, stat.st_size)
    if _cache and _cache[0] == path and _cache[1] == version:
        return _cache[2]
    with path.open("rb") as f:
        try:
            household = parse(tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            raise HouseholdConfigError(f"{path}: {e}") from e
    _cache = (path, version, household)
    return household


def load_household() -> Household:
    """The household to work with: the saved one, or the setup-mode household before there is one."""
    return current_household() or SETUP_HOUSEHOLD


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_toml(household: Household) -> str:
    out = [
        "# Your household. Written by Xpensimate's setup (web app → More → Household); you can also edit it by hand",
        "# (see household.example.toml for what each field means). Changes saved in the app apply right away; by",
        "# hand, people apply right away and accounts on the next restart (docker compose restart api).",
        "",
        f"description = {_toml_string(household.description)}",
        f"currency = {_toml_string(household.currency)}",
    ]
    for p in household.purposes:
        out += ["", "[[purposes]]", f"name = {_toml_string(p.name)}", f"guide = {_toml_string(p.guide)}"]
        if p.line:
            out.append(f"budget_line = {_toml_string(p.line)}")
        if p.transfer_line:
            out.append(f"transfer_budget_line = {_toml_string(p.transfer_line)}")
        if p.icon != "person":
            out.append(f"icon = {_toml_string(p.icon)}")
    for a in household.accounts:
        out += ["", "[[accounts]]", f"id = {_toml_string(a.id)}", f"institution = {_toml_string(a.institution)}",
                f"name = {_toml_string(a.name)}"]
        if a.currency != household.currency:
            out.append(f"currency = {_toml_string(a.currency)}")
        if a.card_last4:
            out.append(f"card_last4 = {_toml_string(a.card_last4)}")
    return "\n".join(out) + "\n"


def to_dict(household: Household) -> dict:
    """The same shape parse() reads (and the API speaks)."""
    return {
        "description": household.description,
        "currency": household.currency,
        "purposes": [
            {"name": p.name, "guide": p.guide, "budget_line": p.line, "transfer_budget_line": p.transfer_line,
             "icon": p.icon}
            for p in household.purposes
        ],
        "accounts": [asdict(a) for a in household.accounts],
    }


def save_household(household: Household) -> None:
    """Write household.toml atomically (it's re-read on its next use)."""
    path = household_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(to_toml(household))
    os.chmod(tmp, 0o600)
    # The server often runs as root in Docker; hand the file to whoever owns the folder, so it stays editable.
    folder = path.parent.stat()
    try:
        os.chown(tmp, folder.st_uid, folder.st_gid)
    except PermissionError:
        pass
    os.replace(tmp, path)
