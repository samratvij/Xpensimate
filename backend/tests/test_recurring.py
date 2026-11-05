from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.db.models import CategorizationExample, RecurringExpense, Transaction
from app.services.recurring import detect, due_dates, occurrences, paid_due_dates, save_detected

TZ = ZoneInfo("America/Toronto")
TODAY = datetime.now(TZ).date()
MONTH = TODAY.strftime("%Y-%m")


def item(cadence="monthly", anchor=date(2026, 1, 31), **kw):
    defaults = dict(name="X", category="Misc", purpose="Shared", amount=Decimal("10"), amount_tolerance=Decimal("0.25"),
                    active=True)
    return RecurringExpense(cadence=cadence, anchor_date=anchor, **{**defaults, **kw})


def history(db, name, dates, amounts, category, purpose="Shared"):
    for d, a in zip(dates, amounts):
        db.add(CategorizationExample(merchant_key=name, merchant_raw=name, category=category, purpose=purpose,
                                     amount=Decimal(str(-a)), spent_on=d, trip=False))
    db.commit()


def months_back(n, day):
    out = []
    for i in range(n, 0, -1):
        m = (TODAY.month - 1 - i) % 12 + 1
        y = TODAY.year + (TODAY.month - 1 - i) // 12
        out.append(date(y, m, min(day, 28)))
    return out


def test_monthly_due_dates_clamp_to_short_months():
    assert due_dates(item(), date(2026, 2, 1), date(2026, 3, 1)) == [date(2026, 2, 28)]
    assert due_dates(item(), date(2026, 3, 1), date(2026, 4, 1)) == [date(2026, 3, 31)]


def test_biweekly_and_weekly_due_dates():
    biweekly = item("biweekly", date(2026, 10, 2))
    assert due_dates(biweekly, date(2026, 11, 1), date(2026, 12, 1)) == [date(2026, 11, 13), date(2026, 11, 27)]
    weekly = item("weekly", date(2026, 11, 2))
    assert len(due_dates(weekly, date(2026, 11, 1), date(2026, 12, 1))) == 5


def test_detects_steady_series_and_skips_noise(db):
    history(db, "Rent", months_back(6, 1), [2898.21] * 6, "Rent + Parking")
    history(db, "Northside Boxing", months_back(5, 22), [75.4, 75.4, 75.4, 75.4, 75.4], "Health & Fitness", "Alex")
    history(db, "Toronto Hydro", months_back(6, 15), [97.45, 137.27, 152.01, 71.12, 110.0, 125.0], "Hydro")
    # Frequent but irregular groceries; variable Amazon; a membership that stopped half a year ago.
    history(db, "Walmart.Ca", [TODAY - timedelta(days=d) for d in (2, 5, 6, 13, 17, 30, 31, 44)], [100] * 8, "Grocery")
    history(db, "Amazon", months_back(5, 10), [15, 182, 28, 67, 10], "Household")
    history(db, "Old Gym", [date(2025, 1, 5), date(2025, 2, 5), date(2025, 3, 5), date(2025, 4, 5)], [40] * 4, "Health & Fitness")

    found = {d.name: d for d in detect(db, TODAY)}
    assert set(found) == {"Rent", "Northside Boxing", "Toronto Hydro"}
    rent = found["Rent"]
    assert (rent.cadence, rent.amount, rent.merchant_pattern, rent.category) == ("monthly", Decimal("2898.21"), None, "Rent + Parking")
    boxing = found["Northside Boxing"]
    assert (boxing.purpose, boxing.merchant_pattern) == ("Alex", "Northside Boxing")
    assert found["Toronto Hydro"].amount_tolerance > Decimal("0.25")  # utility bills vary


def test_biweekly_detection(db):
    history(db, "Coinamatic", [TODAY - timedelta(days=14 * i) for i in range(5, 0, -1)], [50] * 5, "Laundry")
    assert [(d.name, d.cadence) for d in detect(db, TODAY)] == [("Coinamatic", "biweekly")]


