from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import BalanceSnapshot, BudgetAlert, SavingsGoal, SavingsTarget, Transaction
from app.services import goals


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(goals, "send_to_devices", lambda db, title, body, data: sent.append((title, body, data)) or 1)
    return sent


def move(db, src, dst, amount, day):
    db.add(Transaction(source="test", timestamp=datetime.fromisoformat(f"{day}T12:00:00-04:00"), amount=Decimal(amount),
                       merchant_raw="move", txn_type="TRANSFER", excluded=True, category="Transfer", purpose="Alex",
                       account_id=src, counterpart_account_id=dst))
    db.commit()


def balance(db, account, amount, day="2026-11-09"):
    db.add(BalanceSnapshot(account_id=account, as_of=date.fromisoformat(day), balance=Decimal(amount)))
    db.commit()


def target_set_before(db, when=datetime(2026, 9, 1, tzinfo=timezone.utc)):
    db.get(SavingsTarget, "household").created_at = when
    db.commit()


# --- the monthly target ---------------------------------------------------------------------------

def test_the_plan_starts_with_5000_a_month_and_the_house_goal(client):
    body = client.get("/api/v1/savings").json()
    assert (body["target"]["monthly_amount"], body["target"]["alert_day"], body["target"]["warning_percent"]) == (5000.0, 25, 80.0)
    [house] = body["goals"]
    assert (house["name"], house["target_amount"], house["target_date"], house["account_kinds"]) == (
        "House", 100000.0, "2027-03-31", ["savings"])


def test_target_progress_counts_money_moved_not_income_left(db):
    move(db, "ws_cash", "pc_savings", "2000.00", "2026-10-02")
    move(db, "ws_cash", "ws_fhsa", "1500.00", "2026-10-16")
    move(db, "pc_savings", "ws_cash", "500.00", "2026-10-20")  # taken back out
    move(db, "ws_cash", "ws_joint", "900.00", "2026-10-21")  # between chequing accounts: not saving
    early = goals.target_progress(db, "2026-10", date(2026, 10, 20))
    assert (early.saved, early.percent, early.remaining, early.status) == (Decimal("3000.00"), 60.0, Decimal("2000.00"), "on_track")
    # From the 25th, under 80% of the target is behind; a finished month short of it is behind too.
    assert goals.target_progress(db, "2026-10", date(2026, 10, 25)).status == "behind"
    assert goals.target_progress(db, "2026-10", date(2026, 11, 3)).status == "behind"
    move(db, "ws_cash", "pc_savings", "2000.00", "2026-10-28")
    assert goals.target_progress(db, "2026-10", date(2026, 10, 29)).status == "met"


def test_behind_alert_fires_once_on_or_after_the_alert_day(db, pushes):
    move(db, "ws_cash", "pc_savings", "1000.00", "2026-10-02")
    assert goals.check_target(db, date(2026, 10, 24)) == []
    [alert] = goals.check_target(db, date(2026, 10, 26))
    assert (alert.budget_line, alert.level, alert.spent, alert.monthly_limit) == ("Savings target", "BEHIND", Decimal("1000.00"), Decimal("5000.00"))
    title, body, data = pushes[0]
    assert title == "Behind on savings: 20% of $5,000"
    assert body == "$1,000.00 moved to savings and investments in October; $4,000.00 to go to reach the target."
    assert data["event"] == "SAVINGS_BEHIND"
    assert goals.check_target(db, date(2026, 10, 27)) == []
    assert alert.sent_at is not None


def test_a_month_that_finished_short_gets_a_note(db, pushes):
    target_set_before(db)
    move(db, "ws_cash", "pc_savings", "4200.00", "2026-10-02")
    [note] = goals.check_target(db, date(2026, 11, 2))
    assert (note.month, note.level) == ("2026-10", "SHORT")
    assert pushes[0][0] == "October savings finished short"
    assert goals.check_target(db, date(2026, 11, 3)) == []


