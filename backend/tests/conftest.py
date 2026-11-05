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

TABLES = "budget_alerts, devices, receipt_items, transaction_items, receipts, transactions, statement_imports, recurring_expenses, raw_ingest_log, budget_envelopes, categorization_rules, categorization_examples, gmail_sync_state, categories, accounts"


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


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})
