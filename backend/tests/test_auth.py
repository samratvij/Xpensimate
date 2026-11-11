from fastapi.testclient import TestClient

from main import app


def test_health_needs_no_token():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_api_rejects_missing_token():
    assert TestClient(app).get("/api/v1/accounts").status_code == 401


def test_api_rejects_wrong_token():
    resp = TestClient(app).get("/api/v1/accounts", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_meta(client):
    accounts = {a["id"]: a for a in client.get("/api/v1/accounts").json()}
    assert list(accounts) == sorted(accounts)
    assert {"ctfs_triangle", "rbc_sam", "ws_cash", "rbc_sam_cheq", "pc_savings", "ws_fhsa", "parents_loan"} <= set(accounts)
    assert (accounts["rbc_sam_cheq"]["owner"], accounts["rbc_sam_cheq"]["kind"]) == ("Sam", "chequing")
    assert (accounts["ws_fhsa"]["owner"], accounts["ws_fhsa"]["kind"]) == ("Alex", "investment")
    taxonomy = client.get("/api/v1/taxonomy").json()
    assert "Grocery" in taxonomy["categories"]
    assert "Alex" in taxonomy["purposes"]
    assert {"Grocery", "Alex Personal", "Parents", "Parents Shopping"} <= set(taxonomy["budget_lines"])
    assert taxonomy["person_lines"] == {
        "Alex Personal": "person", "Sam Personal": "person", "Sam Abroad": "abroad",
        "Parents": "family", "Parents Shopping": "family",
    }
    assert set(taxonomy["person_lines"]) <= set(taxonomy["budget_lines"])


def test_each_person_has_their_own_token(client):
    assert client.get("/api/v1/me").json() == {"user": "Alex"}
    sam = TestClient(app, headers={"Authorization": "Bearer sam-test-token-0001"})
    assert sam.get("/api/v1/me").json() == {"user": "Sam"}
    assert sam.get("/api/v1/accounts").status_code == 200


def test_edits_record_who_made_them(client):
    txn = client.post("/api/v1/transactions/push", json={
        "timestamp": "2026-11-02T14:00:00Z", "amount": "40.00", "merchant": "FARM BOY #12",
    }).json()
    assert txn["edited_by"] is None  # only automation so far

    sam = TestClient(app, headers={"Authorization": "Bearer sam-test-token-0001"})
    edited = sam.patch(f"/api/v1/transactions/{txn['id']}", json={"purpose": "Sam"}).json()
    assert edited["edited_by"] == "Sam"

    split = client.put(f"/api/v1/transactions/{txn['id']}/items", json={"items": [
        {"sku_name": "Veg", "price": "30.00", "category": "Grocery", "purpose": "Shared"},
        {"sku_name": "Snacks", "price": "10.00", "category": "Grocery", "purpose": "Alex"},
    ]}).json()
    assert split["edited_by"] == "Alex"


def test_bad_user_tokens_are_rejected():
    import pytest

    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(api_token="a" * 32, user_tokens="Sam:short").tokens()
    with pytest.raises(ValueError):
        Settings(api_token="a" * 32, user_tokens="Sam:" + "a" * 32).tokens()


def test_web_app_is_served_without_a_token():
    web = TestClient(app)
    assert web.get("/", follow_redirects=False).headers["location"] == "/app/"
    page = web.get("/app/")
    assert page.status_code == 200 and "Xpensimate" in page.text
    assert page.headers["cache-control"] == "no-cache"
