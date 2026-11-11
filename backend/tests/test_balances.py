import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.core.config import get_settings
from app.db.models import Transaction
from app.statements import balance_screenshot
from tests.fixtures import screens

TZ = ZoneInfo("America/Toronto")
LOAN = 20000.0


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 80), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def read(monkeypatch):
    """Upload a screenshot whose reading is `rows` (as Claude would return them)."""
    def upload(client, rows, **form):
        monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
        seen = {}

        def fake(jpeg, accounts, owner):
            seen["owner"] = owner
            return balance_screenshot.BalanceRead(shows_balances=bool(rows),
                                                  rows=[balance_screenshot.BalanceRow(**r) for r in rows])

        monkeypatch.setattr(balance_screenshot, "read_balances", fake)
        resp = client.post("/api/v1/balances/read", files={"file": ("shot.png", _png(), "image/png")}, data=form)
        monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
        return resp, seen

    return upload


def put(client, account, day, balance):
    return client.put(f"/api/v1/balances/{account}", json={"as_of": day, "balance": balance})


def month(client, m):
    body = client.get("/api/v1/balances", params={"month": m}).json()
    return body, {a["account_id"]: a for a in body["accounts"]}


def transfer(db, src, dst, amount, day):
    db.add(Transaction(source="test", timestamp=datetime.fromisoformat(f"{day}T12:00:00-04:00"), amount=Decimal(amount),
                       merchant_raw="move", txn_type="TRANSFER", excluded=True, category="Transfer", purpose="Alex",
                       account_id=src, counterpart_account_id=dst))
    db.commit()


# --- reading screenshots --------------------------------------------------------------------------

def test_screenshot_balances_are_read_and_matched_not_saved(client, read):
    resp, seen = read(client, screens.WS_BALANCES, owner="Alex")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == datetime.now(TZ).date().isoformat()
    assert seen["owner"] == "Alex"
    rows = {r["shown_name"]: r for r in body["rows"]}
    assert (rows["Checking"]["account_id"], rows["Checking"]["account_name"]) == ("ws_cash", "Wealthsimple Cash")
    assert (rows["Credit card Visa Infinite Privilege"]["balance"], rows["Credit card Visa Infinite Privilege"]["owed"]) == (1320.15, True)
    # Not a Canadian-dollar account the household tracks: left for the person to pick, or skip.
    assert rows["US savings"]["account_id"] is None
    # Nothing saved yet.
    assert month(client, date.today().strftime("%Y-%m"))[1]["ws_cash"]["balance"] is None


def test_screenshot_without_balances_is_rejected(client, read):
    assert read(client, [])[0].status_code == 422


def test_reading_needs_the_api_key(client):
    resp = client.post("/api/v1/balances/read", files={"file": ("shot.png", _png(), "image/png")})
    assert resp.status_code == 503


# --- net worth ------------------------------------------------------------------------------------

def test_confirmed_balances_make_net_worth(client):
    rows = [{"account_id": r["account_id"], "balance": r["balance"]} for r in screens.PC_BALANCES + screens.TFSA_BALANCE]
    resp = client.post("/api/v1/balances/batch", json={"as_of": "2026-10-31", "rows": rows})
    assert resp.status_code == 201 and len(resp.json()) == 4

    body, accounts = month(client, "2026-10")
    # Assets: PC Money, PC savings, TFSA and the loan to Sam's parents; the Mastercard balance is owed.
    assert body["assets"] == round(31.64 + 37812.40 + 5450.30 + LOAN, 2)
    assert body["liabilities"] == 240.10
    assert body["net_worth"] == round(31.64 + 37812.40 + 5450.30 + LOAN - 240.10, 2)
    assert body["groups"]["savings"] == 37812.40 and body["groups"]["investments"] == 5450.30
    assert accounts["pc_mastercard"]["group"] == "credit_cards"
    loan = accounts["parents_loan"]
    assert (loan["balance"], loan["source"], loan["stale"]) == (LOAN, "static", False)
    assert accounts["rbc_alex_savings"]["balance"] is None
    assert body["missing"] > 0


