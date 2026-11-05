from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.db.models import Transaction
from app.services import categorizer
from app.services.llm import LlmClassification
from app.services.merchant import matches_pattern, normalize_merchant


def push(client, merchant, ts="2026-11-07T15:00:00Z", **extra):
    payload = {"timestamp": ts, "amount": "12.34", "merchant": merchant, **extra}
    resp = client.post("/api/v1/transactions/push", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def fake_claude(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return LlmClassification(category="Shopping", purpose="Shared", confidence=0.876)

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(categorizer, "classify_with_claude", fake)
    return calls


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SUBWAY #1234", "Subway"),
        ("SQ *BLUE BOTTLE COFFEE", "Blue Bottle Coffee"),
        ("TIM HORTONS #2231 TORONTO ON", "Tim Hortons Toronto"),
        ("AMZN Mktp CA*2K3AB1XY0", "Amazon"),
        ("McDonald's 40123", "Mcdonalds"),
        ("PETRO-CANADA 98765", "Petro-Canada"),
    ],
)
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected


def test_pattern_matches_whole_words_only():
    assert matches_pattern("Metro", "Metro 123")
    assert not matches_pattern("Metro", "Metrolinx")
    assert matches_pattern("McDonalds", "MCDONALD'S")


def test_rule_assigns_category_and_purpose(client):
    body = push(client, "METRO 512 TORONTO ON", account_id="ws_credit")
    assert (body["category"], body["purpose"]) == ("Grocery", "Shared")
    assert body["inference_confidence"] == 1.0
    assert body["categorized_by"] == "rule"


def test_whole_word_rule_does_not_misfire(client):
    # "Metro" is a grocery rule; "Metrolinx" must not match it.
    assert push(client, "METROLINX GO TRANSIT")["category"] != "Grocery"


def test_uber_eats_beats_uber(client):
    client.post("/api/v1/rules", json={"merchant_pattern": "Uber", "target_category": "Gas & Parking", "priority": 2})
    assert push(client, "UBER EATS")["category"] == "Eating Out"
    assert push(client, "UBER *TRIP", source_ref="trip")["category"] == "Gas & Parking"


def test_account_specific_rule_wins(client):
    client.post(
        "/api/v1/rules",
        json={"merchant_pattern": "Costco", "account_id": "ctfs_triangle", "target_category": "Shopping",
              "priority": 1},
    )
    assert push(client, "COSTCO WHOLESALE", account_id="ctfs_triangle")["category"] == "Shopping"
    assert push(client, "COSTCO WHOLESALE", account_id="ws_credit")["category"] == "Grocery"


def test_unknown_merchant_without_key_stays_uncategorized(client, monkeypatch):
    monkeypatch.setattr(categorizer, "classify_with_claude", lambda **_: pytest.fail("Claude must not be called"))
    body = push(client, "ZXQ HOLDINGS")
    assert (body["category"], body["categorized_by"], body["inference_confidence"]) == ("Uncategorized", "none", 0.0)


def test_unknown_merchant_falls_back_to_claude(client, fake_claude):
    body = push(client, "NEWEGG CANADA", account_id="pc_mastercard")
    assert (body["category"], body["purpose"], body["categorized_by"]) == ("Shopping", "Shared", "llm")
    assert body["inference_confidence"] == 0.88
    assert fake_claude[0]["merchant"] == "Newegg Canada"
    assert fake_claude[0]["account"] == "PC Financial PC Financial Mastercard"
    assert fake_claude[0]["local_time"].utcoffset() is not None


def test_rules_skip_claude(client, fake_claude):
    push(client, "STARBUCKS 4421")
    assert fake_claude == []


