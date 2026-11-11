from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Receipt, StatementImport, Transaction

NOW = datetime.now(timezone.utc)


def shift(month, by):
    y, m = map(int, month.split("-"))
    i = y * 12 + m - 1 + by
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


THIS = datetime.now().strftime("%Y-%m")


def add(db, amount, category="Grocery", purpose="Shared", month=THIS, day=10, account="pc_mastercard", **kw):
    ts = datetime.fromisoformat(f"{month}-{day:02d}T17:00:00+00:00")
    kw.setdefault("categorized_by", "rule")
    txn = Transaction(source="test", timestamp=ts, amount=Decimal(amount), merchant_raw=kw.pop("merchant", "LOBLAWS #1"),
                      category=category, purpose=purpose, account_id=account, **kw)
    db.add(txn)
    db.commit()
    return txn


def test_trends_cover_each_month_up_to_now(client, db):
    add(db, "100.00", month=shift(THIS, -2))
    add(db, "40.00", month=THIS)
    add(db, "25.00", category="Health & Fitness", purpose="Alex", month=THIS)
    body = client.get("/api/v1/trends", params={"months": 3}).json()
    assert [m["month"] for m in body] == [shift(THIS, -2), shift(THIS, -1), THIS]
    assert [m["spent"] for m in body] == [100.0, 0.0, 65.0]
    assert body[-1]["by_line"] == {"Grocery": 40.0, "Alex Personal": 25.0}
    alex = client.get("/api/v1/trends", params={"months": 1, "purpose": "Alex"}).json()
    assert alex[0]["spent"] == 25.0


def test_review_compares_months_and_lists_what_needs_a_look(client, db):
    client.put("/api/v1/budgets/Grocery", json={"monthly_limit": "500"})
    for k, amount in ((1, "300.00"), (2, "200.00"), (3, "100.00")):
        add(db, amount, month=shift(THIS, -k))
    add(db, "250.00")
    guess = add(db, "60.00", category="Shopping", merchant="ZXQ HOLDINGS", categorized_by="llm",
                inference_confidence=Decimal("0.40"))
    add(db, "70.00", category="Shopping", merchant="SURE THING", categorized_by="llm", inference_confidence=Decimal("0.90"))
    add(db, "999.00", excluded=True, categorized_by="none")
    db.add(StatementImport(filename="s.csv", bank_format="pcf", account_id="pc_mastercard", status="done", rows=[],
                           period_start=datetime.fromisoformat(f"{THIS}-01").date(),
                           period_end=datetime.fromisoformat(f"{THIS}-20").date()))
    db.add(Receipt(merchant="Loblaws", timestamp=NOW, total=Decimal("5.00")))
    db.commit()

    body = client.get("/api/v1/review", params={"month": THIS}).json()
    assert (body["total_spent"], body["previous_total"], body["average_3m"]) == (380.0, 300.0, 200.0)
    grocery = next(line for line in body["lines"] if line["budget_line"] == "Grocery")
    assert (grocery["spent"], grocery["monthly_limit"], grocery["previous"], grocery["average_3m"]) == (250.0, 500.0, 300.0, 200.0)
    assert [t["id"] for t in body["needs_check"]] == [str(guess.id)]
    pc = next(a for a in body["accounts"] if a["account_id"] == "pc_mastercard")
    assert (pc["transactions"], pc["statement_imported"]) == (3, True)
    assert body["top_merchants"][0] == {"merchant": "Loblaws", "spent": 250.0, "count": 1}
    assert body["unmatched_receipts"] == 1


# --- insight cards ---------------------------------------------------------------------------------

from datetime import date, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.db.models import RecurringExpense  # noqa: E402
from app.services import insights as insight_service  # noqa: E402

TZ = ZoneInfo("America/Toronto")
DAY = date(2026, 11, 12)


def on(db, amount, when, merchant="LOBLAWS #1", category="Grocery", purpose="Shared", **kw):
    kw.setdefault("categorized_by", "rule")
    txn = Transaction(source="test", timestamp=datetime(when.year, when.month, when.day, 12, tzinfo=TZ),
                      amount=Decimal(amount), merchant_raw=merchant, category=category, purpose=purpose,
                      account_id="pc_mastercard", **kw)
    db.add(txn)
    db.commit()
    return txn


