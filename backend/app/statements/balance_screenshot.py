"""Screenshots of a bank app's accounts (an overview, or one account's page), read by the AI model into balances.

Nothing is saved from a reading: the app shows what was read, matched to the household's accounts, and the person
confirms (and can change the account) before the balances are recorded (app/api/v1/balances.py).
"""
import logging
from typing import Literal

from pydantic import BaseModel, create_model

from app.core.config import get_settings
from app.db.models import Account
from app.core.household import load_household
from app.services.llm import LlmError, ask
from app.services.ocr_service import OcrError, normalize_image

log = logging.getLogger(__name__)

UNKNOWN_ACCOUNT = "unknown"


def system_prompt(accounts: list[Account], owner: str | None) -> str:
    listed = "\n".join(
        f'- "{a.id}": {a.institution} {a.account_name} ({a.kind.replace("_", " ")}, {a.owner}'
        + (f", ending {' or '.join(a.card_digits)}" if a.card_digits else "") + ")"
        for a in accounts
    )
    whose = (f"The screenshot is from {owner}'s phone, so an account shown without a name is {owner}'s "
             f"(or a joint one).") if owner else ""
    household = load_household()
    return f"""You read screenshots of banking and investing apps that show account balances, for a household
that is {household.description}: an overview listing several accounts, or the page of a single account.

Return one row per account whose balance is fully visible:
- shown_name is the account's name as shown (with any digits shown, e.g. "Savings ***4541").
- balance is the balance shown, as a positive number. For a credit card or line of credit it is the amount
  owed; set owed to true for those, false otherwise. For an investment account use its total value (not
  "available to trade", gains, or a past-month change).
- currency is the currency shown ({household.currency} unless the screen says otherwise).
- account_id is the household's account it is, from the list below, matched by the bank's look, the
  account's name and type, and any digits shown; "{UNKNOWN_ACCOUNT}" when no account fits or two fit equally.
{whose}
{listed}

Skip promotions, products not opened (e.g. "Open an account", a "+" to add one), points balances and totals that
add up several accounts. If the image shows no account balances, set shows_balances to false and return no rows."""


class BalanceRow(BaseModel):
    shown_name: str
    balance: float
    owed: bool
    currency: str
    account_id: str


class BalanceRead(BaseModel):
    shows_balances: bool
    rows: list[BalanceRow]


def output_model(account_ids: list[str]) -> type[BaseModel]:
    row = create_model("BalanceRow", __base__=BalanceRow,
                       account_id=(Literal[tuple([*account_ids, UNKNOWN_ACCOUNT])], ...))
    return create_model("BalanceRead", __base__=BalanceRead, rows=(list[row], ...))


def read_balances(jpeg: bytes, accounts: list[Account], owner: str | None) -> BalanceRead:
    try:
        answer = ask(model=get_settings().screenshot_reader(), system=system_prompt(accounts, owner),
                     text="List the account balances on this screen.", image_jpeg=jpeg,
                     schema=output_model([a.id for a in accounts]), max_tokens=4000, effort="low")
    except LlmError as e:
        log.warning("Balance screenshot reading failed: %s", e)
        raise OcrError(str(e)) from e
    return BalanceRead.model_validate(answer.model_dump())


def parse_balance_screenshot(content: bytes, accounts: list[Account], owner: str | None) -> BalanceRead:
    try:
        jpeg = normalize_image(content)
    except ValueError as e:
        raise ValueError("The image couldn't be read") from e
    read = read_balances(jpeg, accounts, owner)
    if not read.shows_balances or not read.rows:
        raise ValueError("No account balances found in this screenshot")
    return read
