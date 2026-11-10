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


def test_currency_defaults_to_cad_and_is_checked():
    assert parse(household()).currency == "CAD"
    assert parse(household(currency="eur")).currency == "EUR"
    with pytest.raises(HouseholdConfigError, match="3-letter code"):
        parse(household(currency="Euro"))


def test_the_household_currency_is_used_everywhere(client, own_file):
    accounts = [{"id": "bank_card", "institution": "Bank", "name": "Card"},
                {"id": "travel", "institution": "Bank", "name": "Travel card", "currency": "USD"}]
    assert client.put("/api/v1/household", json=body(currency="EUR", accounts=accounts)).status_code == 200
    saved = tomllib.loads(own_file.read_text())
    assert saved["currency"] == "EUR" and "currency" not in saved["accounts"][0] and saved["accounts"][1]["currency"] == "USD"
    assert client.get("/api/v1/taxonomy").json()["currency"] == "EUR"
    currencies = {a["id"]: a["currency"] for a in client.get("/api/v1/accounts").json()}
    assert (currencies["bank_card"], currencies["travel"]) == ("EUR", "USD")
    # A charge whose source doesn't say gets the household's currency.
    pushed = client.post("/api/v1/transactions/push", json={
        "timestamp": datetime.now(timezone.utc).isoformat(), "amount": "4.50", "merchant": "Cafe", "source_ref": "c1"}).json()
    assert pushed["currency"] == "EUR"


def test_accounts_written_before_kinds_still_load():
    """A household.toml from an earlier Xpensimate: no kind or owner, one card each."""
    old = household(accounts=[{"id": "ws_credit", "institution": "Wealthsimple", "name": "Card", "card_last4": "1234"},
                              {"id": "my_bank", "institution": "Bank", "name": "Everyday"}])
    ws, mine = parse(old).accounts
    assert (ws.kind, ws.owner, ws.cards) == ("credit_card", "Shared", ["1234"])
    assert (mine.kind, mine.owner, mine.cards) == ("chequing", "Shared", [])


@pytest.mark.parametrize(("written", "kind"), [
    ("Checking", "chequing"), ("TFSA", "investment"), ("401(k)", "retirement"), ("RRSP", "retirement"),
    ("ISA", "investment"), ("savings", "savings"), ("mortgage", "loan"),
])
def test_regional_account_kinds_become_general_ones(written, kind):
    data = household(accounts=[{"id": "a", "institution": "Bank", "name": "A", "kind": written}])
    assert parse(data).accounts[0].kind == kind


def test_owner_and_several_cards():
    data = household(purposes=[SHARED, {"name": "Alex", "guide": "g", "budget_line": "Alex Personal"}], accounts=[
        {"id": "a", "institution": "Bank", "name": "Joint card", "kind": "credit_card", "card_last4": ["1111", "2222"]},
        {"id": "b", "institution": "Bank", "name": "Alex's", "owner": "Alex", "card_last4": "3333, 4444"}])
    joint, alex = parse(data).accounts
    assert (joint.cards, alex.cards, alex.owner) == (["1111", "2222"], ["3333", "4444"], "Alex")
    with pytest.raises(HouseholdConfigError, match="owner must be one of"):
        parse({**data, "accounts": [{"id": "c", "institution": "Bank", "name": "C", "owner": "Nobody"}]})
    with pytest.raises(HouseholdConfigError, match="kind must be one of"):
        parse({**data, "accounts": [{"id": "c", "institution": "Bank", "name": "C", "kind": "piggy bank"}]})
    with pytest.raises(HouseholdConfigError, match="listed twice"):
        parse({**data, "accounts": [{"id": "c", "institution": "Bank", "name": "C", "card_last4": ["1111", "1111"]}]})


def test_kinds_owners_and_cards_are_saved_and_used(client, db, own_file):
    accounts = [{"id": "tangerine_mastercard", "institution": "Tangerine", "name": "Mastercard", "kind": "credit_card",
                 "card_last4": ["7001", "7002"]},
                {"id": "alex_savings", "institution": "Bank", "name": "Savings", "kind": "savings", "owner": "Alex"}]
    got = client.put("/api/v1/household", json=body(accounts=accounts))
    assert got.status_code == 200, got.text
    assert {k["id"] for k in got.json()["account_kinds"]} >= {"chequing", "credit_card", "savings", "retirement"}
    saved = tomllib.loads(own_file.read_text())["accounts"]
    assert saved[0]["card_last4"] == ["7001", "7002"] and saved[1]["owner"] == "Alex" and saved[1]["kind"] == "savings"
    listed = {a["id"]: a for a in client.get("/api/v1/accounts").json()}
    assert (listed["alex_savings"]["kind"], listed["alex_savings"]["owner"]) == ("savings", "Alex")
    from app.services.accounts import account_for_alert
    assert account_for_alert(db, "7002", "tangerine_cheq") == "tangerine_mastercard"
    assert account_for_alert(db, "7001", None, "Tangerine Mastercard") == "tangerine_mastercard"
