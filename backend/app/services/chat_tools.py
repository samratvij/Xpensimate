"""The tools the chat assistant uses to read and change the household's data.

Read tools wrap the same services the apps use, so the assistant's numbers match the screens. Write tools call
the API's own endpoint functions, so a change made from chat is validated, recorded (edited_by) and re-checked
against budgets exactly like one made in the app. Every result is compact JSON: it goes back to the model.
"""
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.v1 import budgets as budgets_api
from app.api.v1 import insights as insights_api
from app.api.v1 import recurring as recurring_api
from app.api.v1 import balances as balances_api
from app.api.v1 import goals as goals_api
from app.api.v1 import summary as summary_api
from app.api.v1 import transactions as transactions_api
from app.core.money import money
from app.core.taxonomy import DEFAULT_PURPOSE, budget_line, budget_line_condition
from app.db.models import BudgetEnvelope, ChatMemory, Receipt, ReceiptItem, Transaction, TransactionItem
from app.schemas import (
    NOTE_MAX,
    Purpose,
    BudgetIn,
    InsightOut,
    MarkPaidIn,
    RecurringIn,
    RecurringPatch,
    ReviewOut,
    SummaryOut,
    TransactionPatch,
)

MAX_TRANSACTIONS = 100
MAX_LIST = 40  # longest list of rows any other tool returns
MAX_MEMORIES = 50
Month = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; omit for the current month")


@dataclass
class ToolContext:
    db: Session
    user: str
    tz: ZoneInfo
    # Changes made this turn, shown under the answer: {"tool": ..., "summary": ...}.
    actions: list[dict[str, str]] = field(default_factory=list)

    @property
    def today(self) -> date:
        return datetime.now(self.tz).date()