def cards(db, today=DAY, purpose=None):
    return insight_service.insights(db, TZ, today, purpose)


def kinds(found):
    return {i.kind for i in found}


def history(db, line_amounts, months=(8, 9, 10), day=5, **kw):
    for m in months:
        for amount in line_amounts:
            on(db, amount, date(2026, m, day), **kw)


def test_nothing_to_say_with_no_history(db):
    on(db, "900.00", date(2026, 11, 3))
    assert cards(db) == []


def test_heading_over_budget_when_running_ahead_of_usual(client, db):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "300"})
    for m in (8, 9, 10):
        on(db, "60.00", date(2026, m, 5), merchant="NEAT CAFE", category="Eating Out")
        on(db, "200.00", date(2026, m, 20), merchant="NEAT CAFE", category="Eating Out")
    on(db, "60.00", date(2026, 11, 4), merchant="NEAT CAFE", category="Eating Out")
    for d in (6, 8, 10, 11):
        on(db, "30.00", date(2026, 11, d), merchant="UBER EATS", category="Eating Out")

    [card] = [i for i in cards(db) if i.kind == "pace"]
    assert card.line == "Eating Out" and card.severity == "warn"
    assert card.title == "Eating Out is heading for $380 of $300"  # 180 so far + the usual 200 after the 12th
    assert "keep to $6.67/day for the 18 days left" in card.body
    assert "Mostly Uber Eats ($120, 4 charges)" in card.body
    assert card.impact == Decimal("80.00")


def test_a_line_that_is_always_over_budget_is_not_news(client, db):
    client.put("/api/v1/budgets/Eating Out", json={"monthly_limit": "100"})
    for m in (8, 9, 10, 11):
        on(db, "50.00", date(2026, m, 5), merchant="NEAT CAFE", category="Eating Out")
        if m < 11:
            on(db, "200.00", date(2026, m, 20), merchant="NEAT CAFE", category="Eating Out")
    assert "pace" not in kinds(cards(db))


def test_over_budget_is_an_alert(client, db):
    client.put("/api/v1/budgets/Household", json={"monthly_limit": "100"})
    history(db, ["40.00"], category="Household", merchant="IKEA")
    on(db, "150.00", date(2026, 11, 2), category="Household", merchant="IKEA")
    [card] = [i for i in cards(db) if i.kind == "pace"]
    assert (card.severity, card.title) == ("alert", "Household is $50.00 over budget")


def test_spike_names_its_merchants(db):
    history(db, ["40.00"], category="Shopping", merchant="WINNERS")
    on(db, "40.00", date(2026, 11, 3), category="Shopping", merchant="WINNERS")
    on(db, "90.00", date(2026, 11, 4), category="Shopping", merchant="AMZN MKTP CA*AB12")
    [card] = [i for i in cards(db) if i.kind == "spike"]
    assert card.title == "Shopping is $90.00 above usual so far"
    assert card.body == "$130 by November 12, against a usual $40.00. Mostly Amazon ($90.00)."


def test_no_spike_without_a_baseline(db):
    history(db, ["40.00"], category="Shopping", merchant="WINNERS")
    on(db, "900.00", date(2026, 11, 3), category="Travel", merchant="AIR CANADA")
    assert "spike" not in kinds(cards(db))


def recurring(db, **kw):
    fields = dict(name="Netflix", merchant_pattern="NETFLIX", category="Subscriptions", amount=Decimal("16.49"),
                  amount_tolerance=Decimal("0.10"), cadence="monthly", anchor_date=date(2026, 8, 3))
    fields.update(kw)
    item = RecurringExpense(**fields)
    db.add(item)
    db.commit()
    return item


