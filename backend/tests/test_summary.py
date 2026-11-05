import pytest
from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Transaction, TransactionItem


def add_txn(db, *, amount, category, purpose="Shared", ts="2026-11-10T12:00:00+00:00", items=None,
            merchant="Somewhere", excluded=False):
    txn = Transaction(
        source="test",
        timestamp=datetime.fromisoformat(ts),
        amount=Decimal(amount),
        merchant_raw=merchant,
        excluded=excluded,
        category=category,
        purpose=purpose,
        is_itemized=bool(items),
        items=[
            TransactionItem(sku_name=name, category=cat, purpose=pur, price=Decimal(price))
            for name, cat, pur, price in items or []
        ],
    )
    db.add(txn)
    db.commit()


def by_category(body):
    return {row["category"]: row for row in body["categories"]}


def test_summary_against_budget(client, db):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "100.00"})
    add_txn(db, amount="85.00", category="Eating Out")
    add_txn(db, amount="30.00", category="Eating Out")

    rows = by_category(client.get("/api/v1/summary", params={"month": "2026-11"}).json())
    dining = rows["Eating Out"]
    assert dining["spent"] == 115.0
    assert dining["percent_used"] == 115.0
    assert dining["over_warning"] is True
    assert dining["over_limit"] is True


def test_summary_lists_budgets_with_no_spend(client):
    client.put("/api/v1/budgets/Travel", json={"monthly_limit": "500.00", "warning_threshold_percent": "90"})
    travel = by_category(client.get("/api/v1/summary", params={"month": "2026-11"}).json())["Travel"]
    assert travel["spent"] == 0.0
    assert travel["over_warning"] is False


def test_summary_uses_local_month_boundaries(client, db):
    # 03:00 UTC on Nov 1 is still Oct 31 in Toronto.
    add_txn(db, amount="10.00", category="Grocery", ts="2026-11-01T03:00:00+00:00")
    assert client.get("/api/v1/summary", params={"month": "2026-11"}).json()["total_spent"] == 0.0
    assert client.get("/api/v1/summary", params={"month": "2026-10"}).json()["total_spent"] == 10.0


def test_summary_groups_by_budget_line(client, db):
    add_txn(db, amount="40.00", category="Grocery")
    add_txn(db, amount="25.00", category="Health & Fitness", purpose="Alex")
    add_txn(db, amount="15.00", category="Subscriptions", purpose="Alex")
    add_txn(db, amount="500.00", category="Transfer", purpose="Parents")
    add_txn(db, amount="159.75", category="Government & Fees", purpose="Parents")
    add_txn(db, amount="30.00", category="Grocery", purpose="Sam Abroad")

    lines = by_category(client.get("/api/v1/summary", params={"month": "2026-11"}).json())
    assert {k: v["spent"] for k, v in lines.items()} == {
        "Grocery": 40.0, "Alex Personal": 40.0, "Parents": 500.0, "Parents Shopping": 159.75, "Sam Abroad": 30.0,
    }

    body = client.get("/api/v1/summary", params={"month": "2026-11", "group_by": "category"}).json()
    assert body["group_by"] == "category"
    assert by_category(body)["Grocery"]["spent"] == 70.0


def test_budgets_apply_to_person_lines(client, db):
    client.put("/api/v1/budgets/Alex Personal", json={"monthly_limit": "50.00"})
    add_txn(db, amount="45.00", category="Health & Fitness", purpose="Alex")
    line = by_category(client.get("/api/v1/summary", params={"month": "2026-11"}).json())["Alex Personal"]
    assert (line["spent"], line["over_warning"], line["over_limit"]) == (45.0, True, False)


def test_summary_splits_itemized_transactions(client, db):
    add_txn(
        db,
        amount="60.00",
        category="Shopping",
        items=[
            ("Milk", "Grocery", "Shared", "10.00"),
            ("HDMI cable", "Shopping", "Alex", "50.00"),
        ],
    )
    rows = by_category(client.get("/api/v1/summary", params={"month": "2026-11"}).json())
    assert rows["Grocery"]["spent"] == 10.0
    assert rows["Alex Personal"]["spent"] == 50.0
    assert "Shopping" not in rows

    alex = client.get("/api/v1/summary", params={"month": "2026-11", "purpose": "Alex"}).json()
    assert by_category(alex).keys() == {"Alex Personal"}


def test_summary_filters_by_purpose(client, db):
    add_txn(db, amount="20.00", category="Grocery", purpose="Shared")
    add_txn(db, amount="7.00", category="Grocery", purpose="Sam")
    body = client.get("/api/v1/summary", params={"month": "2026-11", "purpose": "Sam"}).json()
    assert body["total_spent"] == 7.0


def test_summary_rejects_bad_month(client):
    assert client.get("/api/v1/summary", params={"month": "2026-13"}).status_code == 422


def test_budget_crud(client):
    assert client.put("/api/v1/budgets/Grocery", json={"monthly_limit": "400"}).status_code == 200
    assert client.put("/api/v1/budgets/Parents Shopping", json={"monthly_limit": "500"}).status_code == 200
    assert client.put("/api/v1/budgets/NotACategory", json={"monthly_limit": "1"}).status_code == 422
    assert [b["category"] for b in client.get("/api/v1/budgets").json()] == ["Grocery", "Parents Shopping"]
    assert client.delete("/api/v1/budgets/Grocery").status_code == 204
    assert client.delete("/api/v1/budgets/Grocery").status_code == 404


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Grocery", {"shared groceries", "split basket"}),
        ("Alex Personal", {"boxing club", "split basket"}),
        ("Parents", {"allowance"}),
        ("Parents Shopping", {"drivetest"}),
    ],
)
def test_transactions_by_budget_line(client, db, line, expected):
    add_txn(db, amount="40.00", category="Grocery", merchant="shared groceries")
    add_txn(db, amount="75.40", category="Health & Fitness", purpose="Alex", merchant="boxing club")
    add_txn(db, amount="500.00", category="Transfer", purpose="Parents", merchant="allowance")
    add_txn(db, amount="159.75", category="Government & Fees", purpose="Parents", merchant="drivetest")
    add_txn(db, amount="20.00", category="Grocery", merchant="excluded thing", excluded=True)
    add_txn(db, amount="60.00", category="Shopping", merchant="split basket",
            items=[("Milk", "Grocery", "Shared", "10.00"), ("Cable", "Shopping", "Alex", "50.00")])
    rows = client.get("/api/v1/transactions", params={"budget_line": line}).json()
    assert {r["merchant_raw"] for r in rows} == expected
