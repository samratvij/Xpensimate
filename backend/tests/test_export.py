import csv
import io
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.db.models import Transaction, TransactionItem

TZ = ZoneInfo("America/Toronto")


def _txn(db, when, amount, merchant, **kw):
    txn = Transaction(source="manual", timestamp=when, amount=Decimal(amount), merchant_raw=merchant,
                      category=kw.pop("category", "Grocery"), purpose=kw.pop("purpose", "Shared"), **kw)
    db.add(txn)
    db.commit()
    return txn


def _rows(resp):
    assert resp.status_code == 200, resp.text
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))


def test_csv_covers_the_range_in_local_days_oldest_first(client, db):
    _txn(db, datetime(2026, 10, 31, 23, 30, tzinfo=TZ), "9.99", "Before")
    _txn(db, datetime(2026, 11, 30, 23, 30, tzinfo=TZ), "20.00", "Last day", account_id="ws_credit")
    _txn(db, datetime(2026, 11, 1, 0, 5, tzinfo=TZ), "12.50", "First day", note="milk, eggs")
    _txn(db, datetime(2026, 12, 1, 0, 1, tzinfo=TZ), "1.00", "After")

    resp = client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30")
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="xpensimate_2026-11-01_to_2026-11-30.csv"' in resp.headers["content-disposition"]
    rows = _rows(resp)
    assert [r["Description"] for r in rows] == ["First day", "Last day"]
    assert rows[0]["Date"] == "2026-11-01" and rows[0]["Debit"] == "12.50" and rows[0]["Credit"] == ""
    assert rows[0]["Note"] == "milk, eggs" and rows[0]["Account"] == "Cash / other"
    assert rows[1]["Account"] != "ws_credit"  # the account's name, not its id


def test_debits_credits_and_what_counts_as_spending(client, db):
    day = datetime(2026, 11, 5, 12, tzinfo=TZ)
    _txn(db, day, "50.00", "Store")
    _txn(db, day, "-10.00", "Store refund", txn_type="REFUND")
    _txn(db, day, "3000.00", "Payroll", txn_type="INCOME", category="Salary", purpose="Samrat", excluded=True)
    _txn(db, day, "400.00", "Card payment", txn_type="TRANSFER", category="Transfer", excluded=True)
    rows = {r["Description"]: r for r in _rows(client.get("/api/v1/transactions/export?start=2026-11-05&end=2026-11-05"))}
    assert rows["Store"]["Debit"] == "50.00" and rows["Store"]["Counts as spending"] == "Yes"
    assert rows["Store refund"]["Credit"] == "10.00" and rows["Store refund"]["Counts as spending"] == "Yes"
    assert rows["Payroll"]["Credit"] == "3000.00" and rows["Payroll"]["Counts as spending"] == "No"
    assert rows["Card payment"]["Debit"] == "400.00" and rows["Card payment"]["Type"] == "Transfer"


def test_itemized_charge_lists_its_lines_categories(client, db):
    txn = _txn(db, datetime(2026, 11, 7, 12, tzinfo=TZ), "30.00", "Costco", is_itemized=True)
    db.add_all([TransactionItem(transaction_id=txn.id, sku_name="Milk", category="Grocery", price=Decimal("10")),
                TransactionItem(transaction_id=txn.id, sku_name="Soap", category="Household", price=Decimal("20"))])
    db.commit()
    [row] = _rows(client.get("/api/v1/transactions/export?start=2026-11-07&end=2026-11-07"))
    assert row["Category"] == "Grocery; Household"


def test_account_filter(client, db):
    day = datetime(2026, 11, 5, 12, tzinfo=TZ)
    _txn(db, day, "5.00", "Card", account_id="ws_credit")
    _txn(db, day, "6.00", "Cash")
    rows = _rows(client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30&account_id=ws_credit"))
    assert [r["Description"] for r in rows] == ["Card"]
    assert client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30&account_id=nope").status_code == 422


def test_pdf_statement(client, db):
    _txn(db, datetime(2026, 11, 5, 12, tzinfo=TZ), "50.00", "Café Olé → Montréal", note="a note")
    resp = client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30&format=pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert 'filename="xpensimate_2026-11-01_to_2026-11-30.pdf"' in resp.headers["content-disposition"]


def test_empty_pdf_and_bad_ranges(client):
    assert client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30&format=pdf").content.startswith(b"%PDF")
    assert client.get("/api/v1/transactions/export?start=2026-11-30&end=2026-11-01").status_code == 422
    assert client.get("/api/v1/transactions/export?start=2010-01-01&end=2026-11-01").status_code == 422
    assert client.get("/api/v1/transactions/export?start=2026-11-01").status_code == 422


def test_export_needs_a_token(client):
    client.headers.pop("Authorization")
    assert client.get("/api/v1/transactions/export?start=2026-11-01&end=2026-11-30").status_code == 401
