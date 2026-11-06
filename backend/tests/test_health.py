from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.daemons.gmail_sync import process_history, record_error
from app.db.models import Device, GmailSyncState, RawIngestLog
from tests.gmail_fakes import FakeGmail, history_page

EMAIL = "me@example.com"


def checks(client) -> dict[str, dict]:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    return {c["name"]: c for c in resp.json()["checks"]}


@pytest.fixture
def gmail_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "gcp_project_id", "test-project")


def test_health_needs_a_token():
    from fastapi.testclient import TestClient
    from main import app
    assert TestClient(app).get("/api/v1/health").status_code == 401


def test_fresh_server(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "gcp_project_id", "")
    result = checks(client)
    assert result["Database"]["status"] == "ok"
    assert result["Gmail bank alerts"]["status"] == "off"
    assert result["AI model"]["status"] == "warning"  # tests run without a key or model server
    assert result["Push notifications"]["status"] in ("off", "warning")


def test_gmail_worker_never_ran(client, gmail_on):
    assert checks(client)["Gmail bank alerts"]["status"] == "error"


def test_gmail_ok_after_a_check(client, db, gmail_on):
    db.add(GmailSyncState(email=EMAIL, history_id=100, watch_expires_at=datetime.now(timezone.utc) + timedelta(days=6)))
    db.commit()
    process_history(db, FakeGmail([], history_pages=[history_page(history_id=110)]), EMAIL)
    gmail = checks(client)["Gmail bank alerts"]
    assert gmail["status"] == "ok"
    assert "just now" in gmail["detail"]


def test_gmail_stale_worker_is_an_error(client, db, gmail_on):
    db.add(GmailSyncState(email=EMAIL, history_id=100, last_checked_at=datetime.now(timezone.utc) - timedelta(hours=3)))
    db.commit()
    gmail = checks(client)["Gmail bank alerts"]
    assert gmail["status"] == "error"
    assert "3 h ago" in gmail["detail"]


def test_gmail_failure_after_last_success_is_shown(client, db, gmail_on):
    db.add(GmailSyncState(email=EMAIL, history_id=100, last_checked_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
    db.commit()
    record_error(db, EMAIL, RuntimeError("invalid_grant"))
    gmail = checks(client)["Gmail bank alerts"]
    assert gmail["status"] == "error"
    assert "invalid_grant" in gmail["detail"]


def test_expired_watch_is_a_warning(client, db, gmail_on):
    now = datetime.now(timezone.utc)
    db.add(GmailSyncState(email=EMAIL, history_id=100, last_checked_at=now, watch_expires_at=now - timedelta(hours=1)))
    db.commit()
    assert checks(client)["Gmail bank alerts"]["status"] == "warning"


def test_unread_bank_emails_are_a_warning(client, db):
    db.add(RawIngestLog(source="gmail_pubsub", payload={"parse_status": "unparsed"}))
    db.add(RawIngestLog(source="gmail_pubsub", payload={"parse_status": "unknown_card"}))
    db.add(RawIngestLog(source="gmail_pubsub", payload={"gmail_id": "fine"}))
    db.commit()
    parsing = checks(client)["Reading bank emails"]
    assert parsing["status"] == "warning"
    assert parsing["detail"].startswith("2 ")


def test_push_with_key_and_phone(client, db, tmp_path, monkeypatch):
    key = tmp_path / "fcm.json"
    key.write_text("{}")
    monkeypatch.setattr(get_settings(), "fcm_credentials", str(key))
    assert checks(client)["Push notifications"]["status"] == "warning"
    db.add(Device(token="abc", name="Pixel"))
    db.commit()
    assert checks(client)["Push notifications"]["status"] == "ok"


def test_bank_email_read_by_a_later_retry_is_not_counted(client, db):
    from tests.test_gmail_sync import TANGERINE
    from app.daemons.gmail_sync import process_message
    db.add(RawIngestLog(source="gmail_pubsub", payload={"gmail_id": "t1", "parse_status": "unparsed"}))
    db.commit()
    assert checks(client)["Reading bank emails"]["status"] == "warning"
    process_message(db, FakeGmail([TANGERINE]), "t1")
    assert checks(client)["Reading bank emails"]["status"] == "ok"


def test_phone_capture_reports_last_purchase(client):
    assert checks(client)["Phone notification capture"]["status"] == "off"
    resp = client.post("/api/v1/notifications", json={
        "package": "com.wealthsimple.trade", "key": "k1", "title": "NORTHSIDE BOXING ",
        "text": "You made a recurring payment for $75.40 with your credit card.",
        "posted_at": "2026-11-04T11:06:50Z",
    })
    assert resp.json()["status"] == "created"
    phone = checks(client)["Phone notification capture"]
    assert phone["status"] == "ok"
    assert "NORTHSIDE BOXING" in phone["detail"]
