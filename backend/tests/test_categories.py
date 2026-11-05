from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import BudgetEnvelope, CategorizationRule, RecurringExpense, Transaction
from app.services.llm import output_model

NOW = datetime.now(timezone.utc)


def names(client):
    return [c["name"] for c in client.get("/api/v1/categories").json()]


def add_txn(db, category, amount="10.00"):
    t = Transaction(source="test", timestamp=NOW, amount=Decimal(amount), merchant_raw="X", category=category)
    db.add(t)
    db.commit()
    return t


def test_defaults_and_usage(client, db):
    add_txn(db, "Grocery")
    cats = {c["name"]: c for c in client.get("/api/v1/categories").json()}
    assert len(cats) == 22 and cats["Grocery"]["transactions"] == 1
    assert client.get("/api/v1/taxonomy").json()["categories"][0] == "Rent + Parking"


def test_add_category_is_usable_everywhere(client, db):
    assert client.post("/api/v1/categories", json={"name": "  Pets  "}).json()["name"] == "Pets"
    txn = add_txn(db, "Misc")
    assert client.patch(f"/api/v1/transactions/{txn.id}", json={"category": "Pets"}).json()["category"] == "Pets"
    assert client.put("/api/v1/budgets/Pets", json={"monthly_limit": "80"}).status_code == 200
    assert "Pets" in client.get("/api/v1/taxonomy").json()["budget_lines"]
    assert client.patch(f"/api/v1/transactions/{txn.id}", json={"category": "Nope"}).status_code == 422


def test_names_are_validated(client):
    assert client.post("/api/v1/categories", json={"name": "Grocery"}).status_code == 409
    assert client.post("/api/v1/categories", json={"name": "Alex Personal"}).status_code == 422
    assert client.post("/api/v1/categories", json={"name": "A/B"}).status_code == 422


def test_rename_moves_everything(client, db):
    txn = add_txn(db, "Eating Out")
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "500"})
    client.post("/api/v1/rules", json={"merchant_pattern": "Subway", "target_category": "Eating Out", "priority": 9})
    client.post("/api/v1/recurring", json={"name": "DashPass", "category": "Eating Out", "amount": "11.29",
                                           "cadence": "monthly", "anchor_date": "2026-11-01"})
    body = client.post("/api/v1/categories/Eating Out/rename", json={"name": "Restaurants"}).json()
    assert body == {"name": "Restaurants", "transactions": 1, "monthly_limit": 500.0}
    db.expire_all()
    assert db.get(Transaction, txn.id).category == "Restaurants"
    assert db.get(BudgetEnvelope, "Restaurants") is not None and db.get(BudgetEnvelope, "Eating Out") is None
    assert db.scalar(select(CategorizationRule.target_category).where(CategorizationRule.merchant_pattern == "Subway")) == "Restaurants"
    assert db.scalar(select(RecurringExpense.category)) == "Restaurants"
    order = names(client)
    assert "Eating Out" not in order and order.index("Restaurants") == 7  # keeps its place


def test_merge_moves_transactions_and_budget(client, db):
    txn = add_txn(db, "Laundry")
    client.put("/api/v1/budgets/Laundry", json={"monthly_limit": "100"})
    assert client.delete("/api/v1/categories/Laundry", params={"into": "Household"}).status_code == 204
    db.expire_all()
    assert db.get(Transaction, txn.id).category == "Household"
    assert db.get(BudgetEnvelope, "Household").monthly_limit == Decimal("100.00")
    assert "Laundry" not in names(client)
    assert client.delete("/api/v1/categories/Household", params={"into": "Household"}).status_code == 422
    assert client.delete("/api/v1/categories/Nope", params={"into": "Misc"}).status_code == 404


def test_merge_keeps_the_targets_budget(client):
    client.put("/api/v1/budgets/Laundry", json={"monthly_limit": "100"})
    client.put("/api/v1/budgets/Household", json={"monthly_limit": "200"})
    client.delete("/api/v1/categories/Laundry", params={"into": "Household"})
    budgets = {b["category"]: b["monthly_limit"] for b in client.get("/api/v1/budgets").json()}
    assert budgets == {"Household": 200.0}


def test_claude_answer_schema_uses_the_live_list(client):
    client.post("/api/v1/categories", json={"name": "Pets"})
    schema = output_model(["Grocery", "Pets"]).model_json_schema()
    assert schema["properties"]["category"]["enum"] == ["Grocery", "Pets"]
