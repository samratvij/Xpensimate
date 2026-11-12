import os

# Must run before the app is imported: point everything at the test database.
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["API_TOKEN"] = "test-token"
os.environ["API_TOKEN_USER"] = "Alex"
os.environ["USER_TOKENS"] = "Sam:sam-test-token-0001"
os.environ["TIMEZONE"] = "America/Toronto"
os.environ["ANTHROPIC_API_KEY"] = ""  # never call Claude from tests
os.environ["HOUSEHOLD_FILE"] = os.path.join(os.path.dirname(__file__), "household.toml")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.base import SessionLocal, engine  # noqa: E402
from app.db.seed import seed  # noqa: E402
from main import app  # noqa: E402

TABLES = "chat_memories, chat_messages, chat_threads, savings_goals, savings_targets, balance_snapshots, income_sources, insight_dismissals, budget_alerts, devices, receipt_items, transaction_items, receipts, transactions, statement_imports, recurring_expenses, raw_ingest_log, budget_envelopes, categorization_rules, categorization_examples, gmail_sync_state, categories, accounts"


@pytest.fixture(scope="session", autouse=True)
def schema():
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_db():
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    with SessionLocal() as db:
        seed(db)
        seed_plan(db)


def seed_plan(db) -> None:
    """The fictional household's money plan, which a real household sets up in the app: two paycheques, a loan
    to family at a fixed value, a monthly savings target and a house goal."""
    from datetime import date
    from decimal import Decimal

    from app.db.models import BalanceSnapshot, IncomeSource, SavingsGoal, SavingsTarget

    db.add_all([
        IncomeSource(name="Alex's pay", earner="Alex", account_id="ws_cash", payer_pattern="NORTHWIND",
                     amount=Decimal("3250.40"), cadence="semimonthly", anchor_date=date(2026, 10, 15)),
        IncomeSource(name="Sam's pay", earner="Sam", account_id="rbc_sam_cheq", payer_pattern="PAYROLL",
                     amount=Decimal("2980.20"), cadence="biweekly", anchor_date=date(2026, 10, 29)),
        BalanceSnapshot(account_id="parents_loan", as_of=date(2026, 1, 1), balance=Decimal("20000.00"),
                        source="static", note="Lent to family; returned when needed"),
        SavingsTarget(scope="household", monthly_amount=Decimal("5000.00")),
        SavingsGoal(name="House", target_amount=Decimal("100000.00"), target_date=date(2027, 3, 31),
                    account_kinds=["savings"], account_ids=["ws_fhsa", "ws_sam_fhsa"]),
    ])
    db.commit()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})
