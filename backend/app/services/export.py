"""Statement export: every transaction in a date range, as a CSV or a bank-statement-style PDF."""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fpdf import FPDF
from fpdf.enums import TableCellFillMode
from fpdf.fonts import FontFace
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.taxonomy import budget_line
from app.db.models import Account, Transaction

# A statement this long is a backup, not a statement; keep a request from loading the whole history by accident.
MAX_DAYS = 366 * 5
CSV_COLUMNS = (
    "Date", "Time", "Description", "Account", "Type", "Category", "Who", "Budget line",
    "Debit", "Credit", "Counts as spending", "Note",
)
SPENDING_TYPES = ("PURCHASE", "REFUND")


@dataclass(frozen=True)
class StatementRow:
    when: datetime  # local time
    description: str
    account: str
    txn_type: str
    category: str
    who: str
    line: str
    debit: Decimal | None
    credit: Decimal | None
    counts: bool
    note: str


@dataclass(frozen=True)
class Statement:
    start: date
    end: date  # inclusive
    account: str | None
    rows: list[StatementRow]

    @property
    def money_out(self) -> Decimal:
        return sum((r.debit for r in self.rows if r.debit), Decimal(0))

    @property
    def money_in(self) -> Decimal:
        return sum((r.credit for r in self.rows if r.credit), Decimal(0))

    @property
    def spent(self) -> Decimal:
        """Net spending: purchases less refunds, leaving out income, transfers and anything excluded."""
        return sum(((r.debit or 0) - (r.credit or 0) for r in self.rows if r.counts), Decimal(0))

    @property
    def income(self) -> Decimal:
        return sum((r.credit or 0 for r in self.rows if r.txn_type == "INCOME"), Decimal(0))

    @property
    def filename_stem(self) -> str:
        return f"xpensimate_{self.start.isoformat()}_to_{self.end.isoformat()}"


def _split_categories(txn: Transaction) -> tuple[str, str, str]:
    """Category, who and budget line; an itemized charge lists each of its lines' values."""
    if not txn.is_itemized or not txn.items:
        return txn.category, txn.purpose, budget_line(txn.category, txn.purpose)
    join = lambda values: "; ".join(dict.fromkeys(values))  # noqa: E731 - unique, in order
    return (join(i.category for i in txn.items), join(i.purpose for i in txn.items),
            join(budget_line(i.category, i.purpose) for i in txn.items))


def build_statement(db: Session, start: date, end: date, tz: ZoneInfo, account_id: str | None = None) -> Statement:
    if end < start:
        raise ValueError("The end date is before the start date")
    if (end - start).days > MAX_DAYS:
        raise ValueError("Export at most five years at a time")
    lo = datetime.combine(start, time(0), tzinfo=tz)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz)
    query = (
        select(Transaction).where(Transaction.timestamp >= lo, Transaction.timestamp < hi)
        .options(selectinload(Transaction.items)).order_by(Transaction.timestamp, Transaction.id)
    )
    if account_id:
        query = query.where(Transaction.account_id == account_id)
    names = dict(db.execute(select(Account.id, Account.account_name)).all())

    rows = []
    for txn in db.scalars(query):
        amount = Decimal(txn.amount)
        # Money in: income and refunds. Everything else (purchases, transfers out) is money out.
        is_credit = txn.txn_type == "INCOME" or amount < 0
        category, who, line = _split_categories(txn)
        account = names.get(txn.account_id, txn.account_id) if txn.account_id else "Cash / other"
        if txn.txn_type == "TRANSFER" and txn.counterpart_account_id:
            account = f"{account} -> {names.get(txn.counterpart_account_id, txn.counterpart_account_id)}"
        rows.append(StatementRow(
            when=txn.timestamp.astimezone(tz),
            description=txn.merchant_normalized or txn.merchant_raw,
            account=account,
            txn_type=txn.txn_type,
            category=category,
            who=who,
            line=line,
            debit=None if is_credit else abs(amount),
            credit=abs(amount) if is_credit else None,
            counts=not txn.excluded and txn.txn_type in SPENDING_TYPES,
            note=txn.note or "",
        ))
    return Statement(start=start, end=end, account=names.get(account_id, account_id) if account_id else None,
                     rows=rows)


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def to_csv(statement: Statement) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for r in statement.rows:
        writer.writerow((
            r.when.date().isoformat(), r.when.strftime("%H:%M"), r.description, r.account, r.txn_type.title(),
            r.category, r.who, r.line, _money(r.debit), _money(r.credit), "Yes" if r.counts else "No", r.note,
        ))
    # A byte-order mark so Excel opens accented merchant names correctly.
    return ("﻿" + out.getvalue()).encode("utf-8")