def test_claude_failure_leaves_uncategorized(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(categorizer, "classify_with_claude", lambda **_: None)
    assert push(client, "NEWEGG CANADA")["category"] == "Uncategorized"


def test_repeat_merchant_reuses_history_instead_of_claude(client, fake_claude):
    push(client, "NEWEGG CANADA", source_ref="a")
    second = push(client, "NEWEGG CANADA", source_ref="b")
    assert second["categorized_by"] == "history"
    assert second["category"] == "Shopping"
    assert len(fake_claude) == 1


def test_manual_category_is_reused_for_same_merchant(client):
    first = push(client, "ZXQ HOLDINGS", source_ref="a")
    client.patch(f"/api/v1/transactions/{first['id']}", json={"category": "Wifi + 5G"})
    second = push(client, "ZXQ HOLDINGS", source_ref="b")
    assert (second["category"], second["categorized_by"], second["inference_confidence"]) == (
        "Wifi + 5G", "history", 0.95,
    )
    assert second["category_is_manual"] is False


def test_low_confidence_history_is_ignored(client, db):
    db.add(Transaction(source="test", timestamp="2026-11-01T12:00:00Z", amount=Decimal("5"),
                       merchant_raw="ZXQ HOLDINGS", merchant_normalized="Zxq Holdings",
                       category="Entertainment", inference_confidence=Decimal("0.30")))
    db.commit()
    assert push(client, "ZXQ HOLDINGS")["category"] == "Uncategorized"


def test_remember_creates_rule(client):
    first = push(client, "ZXQ HOLDINGS #9", source_ref="a")
    client.patch(
        f"/api/v1/transactions/{first['id']}",
        json={"category": "Wifi + 5G", "purpose": "Shared", "remember": True},
    )
    rules = [r for r in client.get("/api/v1/rules").json() if r["merchant_pattern"] == "Zxq Holdings"]
    assert len(rules) == 1
    assert (rules[0]["target_category"], rules[0]["target_purpose"], rules[0]["priority"]) == (
        "Wifi + 5G", "Shared", 10,
    )
    second = push(client, "ZXQ HOLDINGS #12", source_ref="b")
    assert (second["category"], second["purpose"], second["categorized_by"]) == (
        "Wifi + 5G", "Shared", "rule",
    )


def test_remember_requires_category(client):
    txn = push(client, "ZXQ HOLDINGS")
    resp = client.patch(f"/api/v1/transactions/{txn['id']}", json={"purpose": "Parents", "remember": True})
    assert resp.status_code == 422


def test_recategorize_fills_gaps_and_respects_manual(client, monkeypatch):
    unknown = push(client, "NEWEGG CANADA", source_ref="a")
    manual = push(client, "NEWEGG CANADA", source_ref="b")
    client.patch(f"/api/v1/transactions/{manual['id']}", json={"category": "Travel", "purpose": "Parents"})

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        categorizer, "classify_with_claude",
        lambda **_: LlmClassification(category="Shopping", purpose="Shared", confidence=0.9),
    )
    # The manual row is reused as history for its sibling; it is never itself rewritten.
    assert client.post("/api/v1/transactions/recategorize").json() == {"examined": 1, "changed": 1}
    assert client.get(f"/api/v1/transactions/{unknown['id']}").json()["category"] == "Travel"
    assert client.get(f"/api/v1/transactions/{manual['id']}").json()["purpose"] == "Parents"


def test_recategorize_keeps_manual_purpose(client):
    txn = push(client, "STARBUCKS")
    client.patch(f"/api/v1/transactions/{txn['id']}", json={"purpose": "Alex"})
    client.post("/api/v1/transactions/recategorize", params={"include_categorized": True})
    after = client.get(f"/api/v1/transactions/{txn['id']}").json()
    assert (after["category"], after["purpose"]) == ("Eating Out", "Alex")


def test_rules_api_validation_and_delete(client):
    bad = {"merchant_pattern": "Foo", "target_category": "Grocery", "hour_start": 18, "hour_end": 9}
    assert client.post("/api/v1/rules", json=bad).status_code == 422
    assert client.post("/api/v1/rules", json={**bad, "target_category": "Nope"}).status_code == 422
    rule = client.post("/api/v1/rules", json={"merchant_pattern": "Foo", "target_category": "Grocery"}).json()
    assert rule["target_purpose"] is None
    assert client.delete(f"/api/v1/rules/{rule['id']}").status_code == 204
    assert client.delete(f"/api/v1/rules/{rule['id']}").status_code == 404


def test_refusal_fallback_only_where_supported():
    from app.services.llm import refusal_fallback
    assert refusal_fallback("claude-haiku-5-5") == {}
    assert refusal_fallback("claude-sonnet-5-5")["extra_body"] == {"fallbacks": "default"}


def test_prompt_and_budget_lines_come_from_the_purpose_list():
    from app.core.taxonomy import budget_line, purpose_defs
    from app.services.llm import household_guide

    guide = household_guide(["Grocery", "Transfer"])
    assert all(f'- "{p.name}": ' in guide for p in purpose_defs())
    assert "Alex and Sam" in guide
    assert budget_line("Grocery", "Shared") == "Grocery"
    assert budget_line("Grocery", "Sam") == "Sam Personal"
    assert budget_line("Transfer", "Parents") == "Parents"
    assert budget_line("Grocery", "Parents") == "Parents Shopping"