class ToolError(Exception):
    """A problem the model can fix (bad id, unknown category...): returned to it as an error result."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    run: Callable[[ToolContext, Any], Any]
    writes: bool = False


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _local_start(day: date | None, tz: ZoneInfo) -> datetime | None:
    return datetime.combine(day, time.min, tzinfo=tz) if day else None


def _txn_row(txn: Transaction, tz: ZoneInfo) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(txn.id),
        "date": txn.timestamp.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "merchant": txn.merchant_normalized or txn.merchant_raw,
        "amount": float(txn.amount),
        "category": txn.category,
        "purpose": txn.purpose,
        "budget_line": budget_line(txn.category, txn.purpose),
        "account": txn.account_id,
    }
    if txn.note:
        row["note"] = txn.note
    if txn.excluded:
        row["not_an_expense"] = True
    if txn.is_itemized and getattr(txn, "items", None):  # review rows are schemas without items
        row["items"] = [
            {"name": i.sku_name, "price": float(i.price), "budget_line": budget_line(i.category, i.purpose)}
            | ({"note": i.note} if i.note else {})
            for i in txn.items
        ]
    return row


# ---- Read tools ---------------------------------------------------------------------------------------------


class MonthArgs(BaseModel):
    month: str | None = Month


def month_summary(ctx: ToolContext, args: MonthArgs) -> dict:
    summary: SummaryOut = summary_api.get_summary(month=args.month, purpose=None, group_by="budget_line", db=ctx.db)
    out = summary.model_dump(mode="json", exclude_none=True)
    for row in out["categories"]:
        for flag in ("over_warning", "over_limit", "projected_over_limit"):
            if not row.get(flag):
                row.pop(flag, None)  # keep the result short: only the flags that are set
    return out


class TrendArgs(BaseModel):
    months: int = Field(12, ge=1, le=36, description="How many months back, including the current one")
    purpose: Purpose | None = Field(None, description="Only spending for this person / purpose")


def spending_trend(ctx: ToolContext, args: TrendArgs) -> list:
    rows = insights_api.trends(months=args.months, purpose=args.purpose, db=ctx.db)
    return [r.model_dump(mode="json") for r in rows]


def month_review(ctx: ToolContext, args: MonthArgs) -> dict:
    review: ReviewOut = insights_api.review(month=args.month, db=ctx.db)
    out = review.model_dump(mode="json")
    out["needs_check"] = [_txn_row(t, ctx.tz) for t in review.needs_check[:MAX_LIST]]
    return out


class SearchArgs(BaseModel):
    text: str | None = Field(None, max_length=100, description="Matches merchant names and notes (case-insensitive)")
    start_date: date | None = Field(None, description="First day included (local date)")
    end_date: date | None = Field(None, description="Last day included (local date)")
    category: str | None = None
    purpose: Purpose | None = None
    budget_line: str | None = Field(None, description="Only what counts toward this budget line")
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    include_not_expenses: bool = Field(False, description="Also card payments and transfers between own accounts")
    limit: int = Field(25, ge=1, le=MAX_TRANSACTIONS, description="Rows to list, newest first (totals cover all)")


def search_transactions(ctx: ToolContext, args: SearchArgs) -> dict:
    conditions = []
    if args.text:
        like = f"%{args.text}%"
        conditions.append(or_(Transaction.merchant_raw.ilike(like), Transaction.merchant_normalized.ilike(like),
                              Transaction.note.ilike(like)))
    if args.start_date:
        conditions.append(Transaction.timestamp >= _local_start(args.start_date, ctx.tz))
    if args.end_date:
        conditions.append(Transaction.timestamp < _local_start(args.end_date + timedelta(days=1), ctx.tz))
    if args.category:
        conditions.append(Transaction.category == args.category)
    if args.purpose:
        conditions.append(Transaction.purpose == args.purpose)
    if args.min_amount is not None:
        conditions.append(Transaction.amount >= args.min_amount)
    if args.max_amount is not None:
        conditions.append(Transaction.amount <= args.max_amount)
    if args.budget_line:
        item_in_line = exists().where(
            TransactionItem.transaction_id == Transaction.id,
            budget_line_condition(args.budget_line, TransactionItem.category, TransactionItem.purpose),
        )
        conditions.append(or_(
            and_(Transaction.is_itemized.is_(False),
                 budget_line_condition(args.budget_line, Transaction.category, Transaction.purpose)),
            and_(Transaction.is_itemized.is_(True), item_in_line),
        ))
    if not args.include_not_expenses or args.budget_line:
        conditions.append(Transaction.excluded.is_(False))

    count, total = ctx.db.execute(select(func.count(), func.sum(Transaction.amount)).where(*conditions)).one()
    rows = ctx.db.scalars(
        select(Transaction).where(*conditions).options(selectinload(Transaction.items))
        .order_by(Transaction.timestamp.desc(), Transaction.id).limit(args.limit)
    )
    return {
        "matching": count,
        # Whole charges; for a budget line, itemized charges are counted in full here (see month_summary for
        # exact line totals).
        "total": _money(total or Decimal("0")),
        "listed": [_txn_row(t, ctx.tz) for t in rows],
    }


class ItemSearchArgs(BaseModel):
    text: str = Field(min_length=2, max_length=100, description="Matches receipt line names, e.g. 'milk'")
    start_date: date | None = None
    end_date: date | None = None


def search_receipt_items(ctx: ToolContext, args: ItemSearchArgs) -> dict:
    like = f"%{args.text}%"
    start = _local_start(args.start_date, ctx.tz)
    end = _local_start(args.end_date + timedelta(days=1), ctx.tz) if args.end_date else None

    matched = select(TransactionItem, Transaction).join(Transaction).where(
        TransactionItem.sku_name.ilike(like), Transaction.excluded.is_(False))
    if start:
        matched = matched.where(Transaction.timestamp >= start)
    if end:
        matched = matched.where(Transaction.timestamp < end)
    # Scanned receipts not matched to a card charge yet still say what was bought.
    unmatched = select(ReceiptItem, Receipt).join(Receipt).where(
        ReceiptItem.sku_name.ilike(like), Receipt.matched_transaction_id.is_(None))
    if start:
        unmatched = unmatched.where(Receipt.timestamp >= start)
    if end:
        unmatched = unmatched.where(Receipt.timestamp < end)

    lines = [
        (txn.timestamp, txn.merchant_normalized or txn.merchant_raw, item)
        for item, txn in ctx.db.execute(matched)
    ] + [(receipt.timestamp, receipt.merchant, item) for item, receipt in ctx.db.execute(unmatched)]
    lines.sort(key=lambda line: line[0], reverse=True)
    return {
        "matching": len(lines),
        "total": float(sum((item.price for _, _, item in lines), Decimal("0"))),
        "listed": [
            {"date": ts.astimezone(ctx.tz).strftime("%Y-%m-%d"), "store": store, "name": item.sku_name,
             "price": float(item.price), "quantity": float(item.quantity)}
            for ts, store, item in lines[:MAX_LIST]
        ],
    }


def bills(ctx: ToolContext, args: MonthArgs) -> dict:
    tracked = recurring_api.list_recurring(db=ctx.db)
    due = recurring_api.expected(month=args.month, db=ctx.db)
    return {
        "bills": [
            {"id": str(b.id), "name": b.name, "amount": float(b.amount), "cadence": b.cadence,
             "anchor_date": b.anchor_date.isoformat(), "budget_line": budget_line(b.category, b.purpose),
             "category": b.category, "purpose": b.purpose, "active": b.active}
            for b in tracked[:MAX_LIST]
        ],
        "due_this_month": [
            {"bill_id": str(d["recurring"].id), "name": d["recurring"].name, "due": d["due"].isoformat(),
             "status": d["status"], "amount": float(d["recurring"].amount)}
            for d in due[:MAX_LIST]
        ],
    }


class NoArgs(BaseModel):
    pass


def savings_plan(ctx: ToolContext, args: NoArgs) -> dict:
    return goals_api.plan(db=ctx.db).model_dump(mode="json", exclude_none=True)


def net_worth(ctx: ToolContext, args: MonthArgs) -> dict:
    out = balances_api.month_balances(month=args.month, db=ctx.db).model_dump(mode="json", exclude_none=True)
    out["accounts"] = [a for a in out["accounts"] if a.get("balance") is not None][:MAX_LIST]
    return out


def insight_cards(ctx: ToolContext, args: NoArgs) -> list:
    cards = insights_api.insights(purpose=None, db=ctx.db)
    return [InsightOut.model_validate(c).model_dump(mode="json", exclude_none=True) for c in cards[:MAX_LIST]]


# ---- Write tools --------------------------------------------------------------------------------------------


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(f"{value!r} is not a {what} id; look it up first")


class UpdateTransactionArgs(BaseModel):
    transaction_id: str
    category: str | None = None
    purpose: Purpose | None = None
    note: str | None = Field(None, max_length=NOTE_MAX, description='Replaces the note; "" clears it')
    not_an_expense: bool | None = Field(None, description="true: leave it out of totals (e.g. a card payment)")
    remember: bool = Field(False, description="Also file future charges from this merchant the same way")


def update_transaction(ctx: ToolContext, args: UpdateTransactionArgs) -> dict:
    txn_id = _uuid(args.transaction_id, "transaction")
    patch = TransactionPatch(category=args.category, purpose=args.purpose, note=args.note,
                             excluded=args.not_an_expense, remember=args.remember)
    detail = transactions_api.update_transaction(txn_id, patch, db=ctx.db, user=ctx.user)
    changed = [f"{k} → {v!r}" for k, v in (("category", args.category), ("purpose", args.purpose),
                                           ("note", args.note), ("not an expense", args.not_an_expense)) if v is not None]
    when = detail.timestamp.astimezone(ctx.tz).strftime("%b %-d")
    ctx.actions.append({"tool": "update_transaction",
                        "summary": f"{detail.merchant_normalized or detail.merchant_raw} ({when}, "
                                   f"{money(detail.amount)}): {', '.join(changed) or 'no change'}"
                                   + (" (remembered for this merchant)" if args.remember else "")})
    return {"updated": _txn_row(ctx.db.get(Transaction, txn_id), ctx.tz)}


class SetBudgetArgs(BaseModel):
    budget_line: str
    monthly_limit: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    warning_threshold_percent: Decimal | None = Field(
        None, gt=0, le=100, description="Omit to keep the current one (80 for a new budget)")


def set_budget(ctx: ToolContext, args: SetBudgetArgs) -> dict:
    threshold = args.warning_threshold_percent
    if threshold is None:
        current = ctx.db.get(BudgetEnvelope, args.budget_line)
        threshold = current.warning_threshold_percent if current else Decimal("80")
    budget = budgets_api.upsert_budget(
        args.budget_line,
        BudgetIn(monthly_limit=args.monthly_limit, warning_threshold_percent=threshold),
        db=ctx.db,
    )
    ctx.actions.append({"tool": "set_budget", "summary": f"Budget for {budget.category}: {money(budget.monthly_limit)}/month"})
    return {"budget_line": budget.category, "monthly_limit": float(budget.monthly_limit),
            "warning_threshold_percent": float(budget.warning_threshold_percent)}


class AddBillArgs(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    cadence: Literal["weekly", "biweekly", "monthly"]
    anchor_date: date = Field(description="A due date (the next or a past one); the schedule repeats from it")
    category: str
    purpose: Purpose = DEFAULT_PURPOSE
    merchant_pattern: str | None = Field(
        None, min_length=2, max_length=128,
        description="Word(s) in the card charge's merchant name that identify its payments; omit if paid outside the cards")


def add_bill(ctx: ToolContext, args: AddBillArgs) -> dict:
    item = recurring_api.create_recurring(RecurringIn(**args.model_dump()), db=ctx.db)
    ctx.actions.append({"tool": "add_bill", "summary": f"New bill: {item.name}, {money(item.amount)} {item.cadence}"})
    return {"id": str(item.id), "name": item.name}


class UpdateBillArgs(BaseModel):
    bill_id: str
    name: str | None = Field(None, min_length=1, max_length=128)
    amount: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    cadence: Literal["weekly", "biweekly", "monthly"] | None = None
    anchor_date: date | None = None
    category: str | None = None
    purpose: Purpose | None = None
    active: bool | None = Field(None, description="false: stopped / cancelled")


def update_bill(ctx: ToolContext, args: UpdateBillArgs) -> dict:
    changes = args.model_dump(exclude={"bill_id"}, exclude_none=True)
    item = recurring_api.update_recurring(_uuid(args.bill_id, "bill"), RecurringPatch(**changes), db=ctx.db)
    described = ", ".join(f"{k} → {v}" for k, v in changes.items()) or "no change"
    ctx.actions.append({"tool": "update_bill", "summary": f"{item.name}: {described}"})
    return {"id": str(item.id), "name": item.name, "amount": float(item.amount), "active": item.active}


class MarkPaidArgs(BaseModel):
    bill_id: str
    paid_on: date | None = Field(None, description="Omit for today")
    amount: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2, description="Omit for the usual amount")


def mark_bill_paid(ctx: ToolContext, args: MarkPaidArgs) -> dict:
    txn = recurring_api.mark_paid(_uuid(args.bill_id, "bill"), MarkPaidIn(paid_on=args.paid_on, amount=args.amount),
                                  db=ctx.db, user=ctx.user)
    when = txn.timestamp.astimezone(ctx.tz).strftime("%b %-d")
    ctx.actions.append({"tool": "mark_bill_paid", "summary": f"{txn.merchant_raw} marked paid: {money(txn.amount)} on {when}"})
    return {"recorded": _txn_row(txn, ctx.tz)}


class RememberArgs(BaseModel):
    fact: str = Field(min_length=3, max_length=500, description="One self-contained fact, with dates and amounts")


def remember(ctx: ToolContext, args: RememberArgs) -> dict:
    count = ctx.db.scalar(select(func.count()).select_from(ChatMemory).where(ChatMemory.owner == ctx.user))
    if count >= MAX_MEMORIES:
        raise ToolError(f"Already {MAX_MEMORIES} things remembered: forget an outdated one first")
    memory = ChatMemory(owner=ctx.user, fact=args.fact.strip())
    ctx.db.add(memory)
    ctx.db.commit()
    ctx.actions.append({"tool": "remember", "summary": f"Remembered: {memory.fact}"})
    return {"id": str(memory.id)}


class ForgetArgs(BaseModel):
    memory_id: str


def forget(ctx: ToolContext, args: ForgetArgs) -> dict:
    memory = ctx.db.get(ChatMemory, _uuid(args.memory_id, "memory"))
    if memory is None or memory.owner != ctx.user:
        raise ToolError("No such memory")
    ctx.db.delete(memory)
    ctx.db.commit()
    ctx.actions.append({"tool": "forget", "summary": f"Forgot: {memory.fact}"})
    return {"forgotten": memory.fact}


TOOLS: tuple[Tool, ...] = (
    Tool("month_summary", "Spent, still expected (bills_due plus estimated: the usual grocery and gas spend not yet spent), "
         "projected total and budget per budget line for one month, plus income: net pay received and still "
         "expected, by person, and what is left after spending; and savings: the savings rate ((income - spending) / "
         "income) and money moved into savings and investment accounts (transfers, not market returns).",
         MonthArgs, month_summary),
    Tool("spending_trend", "Total spending per month (oldest first) with per-budget-line totals.",
         TrendArgs, spending_trend),
    Tool("month_review", "Month-end review: each line vs. budget, last month and the 3-month average; top "
         "merchants; charges needing a check; which accounts' statements were imported.", MonthArgs, month_review),
    Tool("search_transactions", "Find card charges, payments and refunds. Returns how many match and their total, "
         "and lists the newest (with ids, notes and receipt lines).", SearchArgs, search_transactions),
    Tool("search_receipt_items", "Find individual products on scanned receipts (e.g. 'coffee', 'diapers').",
         ItemSearchArgs, search_receipt_items),
    Tool("bills", "Tracked recurring bills and memberships (with ids), and each due date this month with its status "
         "(paid, upcoming, overdue).", MonthArgs, bills),
    Tool("savings_goals", "The monthly savings target (this month: moved into savings and investments vs the "
         "target, status met / on_track / behind) and each goal (e.g. House): current amount from the latest "
         "balances, what's left, months left and the monthly amount that would reach it. Facts only, never advice.",
         NoArgs, savings_plan),
    Tool("net_worth", "Account balances at the end of a month (latest recorded; stale ones are carried from an "
         "earlier month), net worth (assets less credit cards and loans owed), and for savings and investment "
         "accounts how much of the change was contributed (transfers in) vs market return or interest. Facts only: "
         "never give investment advice.", MonthArgs, net_worth),
    Tool("insights", "The insight cards the app shows this month: budget pace, spikes, price changes, new or "
         "still-charging subscriptions, overdue bills.", NoArgs, insight_cards),
    Tool("update_transaction", "Change a transaction's category, purpose (who it was for), note, or mark it not an "
         "expense.", UpdateTransactionArgs, update_transaction, writes=True),
    Tool("set_budget", "Create or change the monthly budget of a budget line.", SetBudgetArgs, set_budget, writes=True),
    Tool("add_bill", "Start tracking a recurring bill or membership.", AddBillArgs, add_bill, writes=True),
    Tool("update_bill", "Change a tracked bill (amount, schedule, category) or stop it (active=false).",
         UpdateBillArgs, update_bill, writes=True),
    Tool("mark_bill_paid", "Record a payment of a tracked bill made outside the cards (e.g. rent by e-transfer).",
         MarkPaidArgs, mark_bill_paid, writes=True),
    Tool("remember", "Save something this person wants kept in mind in future chats: a plan, a goal, an upcoming "
         "expense, a circumstance.", RememberArgs, remember, writes=True),
    Tool("forget", "Delete a remembered fact (outdated, done, or the person asked).", ForgetArgs, forget, writes=True),
)
BY_NAME = {t.name: t for t in TOOLS}


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def definitions() -> list[dict]:
    """Tool definitions for the API, in a fixed order (they're the start of the cached prompt)."""
    return [{"name": t.name, "description": t.description, "input_schema": _schema(t.params)} for t in TOOLS]


def run_tool(ctx: ToolContext, name: str, raw_input: Any) -> tuple[str, bool]:
    """Runs one tool call; returns (JSON result, is_error). Never raises for problems the model can correct."""
    tool = BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"Unknown tool {name}"}), True
    try:
        args = tool.params.model_validate(raw_input or {})
        result = tool.run(ctx, args)
    except ValidationError as e:
        ctx.db.rollback()
        problems = [f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors()]
        return json.dumps({"error": "Invalid input", "problems": problems}), True
    except HTTPException as e:
        ctx.db.rollback()
        return json.dumps({"error": e.detail}), True
    except ToolError as e:
        ctx.db.rollback()
        return json.dumps({"error": str(e)}), True
    return json.dumps(result, default=str), False