def test_no_note_for_a_month_before_the_target_existed(db, pushes):
    target_set_before(db, datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert goals.check_target(db, date(2026, 11, 2)) == []


def test_no_alerts_without_a_target(client, db, pushes):
    assert client.put("/api/v1/savings/target", json={"monthly_amount": "0"}).status_code == 200
    assert goals.check_target(db, date(2026, 10, 26)) == []
    assert client.get("/api/v1/savings").json()["progress"] is None


def test_summary_shows_the_target(client, db):
    move(db, "ws_cash", "pc_savings", "2500.00", "2026-10-02")
    savings = client.get("/api/v1/summary", params={"month": "2026-10"}).json()["savings"]
    assert (savings["moved"], savings["target"], savings["target_percent"], savings["target_status"]) == (2500.0, 5000.0, 50.0, "behind")
    assert client.get("/api/v1/summary", params={"month": "2026-10", "purpose": "Alex"}).json()["savings"] is None


def test_target_can_be_changed(client):
    resp = client.put("/api/v1/savings/target", json={"monthly_amount": "6000.00", "alert_day": 20, "warning_percent": "75"})
    assert resp.json() == {"monthly_amount": 6000.0, "alert_day": 20, "warning_percent": 75.0}
    assert client.put("/api/v1/savings/target", json={"monthly_amount": "1", "alert_day": 31}).status_code == 422


def test_savings_alerts_are_listed_with_budget_alerts(client, db, pushes):
    move(db, "ws_cash", "pc_savings", "1000.00", "2026-10-02")
    goals.check_target(db, date(2026, 10, 26))
    [alert] = client.get("/api/v1/alerts").json()
    assert (alert["budget_line"], alert["level"]) == ("Savings target", "BEHIND")


# --- goals ----------------------------------------------------------------------------------------

def house(db) -> SavingsGoal:
    return db.scalar(select(SavingsGoal).where(SavingsGoal.name == "House"))


def test_house_goal_counts_savings_and_fhsa_balances(db):
    balance(db, "pc_savings", "37812.40")
    balance(db, "ws_fhsa", "18250.10")
    balance(db, "ws_sam_fhsa", "7400.50")
    balance(db, "ws_tfsa", "5450.30")  # not part of it
    balance(db, "rbc_sam_savings", "480.00", "2026-10-31")
    move(db, "pc_money", "pc_savings", "300.00", "2026-11-05")
    move(db, "pc_savings", "ws_fhsa", "1000.00", "2026-11-06")  # within the goal's accounts: no change

    p = goals.goal_progress(db, house(db), date(2026, 11, 10))
    assert p.current == Decimal("63943.00")
    assert (p.remaining, p.months_left, p.required_monthly) == (Decimal("36057.00"), 5, Decimal("7211.40"))
    assert p.percent == 63.9
    assert p.moved_this_month == Decimal("300.00")
    assert p.oldest_balance == date(2026, 10, 31)
    counted = {a.id for a, _, _ in p.accounts}
    assert {"pc_savings", "ws_fhsa", "ws_sam_fhsa", "tangerine_savings"} <= counted and "ws_tfsa" not in counted


def test_goal_reached_or_past_its_date(db):
    balance(db, "pc_savings", "120000.00")
    reached = goals.goal_progress(db, house(db), date(2026, 11, 10))
    assert (reached.remaining, reached.required_monthly, reached.percent) == (Decimal("0.00"), None, 120.0)
    late = goals.goal_progress(db, house(db), date(2027, 4, 2))
    assert (late.months_left, late.required_monthly) == (0, None)


def test_goals_crud(client):
    resp = client.post("/api/v1/savings/goals", json={
        "name": "Emergency fund", "target_amount": "15000", "target_date": "2027-12-31", "account_ids": ["ws_savings"]})
    assert resp.status_code == 201
    goal = resp.json()
    assert ([a["account_id"] for a in goal["accounts"]], goal["current"]) == (["ws_savings"], 0.0)
    assert goal["required_monthly"] > 0
    assert client.post("/api/v1/savings/goals", json={"name": "X", "target_amount": "1", "target_date": "2027-01-01"}).status_code == 422
    assert client.post("/api/v1/savings/goals", json={"name": "X", "target_amount": "1", "target_date": "2027-01-01",
                                                      "account_ids": ["nope"]}).status_code == 422
    patched = client.patch(f"/api/v1/savings/goals/{goal['id']}", json={"target_amount": "20000", "account_kinds": ["investment"]}).json()
    assert patched["target_amount"] == 20000.0 and "ws_tfsa" in [a["account_id"] for a in patched["accounts"]]
    assert client.patch(f"/api/v1/savings/goals/{goal['id']}", json={"account_kinds": [], "account_ids": []}).status_code == 422
    assert client.delete(f"/api/v1/savings/goals/{goal['id']}").status_code == 204
    assert [g["name"] for g in client.get("/api/v1/savings").json()["goals"]] == ["House"]
