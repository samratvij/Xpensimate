from datetime import date
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.db.models import BudgetEnvelope, Receipt, RecurringExpense, Transaction
from app.scripts.seed_demo import DemoRefused, seed_demo

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
    for model in (Receipt, Transaction, RecurringExpense, BudgetEnvelope):
        db.query(model).delete()
    db.commit()
    assert seed_demo(db, today=TODAY) == first
    assert db.scalar(select(func.sum(Transaction.amount))) == total


def test_demo_never_touches_a_database_with_transactions(db):
    seed_demo(db, today=TODAY)
    with pytest.raises(DemoRefused):
        seed_demo(db, today=TODAY)
