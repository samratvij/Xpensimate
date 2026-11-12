from datetime import date
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.core.household import load_household
from app.db.models import (
    BalanceSnapshot,
    BudgetEnvelope,
    IncomeSource,
    Receipt,
    RecurringExpense,
    SavingsGoal,
    SavingsTarget,
    Transaction,
)
from app.scripts.seed_demo import MONTHLY_SAVING, DemoRefused, seed_demo

TODAY = date(2026, 11, 20)
TZ = ZoneInfo("America/Toronto")


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_demo_fills_six_months_for_the_household(db):
    added = seed_demo(db, today=TODAY)
    assert added > 200
    assert count(db, RecurringExpense) == 8
    assert count(db, Receipt, Receipt.matched_transaction_id.is_not(None)) == 2
    assert count(db, BudgetEnvelope) >= 12
    first = db.scalar(select(func.min(Transaction.timestamp)))
    assert first.astimezone(TZ).date() >= date(2026, 6, 1)
    # Every purpose the household has shows up, and nothing is dated after today.
    purposes = set(db.scalars(select(Transaction.purpose).distinct()))
    assert {"Shared", "Alex", "Sam", "Parents"} <= purposes
    assert db.scalar(select(func.max(Transaction.timestamp))).astimezone(TZ).date() <= TODAY


def test_demo_data_reads_through_the_api(client, db):
    seed_demo(db, today=TODAY)
    summary = client.get("/api/v1/summary", params={"month": "2026-10"}).json()
    lines = {c["category"] for c in summary["categories"]}
    assert {"Grocery", "Alex Personal", "Sam Personal", "Parents"} <= lines
    # Every bill in a finished month was paid, including the ones paid by hand.
    october = client.get("/api/v1/recurring/expected", params={"month": "2026-10"}).json()
    assert len(october) == 8
    assert {o["status"] for o in october} == {"paid"}


def test_demo_is_the_same_every_time(db):
    first = seed_demo(db, today=TODAY)
    total = db.scalar(select(func.sum(Transaction.amount)))
    for model in (Receipt, Transaction, RecurringExpense, BudgetEnvelope, IncomeSource, BalanceSnapshot, SavingsTarget,
                  SavingsGoal):
        db.query(model).delete()
    db.commit()
    assert seed_demo(db, today=TODAY) == first
    assert db.scalar(select(func.sum(Transaction.amount))) == total


def test_demo_never_touches_a_database_with_transactions(db):
    seed_demo(db, today=TODAY)
    with pytest.raises(DemoRefused):
        seed_demo(db, today=TODAY)


def test_demo_has_pay_savings_and_net_worth(client, db):
    seed_demo(db, today=TODAY)
    assert count(db, IncomeSource, IncomeSource.payer_pattern.in_(["HARBORLINE", "MAPLEWORKS"])) == 2
    monthly = sum(int(MONTHLY_SAVING[a.kind]) for a in load_household().accounts if a.kind in MONTHLY_SAVING)
    income = client.get("/api/v1/income", params={"month": "2026-10"}).json()
    assert {d["purpose"] for d in income["deposits"]} == {"Alex", "Sam"}
    summary = client.get("/api/v1/summary", params={"month": "2026-10"}).json()
    assert float(summary["savings"]["moved"]) == monthly > 0
    worth = client.get("/api/v1/balances", params={"month": "2026-10"}).json()
    assert float(worth["contributions"]) == monthly and float(worth["net_worth"]) > 0
    plan = client.get("/api/v1/savings").json()
    assert plan["target"] and "Emergency fund" in {g["name"] for g in plan["goals"]}
    # Purchases never land in savings or investment accounts.
    kinds = {a.id: a.kind for a in load_household().accounts}
    spent_in = set(db.scalars(select(Transaction.account_id).where(Transaction.txn_type == "PURCHASE").distinct()))
    assert {kinds[a] for a in spent_in if a} <= {"credit_card", "chequing"}
