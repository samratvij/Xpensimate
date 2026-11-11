"""Screenshots of a bank app's transaction list, read by the AI model.

A screenshot is a window onto an account, not a statement: it may be filtered, scrolled or cut off, so it
never counts as covering a month, and its rows are stored with their own source ("screenshot"). A full
statement imported later then sees them as charges the app already has, rather than adding them twice.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, create_model

from app.core.config import get_settings
from app.db.models import Account
from app.core.household import load_household
from app.services.llm import LlmError, ask
from app.services.ocr_service import OcrError, normalize_image
from app.statements.parsers import DEPOSIT, TRANSFER_OUT, INTEREST, PAYMENT, PURCHASE, REFUND, ParsedStatement, StatementRow, is_payment_reversal

log = logging.getLogger(__name__)

SCREENSHOT = "screenshot"
UNKNOWN_ACCOUNT = "unknown"
_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF8")


def is_image(content: bytes) -> bool:
    return content.startswith(_SIGNATURES) or (content[:4] == b"RIFF" and content[8:12] == b"WEBP")


def system_prompt(accounts: list[Account], today: date) -> str:
    listed = "\n".join(
        f'- "{a.id}": {a.institution} {a.account_name}'
        + (f" (card ending {' or '.join(a.card_digits)})" if a.card_digits else "")
        for a in accounts
    )
    household = load_household()
    return f"""You read screenshots of a banking app's list of transactions for one account, for a household that
is {household.description}. Amounts are usually in {household.currency}.

Return every transaction that is fully visible, top to bottom:
- date is YYYY-MM-DD. Today is {today.isoformat()}. When the screen shows no year, or says "Today" or
  "Yesterday", use the most recent such date that is not after today.
- merchant is the transaction's name exactly as shown (keep store numbers and prefixes such as "SQ *").
  Leave out the city or location line under it.
- amount is the amount shown, as a positive number, whatever sign or colour the app uses.
- kind: "purchase" for spending; "refund" for money returned by a merchant; "payment" (credit card
  screens only) for a payment towards the card; "deposit" for money coming into a bank account (pay or
  payroll, a direct deposit, an e-transfer received, a transfer in); "transfer" for money sent out of a
  bank account that isn't a purchase (an e-transfer sent, a transfer to another account, a credit card
  payment made from it, a contribution to an investment account); "interest" for interest or fees charged.
- pending is true only when the app marks the row as pending or authorized (not yet posted).
- Skip rows cut off at the edge of the screen, balances, totals, headers and anything that is not a
  transaction.

account_id is the household's account the screen belongs to, chosen from the list below by the bank's
look and name and any card digits shown, or "{UNKNOWN_ACCOUNT}" when it can't be told apart from another
account at the same bank.
{listed}

If the image is not a list of bank transactions, set is_transaction_list to false and return no rows."""


class ScreenshotRow(BaseModel):
    date: str
    merchant: str
    amount: float
    kind: Literal["purchase", "refund", "payment", "interest", "deposit", "transfer"]
    pending: bool


class ScreenshotRead(BaseModel):
    is_transaction_list: bool
    account_id: str
    rows: list[ScreenshotRow]


def output_model(account_ids: list[str]) -> type[BaseModel]:
    """The answer schema with account_id limited to the accounts that exist."""
    return create_model("ScreenshotRead", __base__=ScreenshotRead,
                        account_id=(Literal[tuple([*account_ids, UNKNOWN_ACCOUNT])], ...))


def read_screenshot(jpeg: bytes, accounts: list[Account], today: date) -> ScreenshotRead:
    try:
        answer = ask(model=get_settings().screenshot_reader(), system=system_prompt(accounts, today),
                     text="List the transactions on this screen.", image_jpeg=jpeg,
                     schema=output_model([a.id for a in accounts]), max_tokens=8000, effort="low")
    except LlmError as e:
        log.warning("Screenshot reading failed: %s", e)
        raise OcrError(str(e)) from e
    return ScreenshotRead.model_validate(answer.model_dump())


def to_statement(read: ScreenshotRead, today: date) -> ParsedStatement:
    """Rows in the statement shape (spending positive). Pending rows are kept and flagged."""
    if not read.is_transaction_list:
        raise ValueError("This image doesn't look like a list of bank transactions")
    out = ParsedStatement(SCREENSHOT, "" if read.account_id == UNKNOWN_ACCOUNT else read.account_id)
    signs = {PURCHASE: 1, INTEREST: 1, TRANSFER_OUT: 1, REFUND: -1, PAYMENT: -1, DEPOSIT: -1}
    for row in read.rows:
        try:
            day = date.fromisoformat(row.date)
        except ValueError:
            continue
        if day > today + timedelta(days=1) or not row.merchant.strip():
            continue
        amount = abs(Decimal(str(row.amount)).quantize(Decimal("0.01")))
        if is_payment_reversal(row.merchant):  # a bounced payment: owed again, so positive
            kind = PAYMENT
        else:
            kind, amount = row.kind, amount * signs[row.kind]
        out.rows.append(StatementRow(index=len(out.rows), spent_on=day, merchant=row.merchant.strip(),
                                     amount=amount, kind=kind, pending=row.pending))
    if not out.rows:
        raise ValueError("No transactions found in this screenshot")
    return out


def parse_screenshot(content: bytes, accounts: list[Account], today: date) -> ParsedStatement:
    try:
        jpeg = normalize_image(content)
    except ValueError as e:
        raise ValueError("The image couldn't be read") from e
    return to_statement(read_screenshot(jpeg, accounts, today), today)
