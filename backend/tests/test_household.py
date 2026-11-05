import tomllib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.household import HouseholdConfigError, parse
from app.db.models import Transaction

EXAMPLE = Path(__file__).resolve().parents[1] / "household.example.toml"
SHARED = {"name": "Shared", "guide": "joint spending"}


def household(**overrides):
    return {"description": "two people", "purposes": [SHARED], **overrides}


def test_the_example_household_is_valid():
    example = parse(tomllib.loads(EXAMPLE.read_text()))
    assert example.purposes[0].name == "Shared"
    assert {a.id for a in example.accounts} >= {"tangerine_mastercard", "ws_credit"}


@pytest.mark.parametrize(("data", "message"), [
    (household(purposes=[]), "at least one purpose"),
    (household(purposes=[{**SHARED, "budget_line": "Shared"}]), "first purpose"),
    (household(purposes=[{"name": "Everyone", "guide": "g"}]), "first purpose"),
    (household(purposes=[SHARED, {"name": "Alex"}]), "needs a budget_line"),
    (household(purposes=[SHARED, {"name": "Alex", "guide": "g", "icon": "robot"}]), "icon"),
    (household(purposes=[SHARED, {"name": "A", "guide": "g", "budget_line": "X"},
                         {"name": "B", "guide": "g", "budget_line": "X"}]), "budget lines"),
    (household(purposes=[SHARED, {"name": "A", "guide": "g", "transfer_budget_line": "T"}]), "needs a budget_line"),
    (household(accounts=[{"id": "a", "institution": "Bank", "name": "Card", "card_last4": 1234}]), "four digits"),
    (household(accounts=[{"id": "a", "institution": "Bank"}]), "'name' is required"),
    ({"purposes": [SHARED]}, "'description' is required"),
])
def test_mistakes_are_explained(data, message):
    with pytest.raises(HouseholdConfigError, match=message):
        parse(data)


# --- the setup API (writes a household file of its own, never the tests' household.toml) ---

ALEX = {"name": "Alex", "budget_line": "Alex Personal"}


@pytest.fixture
def own_file(tmp_path, monkeypatch):
    path = tmp_path / "household.toml"
    monkeypatch.setenv("HOUSEHOLD_FILE", str(path))
    return path


def body(**overrides):
    return {"description": "two people in Halifax", "purposes": [{"name": "Shared"}, ALEX], "accounts": [], **overrides}


def test_without_a_household_file_the_server_is_in_setup_mode(client, own_file):
    got = client.get("/api/v1/household").json()
    assert got["setup_needed"] is True
    assert [p["name"] for p in got["purposes"]] == ["Shared"]
    assert {"tangerine_mastercard", "ws_credit"} <= {a["id"] for a in got["known_accounts"]}
    assert client.get("/api/v1/taxonomy").json()["purposes"] == ["Shared"]


def test_saving_writes_the_file_and_applies_right_away(client, own_file):
    account = {"id": "tangerine_mastercard", "institution": "Tangerine", "name": "Mastercard", "card_last4": "4242"}
    saved = client.put("/api/v1/household", json=body(accounts=[account])).json()
    assert saved["setup_needed"] is False
    # A missing guide gets one that fits.
    assert saved["purposes"][1]["guide"].startswith("Alex's own spending")
    assert own_file.read_text().count("[[purposes]]") == 2
    taxonomy = client.get("/api/v1/taxonomy").json()
    assert (taxonomy["purposes"], taxonomy["person_lines"]) == (["Shared", "Alex"], {"Alex Personal": "person"})
    accounts = {a["id"]: a for a in client.get("/api/v1/accounts").json()}
    assert accounts["tangerine_mastercard"]["account_name"] == "Mastercard"
    # The saved file reads back the same.
    assert parse(tomllib.loads(own_file.read_text())).purposes[1].name == "Alex"


def test_bad_households_are_explained(client, own_file):
    assert client.put("/api/v1/household", json=body(purposes=[ALEX])).status_code == 422
    clash = body(purposes=[{"name": "Shared"}, {"name": "Alex", "budget_line": "Grocery"}])
    assert "already a category" in client.put("/api/v1/household", json=clash).json()["detail"]
    assert not own_file.exists()


def test_renaming_a_budget_line_keeps_its_budget(client, own_file):
    client.put("/api/v1/household", json=body())
    client.put("/api/v1/budgets/Alex Personal", json={"monthly_limit": "250"})
    renamed = body(purposes=[{"name": "Shared"}, {"name": "Alex", "budget_line": "Alex's money"}])
    client.put("/api/v1/household", json=renamed)
    budgets = {b["category"]: b["monthly_limit"] for b in client.get("/api/v1/budgets").json()}
    assert budgets.get("Alex's money") == 250.0 and "Alex Personal" not in budgets


def test_a_purpose_in_use_cant_be_removed(client, db, own_file):
    client.put("/api/v1/household", json=body())
    db.add(Transaction(source="test", source_ref="t1", timestamp=datetime(2026, 11, 1, tzinfo=timezone.utc),
                       amount=Decimal("12.00"), merchant_raw="Cafe", purpose="Alex"))
    db.commit()
    refused = client.put("/api/v1/household", json=body(purposes=[{"name": "Shared"}]))
    assert refused.status_code == 409 and "1 charge" in refused.json()["detail"]