def test_detection_does_not_overwrite_known_items(db, client):
    client.post("/api/v1/recurring", json={"name": "Rent", "category": "Rent + Parking", "amount": "3000",
                                           "cadence": "monthly", "anchor_date": "2026-01-01"})
    history(db, "Rent", months_back(6, 1), [2898.21] * 6, "Rent + Parking")
    assert client.post("/api/v1/recurring/detect").json() == []
    assert [r["amount"] for r in client.get("/api/v1/recurring").json()] == [3000.0]


def make(client, **body):
    payload = {"name": "Northside Boxing", "merchant_pattern": "Northside Boxing", "category": "Health & Fitness",
               "purpose": "Alex", "amount": "75.40", "cadence": "monthly", "anchor_date": TODAY.replace(day=1).isoformat()}
    resp = client.post("/api/v1/recurring", json={**payload, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def push(client, merchant, amount, ref):
    return client.post("/api/v1/transactions/push", json={
        "timestamp": datetime.now(timezone.utc).isoformat(), "amount": amount, "merchant": merchant, "source_ref": ref,
    }).json()


def line(client, name):
    rows = client.get("/api/v1/summary", params={"month": MONTH}).json()["categories"]
    return next(r for r in rows if r["category"] == name)


def test_matching_charge_pays_the_expected_amount(client, db):
    make(client)
    before = line(client, "Alex Personal")
    assert (before["spent"], before["expected"], before["projected"]) == (0.0, 75.4, 75.4)

    txn = push(client, "NORTHSIDE BOXING TORONTO ON", "75.40", "a")
    assert (txn["category"], txn["purpose"], txn["categorized_by"]) == ("Health & Fitness", "Alex", "recurring")
    after = line(client, "Alex Personal")
    assert (after["spent"], after["expected"]) == (75.4, 0.0)
    statuses = [e["status"] for e in client.get("/api/v1/recurring/expected", params={"month": MONTH}).json()]
    assert statuses == ["paid"]


def test_charge_with_a_very_different_amount_is_not_a_payment(client):
    make(client)
    push(client, "NORTHSIDE BOXING", "300.00", "a")
    assert line(client, "Alex Personal")["expected"] == 75.4


def test_mark_paid_for_offline_payments(client):
    rent = make(client, name="Rent", merchant_pattern=None, category="Rent + Parking", purpose="Shared", amount="2898.21")
    client.put("/api/v1/budgets/Rent + Parking", json={"monthly_limit": "2800"})
    assert line(client, "Rent + Parking")["projected_over_limit"] is True

    txn = client.post(f"/api/v1/recurring/{rent['id']}/paid", json={}).json()
    assert (txn["merchant_raw"], txn["amount"], txn["budget_line"], txn["categorized_by"]) == (
        "Rent", 2898.21, "Rent + Parking", "manual",
    )
    rent_line = line(client, "Rent + Parking")
    assert (rent_line["spent"], rent_line["expected"], rent_line["over_limit"]) == (2898.21, 0.0, True)
    assert client.post(f"/api/v1/recurring/{rent['id']}/paid", json={}).status_code == 409


def test_overdue_and_upcoming(db):
    biweekly = item("biweekly", date(2026, 11, 2), name="Cleaner", amount=Decimal("80"))
    db.add(biweekly)
    db.commit()
    occ = occurrences(db, date(2026, 11, 1), date(2026, 12, 1), today=date(2026, 11, 18))
    assert [(o.due, o.status) for o in occ] == [(date(2026, 11, 2), "overdue"), (date(2026, 11, 16), "upcoming"),
                                                (date(2026, 11, 30), "upcoming")]


def pay(db, recurring, on, amount="2150.00"):
    db.add(Transaction(source="manual", source_ref=f"pay:{on}", timestamp=datetime.combine(on, datetime.min.time(), TZ)
                       + timedelta(hours=12), amount=Decimal(amount), merchant_raw=recurring.name,
                       category=recurring.category, recurring_id=recurring.id))
    db.commit()


def statuses(db, month_start, today):
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    return [(o.due, o.status) for o in occurrences(db, month_start, month_end, today=today)]


def test_rent_paid_early_pays_next_months_due_date(db):
    rent = item(anchor=date(2026, 9, 1), name="Rent", category="Rent + Parking", amount=Decimal("2150"))
    db.add(rent)
    db.commit()
    pay(db, rent, date(2026, 10, 1))
    pay(db, rent, date(2026, 10, 30))  # November's rent, two days early
    assert statuses(db, date(2026, 10, 1), date(2026, 11, 5)) == [(date(2026, 10, 1), "paid")]
    assert statuses(db, date(2026, 11, 1), date(2026, 11, 5)) == [(date(2026, 11, 1), "paid")]
    assert statuses(db, date(2026, 12, 1), date(2026, 11, 5)) == [(date(2026, 12, 1), "upcoming")]


def test_late_payment_pays_its_own_month_not_the_next(db):
    phone = item(anchor=date(2026, 9, 28), name="Phone", amount=Decimal("80"))
    db.add(phone)
    db.commit()
    pay(db, phone, date(2026, 11, 2), "80")  # October 28's bill, paid five days late
    assert statuses(db, date(2026, 10, 1), date(2026, 11, 20)) == [(date(2026, 10, 28), "paid")]
    assert statuses(db, date(2026, 11, 1), date(2026, 11, 20)) == [(date(2026, 11, 28), "upcoming")]


def test_each_payment_pays_one_due_date_and_far_off_ones_pay_none():
    monthly = item(anchor=date(2026, 1, 15))
    # Two payments a day apart: the second goes to the next nearest due date only if it's within reach.
    assert paid_due_dates(monthly, [date(2026, 10, 15), date(2026, 10, 16)], date(2026, 10, 1), date(2026, 12, 1)) == {
        date(2026, 10, 15)}
    weekly = item("weekly", date(2026, 11, 2))
    assert paid_due_dates(weekly, [date(2026, 11, 2), date(2026, 11, 8), date(2026, 11, 16)],
                          date(2026, 11, 1), date(2026, 11, 30)) == {date(2026, 11, 2), date(2026, 11, 9),
                                                                      date(2026, 11, 16)}


def test_past_months_expect_nothing_and_inactive_items_are_ignored(client):
    item_ = make(client)
    last_year = f"{TODAY.year - 1}-{TODAY.month:02d}"
    assert client.get("/api/v1/summary", params={"month": last_year}).json()["total_expected"] == 0.0
    client.patch(f"/api/v1/recurring/{item_['id']}", json={"active": False})
    assert client.get("/api/v1/summary", params={"month": MONTH}).json()["total_expected"] == 0.0


def test_who_filter_applies_to_expected(client):
    make(client)
    assert client.get("/api/v1/summary", params={"month": MONTH, "purpose": "Sam"}).json()["total_expected"] == 0.0
    assert client.get("/api/v1/summary", params={"month": MONTH, "purpose": "Alex"}).json()["total_expected"] == 75.4


def test_editing_marks_item_as_user_owned(client):
    created = make(client)
    patched = client.patch(f"/api/v1/recurring/{created['id']}", json={"amount": "110.00"}).json()
    assert (patched["amount"], patched["origin"]) == (110.0, "user")
    assert client.delete(f"/api/v1/recurring/{created['id']}").status_code == 204


def test_new_item_claims_a_charge_that_already_arrived(client):
    txn = push(client, "NORTHSIDE BOXING", "75.40", "early")
    assert txn["categorized_by"] != "recurring"
    make(client)
    assert line(client, "Alex Personal")["expected"] == 0.0
    detail = client.get(f"/api/v1/transactions/{txn['id']}").json()
    assert (detail["category"], detail["purpose"], detail["categorized_by"]) == ("Health & Fitness", "Alex", "recurring")
