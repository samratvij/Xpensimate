"""Trends across months and the month-end review (the "reflection" half of budgeting)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import Account, BudgetEnvelope, Receipt, StatementImport, Transaction
from app.schemas import AccountCoverage, MerchantTotal, MonthTotal, ReviewLine, ReviewOut
from app.services.merchant import merchant_key
from app.services.recurring import expected_spend
from app.services.spending import month_bounds, spend_by

router = APIRouter(tags=["insights"])

ZERO = Decimal("0.00")
TOP_MERCHANTS = 8
LOW_CONFIDENCE = Decimal("0.60")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _shift(month: str, by: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + by
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _bounds(month: str):
    try:
        return month_bounds(month, _tz())
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("/trends", response_model=list[MonthTotal])
def trends(
    months: int = Query(12, ge=1, le=36), purpose: str | None = None, db: Session = Depends(get_db)
) -> list[MonthTotal]:
    """Spending per month (oldest first) up to the current month, with each month's budget-line totals."""
    tz = _tz()
    today = datetime.now(tz)
    current = today.strftime("%Y-%m")
    out = []
    for i in range(months - 1, -1, -1):
        month = _shift(current, -i)
        start, end = _bounds(month)
        lines = spend_by(db, start, end, purpose, "budget_line")
        expected = expected_spend(db, start.date(), end.date(), today.date(), "budget_line", purpose) if i == 0 else {}
        out.append(MonthTotal(month=month, spent=sum(lines.values(), ZERO), expected=sum(expected.values(), ZERO),
                              by_line=lines))
    return out


@router.get("/review", response_model=ReviewOut)
def review(month: str | None = None, db: Session = Depends(get_db)) -> ReviewOut:
    tz = _tz()
    month = month or datetime.now(tz).strftime("%Y-%m")
    start, end = _bounds(month)

    spent = spend_by(db, start, end, None, "budget_line")
    previous = spend_by(db, *_bounds(_shift(month, -1)), None, "budget_line")
    last3 = [spend_by(db, *_bounds(_shift(month, -k)), None, "budget_line") for k in (1, 2, 3)]
    budgets = {b.category: b.monthly_limit for b in db.scalars(select(BudgetEnvelope))}

    def avg3(line: str) -> Decimal:
        return (sum((m.get(line, ZERO) for m in last3), ZERO) / 3).quantize(Decimal("0.01"))

    names = sorted(spent.keys() | budgets.keys(), key=lambda n: -(spent.get(n, ZERO)))
    lines = [ReviewLine(budget_line=n, spent=spent.get(n, ZERO), monthly_limit=budgets.get(n),
                        previous=previous.get(n, ZERO), average_3m=avg3(n)) for n in names]

    in_month = (Transaction.timestamp >= start, Transaction.timestamp < end, Transaction.excluded.is_(False))
    needs_check = list(db.scalars(
        select(Transaction).where(
            *in_month,
            Transaction.category_is_manual.is_(False),
            Transaction.purpose_is_manual.is_(False),
            or_(Transaction.categorized_by == "none",
                (Transaction.categorized_by == "llm") & (Transaction.inference_confidence < LOW_CONFIDENCE)),
        ).order_by(Transaction.amount.desc())
    ))

    per_account = {
        account_id: (count, total)
        for account_id, count, total in db.execute(
            select(Transaction.account_id, func.count(), func.sum(Transaction.amount)).where(*in_month)
            .group_by(Transaction.account_id)
        )
    }
    imports = list(db.scalars(
        select(StatementImport).where(
            StatementImport.status == "done",
            StatementImport.period_start <= end.date(),
            StatementImport.period_end >= start.date(),
        )
    ))
    accounts = []
    for account in db.scalars(select(Account).order_by(Account.institution, Account.account_name)):
        count, total = per_account.get(account.id, (0, ZERO))
        covering = [i for i in imports if i.account_id == account.id]
        if not count and not covering:
            continue
        period = None
        if covering:
            period = f"{min(i.period_start for i in covering)} to {max(i.period_end for i in covering)}"
        accounts.append(AccountCoverage(account_id=account.id, institution=account.institution,
                                        account_name=account.account_name, transactions=count, spent=total or ZERO,
                                        statement_imported=bool(covering), statement_period=period))

    merchants: dict[str, list] = {}
    for txn in db.scalars(select(Transaction).where(*in_month, Transaction.amount > 0)):
        entry = merchants.setdefault(merchant_key(txn.merchant_raw), [ZERO, 0])
        entry[0] += txn.amount
        entry[1] += 1
    top = sorted(merchants.items(), key=lambda kv: -kv[1][0])[:TOP_MERCHANTS]

    unmatched = db.scalar(select(func.count()).select_from(Receipt).where(
        Receipt.matched_transaction_id.is_(None), Receipt.timestamp >= start, Receipt.timestamp < end))

    return ReviewOut(
        month=month,
        total_spent=sum(spent.values(), ZERO),
        previous_total=sum(previous.values(), ZERO),
        average_3m=(sum((sum(m.values(), ZERO) for m in last3), ZERO) / 3).quantize(Decimal("0.01")),
        total_budget=sum(budgets.values(), ZERO),
        lines=lines,
        needs_check=needs_check,
        accounts=accounts,
        top_merchants=[MerchantTotal(merchant=m, spent=v[0], count=v[1]) for m, v in top],
        unmatched_receipts=unmatched,
    )