def test_a_stopped_subscription_that_still_charges(client, db):
    item = recurring(db, anchor_date=date(2026, 8, 3))
    for m in (9, 10):
        on(db, "16.49", date(2026, m, 3), merchant="NETFLIX.COM", category="Subscriptions", recurring_id=item.id)
    client.patch(f"/api/v1/recurring/{item.id}", json={"active": False})
    db.refresh(item)
    assert item.deactivated_at is not None
    item.deactivated_at -= timedelta(days=1)  # stopped yesterday, so a charge at noon today comes after
    db.commit()
    assert "still_charging" not in kinds(cards(db, today=date.today()))

    late = on(db, "16.49", date.today(), merchant="NETFLIX.COM", category="Subscriptions")
    db.expire_all()
    [card] = [i for i in cards(db, today=date.today()) if i.kind == "still_charging"]
    assert card.title == "Netflix charged $16.49 after you stopped it"
    assert (card.severity, card.transaction_id) == ("alert", late.id)


def test_a_fixed_price_that_went_up(db):
    item = recurring(db)
    for m in (8, 9, 10):
        on(db, "16.49", date(2026, m, 3), merchant="NETFLIX.COM", category="Subscriptions", recurring_id=item.id)
    on(db, "18.99", date(2026, 11, 3), merchant="NETFLIX.COM", category="Subscriptions", recurring_id=item.id)
    [card] = [i for i in cards(db) if i.kind == "price_change"]
    assert card.title == "Netflix went up to $18.99"
    assert card.body == "Was $16.49; that's $30.00 a year more."


def test_a_varying_bill_is_not_a_price_change(db):
    item = recurring(db, name="Fido", merchant_pattern="FIDO", category="Wifi + 5G", amount=Decimal("115"),
                     amount_tolerance=Decimal("0.50"))
    for m, amount in ((8, "106.23"), (9, "158.20"), (10, "115.00")):
        on(db, amount, date(2026, m, 3), merchant="FIDO MOBILE", category="Wifi + 5G", recurring_id=item.id)
    on(db, "140.00", date(2026, 11, 3), merchant="FIDO MOBILE", category="Wifi + 5G", recurring_id=item.id)
    assert "price_change" not in kinds(cards(db))


def test_an_untracked_monthly_charge(client, db):
    for m in (8, 9, 10, 11):
        on(db, "9.99", date(2026, m, 7), merchant="SPOTIFY P12345", category="Subscriptions")
    [card] = [i for i in cards(db) if i.kind == "new_subscription"]
    assert card.title == "Spotify looks like a monthly charge"
    assert card.action == "track_recurring" and card.recurring["cadence"] == "monthly"

    assert client.post("/api/v1/recurring", json=card.recurring).status_code == 201
    assert "new_subscription" not in kinds(cards(db))


def test_bills_overdue_and_coming_up(db):
    recurring(db, name="Rent", merchant_pattern=None, category="Rent + Parking", amount=Decimal("2874"),
              anchor_date=date(2026, 8, 1))
    recurring(db, name="Hydro", merchant_pattern="HYDRO OTTAWA", category="Hydro", amount=Decimal("90"),
              amount_tolerance=Decimal("0.50"), anchor_date=date(2026, 8, 15))
    found = cards(db)
    [overdue] = [i for i in found if i.kind == "overdue_bill"]
    assert overdue.title == "Rent was due Nov 1"
    assert overdue.body == "$2,874. Mark it paid if you paid it another way."
    [coming] = [i for i in found if i.kind == "upcoming_bills"]
    assert coming.title == "1 bill in the next 7 days · $90.00"
    assert coming.body == "Hydro $90.00 on Nov 15."

    on(db, "2874.00", date(2026, 11, 1), merchant="Rent", category="Rent + Parking")  # paid, never linked
    assert "overdue_bill" not in kinds(cards(db))


def test_person_filter_keeps_to_their_lines(client, db):
    client.put("/api/v1/budgets/Alex Personal", json={"monthly_limit": "100"})
    history(db, ["20.00"], category="Shopping", purpose="Alex", merchant="WINNERS")
    on(db, "150.00", date(2026, 11, 2), category="Shopping", purpose="Alex", merchant="WINNERS")
    assert [i.line for i in cards(db, purpose="Alex")] == ["Alex Personal"]
    assert cards(db, purpose="Sam") == []


