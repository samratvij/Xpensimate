"""RBC credit card statements, which RBC only offers as PDF.

The activity table sits in the left part of the page with an information panel to its right, so each page
is cropped before its text is read. Rows look like
    SEP 15 SEP 16 AMZN MKTP CA*5L8KA77S0 866-216-1072 ON $40.67
    74537886258401717306805                      <- RBC's reference for the row
with no year: it comes from "STATEMENT FROM SEP 16 TO OCT 15, 2026".
"""
import io
import re
from datetime import date
from decimal import Decimal

import pdfplumber

from app.statements.parsers import (
    INTEREST,
    PAYMENT,
    PURCHASE,
    REFUND,
    ParsedStatement,
    StatementRow,
    is_payment_reversal,
)

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
ACTIVITY_COLUMN = 0.6  # share of the page width that holds the activity table

_PERIOD = re.compile(r"STATEMENT FROM ([A-Z]{3}) (\d{1,2})(?:, (\d{4}))? TO ([A-Z]{3}) (\d{1,2}), (\d{4})")
_ROW_START = re.compile(r"^([A-Z]{3}) (\d{1,2}) ([A-Z]{3}) (\d{1,2}) (.*)$")
_AMOUNT = re.compile(r"(-?)\$([\d,]+\.\d{2})")
_REF = re.compile(r"\b\d{15,}\b")
# Lines that end the activity table (or a cardholder's part of it).
_TABLE_END = re.compile(r"^(TOTAL ACCOUNT BALANCE|SUBTOTAL|NEW BALANCE|PREVIOUS ACCOUNT BALANCE)")


def _row(index: int, start: re.Match, extra: list[str], end_month: int, end_year: int) -> StatementRow | None:
    text = " ".join([start[5], *extra])
    amounts = list(_AMOUNT.finditer(text))
    if not amounts:
        return None
    amount_match = amounts[-1]
    amount = Decimal(amount_match[2].replace(",", "")) * (-1 if amount_match[1] else 1)
    ref = _REF.search(text)
    description = (text[: amount_match.start()] + text[amount_match.end():])
    description = re.sub(r"\s+", " ", _REF.sub(" ", description)).strip()

    month = MONTHS[start[1]]
    # A December purchase on a statement ending in January belongs to the year before.
    year = end_year - 1 if month > end_month else end_year
    upper = description.upper()
    if amount < 0 or is_payment_reversal(description):
        kind = PAYMENT if "PAYMENT" in upper else REFUND
    else:
        kind = INTEREST if "INTEREST" in upper else PURCHASE
    return StatementRow(index=index, spent_on=date(year, month, int(start[2])), merchant=description, amount=amount,
                        kind=kind, ref=ref[0] if ref else None)


def parse_rbc_text(text: str) -> ParsedStatement:
    period = _PERIOD.search(text)
    if period is None:
        raise ValueError("Couldn't find the statement period in this RBC statement")
    end_month, end_year = MONTHS[period[4]], int(period[6])

    out = ParsedStatement("rbc", "rbc_visa")
    start: re.Match | None = None
    extra: list[str] = []

    def flush():
        if start is not None and (row := _row(len(out.rows), start, extra, end_month, end_year)):
            out.rows.append(row)

    for line in (l.strip() for l in text.splitlines()):
        if (match := _ROW_START.match(line)) and match[1] in MONTHS and match[3] in MONTHS:
            flush()
            start, extra = match, []
        elif start is not None and _TABLE_END.match(line):
            flush()
            start, extra = None, []
        elif start is not None:
            extra.append(line)
    flush()
    return out


def parse_pdf_statement(content: bytes) -> ParsedStatement:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
            if "RBC" not in first and "Royal Bank" not in first:
                raise ValueError("Only RBC credit card PDF statements are supported; use a CSV export for other banks.")
            text = "\n".join(
                page.crop((0, 0, page.width * ACTIVITY_COLUMN, page.height)).extract_text() or "" for page in pdf.pages
            )
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - any parser failure means "not a readable PDF"
        raise ValueError("The PDF couldn't be read") from e
    parsed = parse_rbc_text(text)
    if not parsed.rows:
        raise ValueError("No transactions found in this RBC statement")
    return parsed
