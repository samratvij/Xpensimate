from datetime import datetime, timedelta, timezone

import pytest

from app.services import push

NOW = datetime.now(timezone.utc)


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self.ok = status == 200
        self._body = body or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeFcm:
    def __init__(self):
        self.sent = []
        self.dead_tokens = set()

    def post(self, url, json, timeout):
        token = json["message"]["token"]
        if token in self.dead_tokens:
            return FakeResponse(404, {"error": {"status": "NOT_FOUND"}})
        self.sent.append(json["message"])
        return FakeResponse(200, {"name": "projects/p/messages/1"})


@pytest.fixture
def fcm(monkeypatch, client):
    fake = FakeFcm()
    monkeypatch.setattr(push, "fcm_session", lambda: (fake, "xpensimate"))
    client.post("/api/v1/devices", json={"token": "phone-token-" + "x" * 20, "name": "Pixel"})
    return fake


def spend(client, amount, merchant="SUBWAY #12 TORONTO ON", ts=None, ref=None):
    resp = client.post("/api/v1/transactions/push", json={
        "timestamp": (ts or NOW).isoformat(), "amount": amount, "merchant": merchant, "source_ref": ref,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def alerts(client):
    return [(a["budget_line"], a["level"]) for a in client.get("/api/v1/alerts").json()]


def test_no_budget_no_alert(client, fcm):
    spend(client, "500.00")
    assert alerts(client) == [] and fcm.sent == []


def test_warning_then_exceeded_once_each(client, fcm):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "100.00"})
    spend(client, "85.00", ref="a")
    assert alerts(client) == [("Eating Out", "WARNING")]
    message = fcm.sent[-1]
    assert message["notification"]["title"] == "Eating Out at 85% of budget"
    assert message["notification"]["body"].endswith("· Subway $85.00")
    assert message["data"]["event"] == "BUDGET_WARNING"
    assert message["android"]["notification"]["channel_id"] == "budget_alerts"

    spend(client, "5.00", ref="b")
    assert len(fcm.sent) == 1

    spend(client, "15.40", ref="c")
    assert alerts(client) == [("Eating Out", "EXCEEDED"), ("Eating Out", "WARNING")]
    assert fcm.sent[-1]["notification"]["title"] == "Over budget: Eating Out"
    assert "$105.40 of $100.00" in fcm.sent[-1]["notification"]["body"]

    spend(client, "30.00", ref="d")
    assert len(fcm.sent) == 2


def test_jumping_straight_over_skips_the_warning(client, fcm):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "50.00"})
    spend(client, "60.00", ref="a")
    spend(client, "1.00", ref="b")
    assert alerts(client) == [("Eating Out", "EXCEEDED")]


def test_old_months_never_alert(client, fcm):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "10.00"})
    spend(client, "50.00", ts=NOW - timedelta(days=62))
    assert alerts(client) == []


def test_editing_who_moves_spend_to_a_person_line(client, fcm):
    client.put("/api/v1/budgets/Alex Personal", json={"monthly_limit": "100.00"})
    txn = spend(client, "120.00", merchant="NORTHSIDE BOXING")
    assert alerts(client) == []
    client.patch(f"/api/v1/transactions/{txn['id']}", json={"category": "Health & Fitness", "purpose": "Alex"})
    assert alerts(client) == [("Alex Personal", "EXCEEDED")]
    assert fcm.sent[-1]["data"]["transaction_id"] == txn["id"]


def test_lowering_a_budget_can_alert(client, fcm):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "500.00"})
    spend(client, "90.00")
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "100.00"})
    assert alerts(client) == [("Eating Out", "WARNING")]
    assert "Subway" not in fcm.sent[-1]["notification"]["body"]


def test_alert_is_kept_when_push_is_off(client, monkeypatch):
    monkeypatch.setattr(push, "fcm_session", lambda: None)
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "10.00"})
    spend(client, "20.00")
    stored = client.get("/api/v1/alerts").json()
    assert [(a["level"], a["sent_at"]) for a in stored] == [("EXCEEDED", None)]


def test_unregistered_tokens_are_dropped(client, fcm):
    client.post("/api/v1/devices", json={"token": "old-phone-" + "y" * 20})
    fcm.dead_tokens.add("old-phone-" + "y" * 20)
    assert client.post("/api/v1/devices/test").json() == {"devices": 2, "delivered": 1}
    assert client.post("/api/v1/devices/test").json() == {"devices": 1, "delivered": 1}


def test_device_registration_is_idempotent(client):
    token = "tok-" + "z" * 30
    assert client.post("/api/v1/devices", json={"token": token, "name": "Pixel"}).status_code == 201
    again = client.post("/api/v1/devices", json={"token": token})
    assert again.status_code == 200 and again.json()["name"] == "Pixel"
    assert client.delete(f"/api/v1/devices/{token}").status_code == 204
    assert client.delete(f"/api/v1/devices/{token}").status_code == 404


def test_alerts_filter_by_month(client, fcm):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "10.00"})
    spend(client, "20.00")
    assert len(client.get("/api/v1/alerts").json()) == 1
    assert len(client.get("/api/v1/alerts", params={"month": "2020-01"}).json()) == 0
    assert client.get("/api/v1/alerts", params={"month": "bad"}).status_code == 422
