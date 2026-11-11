from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.db.models import CategorizationRule, RecurringExpense, Transaction
from app.services import renewals
from app.services.recurring import link_transaction, occurrences, subscription_name, track_new_subscription

TZ = ZoneInfo("America/Toronto")
TODAY = datetime.now(TZ).date()


def charge(db, merchant, amount, day, category="Subscriptions"):
    txn = Transaction(source="manual", timestamp=datetime.combine(day, time(12), tzinfo=TZ), amount=Decimal(amount),
                      txn_type="PURCHASE", merchant_raw=merchant, category=category, purpose="Alex")
    db.add(txn)
    db.flush()
    return txn


def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(renewals, "send_to_devices", lambda db, title, body, data: calls.append((title, body)) or 1)
    return calls


def test_subscription_name_skips_the_app_store():
    assert subscription_name("GOOGLE *GOOGLE*ONE 01M") == "Google One"
    assert subscription_name("GOOGLE *CapCut Video E") == "Capcut Video E"
    assert subscription_name("ANTHROPIC") == "Anthropic"
    assert subscription_name("GITHUB  INC. SAN FRANCISCO CA US") == "Github"


def test_first_subscription_charge_starts_a_monthly_bill(db):
    day = date(2026, 11, 8)
    txn = charge(db, "GOOGLE *GOOGLE*ONE 01M", "30.50", day)
    item = track_new_subscription(db, txn)
    db.commit()
    assert (item.name, item.cadence, item.anchor_date, item.amount, item.origin) == (
        "Google One", "monthly", day, Decimal("30.50"), "detected")
    assert txn.recurring_id == item.id
    dec = occurrences(db, date(2026, 12, 1), date(2027, 1, 1), date(2026, 11, 9))
    assert [(o.item.name, o.due, o.status) for o in dec] == [("Google One", date(2026, 12, 8), "upcoming")]
    # Next month's charge pays it rather than starting another.
    nxt = Transaction(source="manual", timestamp=datetime(2026, 12, 8, 12, tzinfo=TZ), amount=Decimal("30.50"),
                      txn_type="PURCHASE", merchant_raw="GOOGLE *GOOGLE*ONE 01M", category="Subscriptions",
                      purpose="Alex")
    assert link_transaction(db, nxt) == item


def test_only_subscriptions_and_not_irregular_or_stopped_ones(db):
    assert track_new_subscription(db, charge(db, "LOBLAWS", "80", TODAY, category="Grocery")) is None
    # Irregular: charged a different amount two weeks ago.
    charge(db, "UPWORK", "56.80", TODAY - timedelta(days=14))
    assert track_new_subscription(db, charge(db, "UPWORK", "70.10", TODAY)) is None
    # Same amount a month ago: a subscription nobody tracked yet.
    charge(db, "CLAUDE", "31.64", TODAY - timedelta(days=30))
    assert track_new_subscription(db, charge(db, "CLAUDE", "31.64", TODAY)).name == "Claude"
    # Stopped on purpose: a new charge doesn't bring it back as a new bill.
    db.add(RecurringExpense(name="Spotify", merchant_pattern="Spotify", category="Subscriptions", purpose="Alex",
                            amount=Decimal("14.34"), amount_tolerance=Decimal("0.10"), cadence="monthly",
                            anchor_date=TODAY, active=False))
    db.flush()
    assert track_new_subscription(db, charge(db, "SPOTIFY", "14.34", TODAY)) is None


def test_second_plan_from_the_same_service_gets_its_own_bill(db):
    charge(db, "APPLE.COM", "56.49", TODAY - timedelta(days=30))
    charge(db, "APPLE.COM", "4.51", TODAY - timedelta(days=31))
    big = track_new_subscription(db, charge(db, "APPLE.COM", "56.49", TODAY))
    small = track_new_subscription(db, charge(db, "APPLE.COM", "4.51", TODAY))
    assert (big.name, small.name) == ("Apple.Com", "Apple.Com $4.51")


def test_incoming_charge_is_tracked_and_announced(client, db, monkeypatch):
    calls = sent(monkeypatch)
    db.add(CategorizationRule(merchant_pattern="GOOGLE", target_category="Subscriptions", target_purpose="Alex"))
    db.commit()
    txn = client.post("/api/v1/transactions/push", json={
        "timestamp": datetime.now(TZ).isoformat(), "amount": "30.50", "merchant": "GOOGLE *GOOGLE*ONE 01M",
        "source_ref": "g1",
    }).json()
    assert txn["category"] == "Subscriptions"
    assert [r["name"] for r in client.get("/api/v1/recurring").json()] == ["Google One"]
    assert len(calls) == 1 and calls[0][0] == "New subscription: Google One"


def test_reminder_a_week_ahead_goes_out_once(db, monkeypatch):
    calls = sent(monkeypatch)
    today = date(2026, 12, 1)
    db.add(RecurringExpense(name="Google One", merchant_pattern="Google One", category="Subscriptions",
                            purpose="Alex", amount=Decimal("30.50"), amount_tolerance=Decimal("0.10"),
                            cadence="monthly", anchor_date=date(2026, 11, 8)))
    db.add(RecurringExpense(name="Rent", category="Rent + Parking", purpose="Shared", amount=Decimal("2650"),
                            cadence="monthly", anchor_date=date(2026, 11, 6)))
    db.commit()
    assert renewals.send_renewal_reminders(db, date(2026, 11, 30)) == []  # 8 days out: not yet
    assert [i.name for i in renewals.send_renewal_reminders(db, today)] == ["Google One"]
    assert calls == [("Google One renews on Tue Dec 8", "$30.50. Cancel before then if you don't want it.")]
    assert renewals.send_renewal_reminders(db, today + timedelta(days=1)) == []