def test_ranked_by_severity_then_impact_and_dismissable(client, db):
    client.put("/api/v1/budgets/Household", json={"monthly_limit": "100"})
    history(db, ["40.00"], category="Household", merchant="IKEA")
    history(db, ["40.00"], category="Shopping", merchant="WINNERS")
    on(db, "150.00", date.today().replace(day=1), category="Household", merchant="IKEA")
    on(db, "400.00", date.today().replace(day=1), category="Shopping", merchant="WINNERS")

    body = client.get("/api/v1/insights").json()
    assert [c["kind"] for c in body][:2] == ["pace", "spike"]  # the alert first, though the spike is bigger
    assert body[0]["action"] == "open_line" and body[0]["line"] == "Household"

    assert client.post("/api/v1/insights/dismiss", json={"key": body[0]["key"]}).status_code == 204
    assert client.post("/api/v1/insights/dismiss", json={"key": body[0]["key"]}).status_code == 204
    assert body[0]["key"] not in [c["key"] for c in client.get("/api/v1/insights").json()]


def at(db, amount, when, merchant, account="pc_mastercard", **kw):
    txn = Transaction(source="test", timestamp=when.replace(tzinfo=TZ), amount=Decimal(amount), merchant_raw=merchant,
                      category="Eating Out", purpose="Shared", categorized_by="rule", account_id=account, **kw)
    db.add(txn)
    db.commit()
    return txn


def test_a_changed_order_asks_whether_the_first_charge_was_a_hold(client, db):
    hold = at(db, "25.00", datetime(2026, 11, 7, 18), "DoorDash")
    at(db, "10.42", datetime(2026, 11, 7, 20), "DoorDash")
    found = [i for i in cards(db) if i.kind == "replaced_hold"]
    assert [(i.transaction_id, i.action, i.impact) for i in found] == [(hold.id, "remove_hold", Decimal("25.00"))]
    assert "then $10.42 the same day" in found[0].body


def test_holds_need_an_adjusting_merchant_a_different_amount_and_the_same_card(db):
    at(db, "30.00", datetime(2026, 11, 3, 9), "TIM HORTONS")  # not a merchant that adjusts orders
    at(db, "12.00", datetime(2026, 11, 3, 10), "TIM HORTONS")
    at(db, "20.00", datetime(2026, 11, 4, 18), "DoorDash")  # the same amount twice: two orders
    at(db, "20.00", datetime(2026, 11, 4, 19), "DoorDash")
    at(db, "40.00", datetime(2026, 11, 5, 18), "Uber Eats")  # different cards
    at(db, "15.00", datetime(2026, 11, 5, 19), "Uber Eats", account="ws_credit")
    at(db, "11.29", datetime(2026, 11, 6, 8), "DOORDASHDASHPASS")  # the subscription, then an order
    at(db, "30.00", datetime(2026, 11, 6, 19), "DOORDASHDASHPASS")
    at(db, "90.00", datetime(2026, 10, 25, 12), "WALMART.CA")  # a week and a half apart
    at(db, "60.00", datetime(2026, 11, 5, 12), "WALMART.CA")
    assert "replaced_hold" not in kinds(cards(db))


def test_a_statement_confirmed_charge_is_no_hold_and_dismissing_keeps_both(client, db):
    imp = StatementImport(filename="s.csv", bank_format="pcf", account_id="pc_mastercard", status="done", rows=[])
    db.add(imp)
    db.commit()
    at(db, "100.00", datetime(2026, 11, 1, 12), "WALMART.CA", statement_import_id=imp.id)
    at(db, "75.00", datetime(2026, 11, 4, 12), "WALMART.CA")
    assert "replaced_hold" not in kinds(cards(db))

    hold = at(db, "50.00", datetime(2026, 11, 8, 12), "INSTACART")
    at(db, "44.00", datetime(2026, 11, 9, 12), "INSTACART")
    card = next(i for i in cards(db) if i.kind == "replaced_hold")
    assert card.key == f"hold:{hold.id}"
    insight_service.dismiss(db, card.key, None)
    assert "replaced_hold" not in kinds(cards(db))