def _latin1(text: str) -> str:
    """The PDF's built-in fonts only cover Latin-1; swap the few common characters outside it."""
    text = text.replace("→", "->").replace("–", "-").replace("—", "-").replace("’", "'").replace("“", '"')
    return text.replace("”", '"').replace("…", "...").encode("latin-1", "replace").decode("latin-1")


class _StatementPdf(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="landscape", format="letter")
        self.statement_title = title
        self.set_auto_page_break(auto=True, margin=14)
        self.set_margins(12, 12, 12)

    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Helvetica", size=8)
        self.set_text_color(120)
        self.cell(0, 5, _latin1(self.statement_title), align="L")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")


def to_pdf(statement: Statement, generated: datetime) -> bytes:
    period = f"{statement.start:%b %-d, %Y} to {statement.end:%b %-d, %Y}"
    pdf = _StatementPdf(f"Xpensimate statement · {period}")
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Xpensimate statement", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(90)
    scope = statement.account or "All accounts"
    pdf.cell(0, 5, _latin1(f"{period}  ·  {scope}  ·  generated {generated:%b %-d, %Y %-I:%M %p}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Summary boxes, like the top of a bank statement.
    totals = (("Transactions", str(len(statement.rows))), ("Money out", f"${statement.money_out:,.2f}"),
              ("Money in", f"${statement.money_in:,.2f}"), ("Net spending", f"${statement.spent:,.2f}"),
              ("Income", f"${statement.income:,.2f}"))
    width = (pdf.w - pdf.l_margin - pdf.r_margin) / len(totals)
    y = pdf.get_y()
    for i, (label, value) in enumerate(totals):
        x = pdf.l_margin + i * width
        pdf.set_fill_color(242, 244, 247)
        pdf.rect(x + 1, y, width - 2, 16, style="F")
        pdf.set_xy(x + 4, y + 2)
        pdf.set_font("Helvetica", size=8)
        pdf.set_text_color(100)
        pdf.cell(width - 8, 4, label.upper())
        pdf.set_xy(x + 4, y + 7)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20)
        pdf.cell(width - 8, 6, value)
    pdf.set_y(y + 20)
    pdf.set_font("Helvetica", size=8)
    pdf.set_text_color(110)
    pdf.multi_cell(0, 4, "Net spending is purchases less refunds; income, transfers between your own accounts and "
                         "charges marked not an expense are left out. Rows in grey don't count as spending.",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_text_color(20)
    if not statement.rows:
        pdf.set_font("Helvetica", size=11)
        pdf.cell(0, 10, "No transactions in this period.")
        return bytes(pdf.output())

    pdf.set_font("Helvetica", size=8.5)
    pdf.set_draw_color(220)
    day_format = "%b %-d" if statement.start.year == statement.end.year else "%b %-d, %Y"
    headings = ("Date", "Description", "Account", "Category", "Who", "Debit", "Credit")
    with pdf.table(
        col_widths=(22, 66, 48, 44, 24, 22, 22),
        text_align=("LEFT", "LEFT", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT"),
        cell_fill_color=(248, 249, 251), cell_fill_mode=TableCellFillMode.ROWS,
        line_height=5, borders_layout="HORIZONTAL_LINES", repeat_headings=1,
        headings_style=_heading_style(),
    ) as table:
        table.row(headings)
        for r in statement.rows:
            description = r.description + ("" if r.txn_type in SPENDING_TYPES else f"  [{r.txn_type.title()}]")
            description += f"\n{r.note}" if r.note else ""
            cells = (r.when.strftime(day_format), description, r.account, r.category, r.who,
                     _money(r.debit), _money(r.credit))
            table.row([_latin1(c) for c in cells], style=None if r.counts else FontFace(color=(130, 130, 130)))
        table.row(["", "Totals", "", "", "", f"{statement.money_out:,.2f}", f"{statement.money_in:,.2f}"],
                  style=_heading_style())
    return bytes(pdf.output())


def _heading_style() -> FontFace:
    return FontFace(emphasis="BOLD", fill_color=(230, 233, 238), color=(20, 20, 20))