def test_a_balance_carries_forward_marked_stale(client):
    put(client, "pc_savings", "2026-09-30", "30000.00")
    put(client, "pc_savings", "2026-10-31", "37812.40")
    put(client, "ws_tfsa", "2026-09-30", "5000.00")
    body, accounts = month(client, "2026-10")
    assert (accounts["pc_savings"]["balance"], accounts["pc_savings"]["stale"]) == (37812.40, False)
    # No TFSA balance in October: September's is used, flagged.
    assert (accounts["ws_tfsa"]["balance"], accounts["ws_tfsa"]["as_of"], accounts["ws_tfsa"]["stale"]) == (5000.0, "2026-09-30", True)
    assert body["previous_net_worth"] == 30000.0 + 5000.0 + LOAN
    assert body["change"] == 7812.40


def test_change_splits_into_contributions_and_market_return(client, db):
    put(client, "ws_tfsa", "2026-09-30", "5000.00")
    transfer(db, "ws_cash", "ws_tfsa", "500.00", "2026-10-05")
    transfer(db, "ws_tfsa", "ws_cash", "100.00", "2026-10-20")
    transfer(db, "ws_cash", "ws_tfsa", "999.00", "2026-09-30")  # on the day of the earlier balance: already in it
    put(client, "ws_tfsa", "2026-10-31", "5450.30")
    put(client, "pc_savings", "2026-09-30", "37000.00")
    transfer(db, "pc_money", "pc_savings", "800.00", "2026-10-15")
    put(client, "pc_savings", "2026-10-31", "37812.40")

    body, accounts = month(client, "2026-10")
    tfsa = accounts["ws_tfsa"]
    assert (tfsa["change"], tfsa["contributions"], tfsa["market_return"]) == (450.30, 400.0, 50.3)
    savings = accounts["pc_savings"]
    assert (savings["contributions"], savings["market_return"]) == (800.0, 12.4)  # interest
    assert (body["contributions"], body["market_return"]) == (1200.0, 62.7)
    # A chequing account's change is spending and income, not a return.
    put(client, "pc_money", "2026-09-30", "100.00")
    put(client, "pc_money", "2026-10-31", "31.64")
    assert month(client, "2026-10")[1]["pc_money"]["market_return"] is None


def test_manual_balances(client):
    future = (datetime.now(TZ).date() + timedelta(days=2)).isoformat()
    assert put(client, "ws_fhsa", future, "1.00").status_code == 422
    assert put(client, "nope", "2026-10-31", "1.00").status_code == 404
    assert put(client, "ws_fhsa", "2026-10-31", "-5").status_code == 422
    first = put(client, "ws_fhsa", "2026-10-31", "19000.00").json()
    again = put(client, "ws_fhsa", "2026-10-31", "18250.10").json()
    assert first["id"] == again["id"] and again["balance"] == 18250.10 and again["created_by"] == "Alex"
    history = client.get("/api/v1/balances/ws_fhsa/history").json()
    assert [(h["as_of"], h["balance"]) for h in history] == [("2026-10-31", 18250.10)]
    assert client.delete(f"/api/v1/balances/snapshots/{again['id']}").status_code == 204
    assert client.get("/api/v1/balances/ws_fhsa/history").json() == []


def test_batch_is_checked(client):
    dup = {"as_of": "2026-10-31", "rows": [{"account_id": "ws_cash", "balance": "1"}, {"account_id": "ws_cash", "balance": "2"}]}
    assert client.post("/api/v1/balances/batch", json=dup).status_code == 422
    unknown = {"as_of": "2026-10-31", "rows": [{"account_id": "nope", "balance": "1"}]}
    assert client.post("/api/v1/balances/batch", json=unknown).status_code == 404
    assert client.get("/api/v1/balances", params={"month": "2026-13"}).status_code == 422


def test_the_loan_counts_from_january(client):
    body, accounts = month(client, "2025-12")
    assert accounts["parents_loan"]["balance"] is None and body["net_worth"] == 0.0
    assert month(client, "2026-06")[0]["net_worth"] == LOAN
