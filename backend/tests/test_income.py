from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.daemons.gmail_sync import process_message
from app.daemons.parsers import rbc, wealthsimple, wealthsimple_email
from app.db.models import IncomeSource, Transaction
from app.services import income
from tests.fixtures import alerts, screens
from tests.gmail_fakes import FakeGmail, gmail_message
from tests.test_statements import screenshot  # noqa: F401 - fixture

TZ = ZoneInfo("America/Toronto")


def _ms(when: str) -> int:
    return int(datetime.fromisoformat(when).timestamp() * 1000)


WS_PAY = gmail_message("ws1", "Wealthsimple <notifications@o.wealthsimple.com>", alerts.WS_DIRECT_DEPOSIT_SUBJECT,
                       alerts.WS_DIRECT_DEPOSIT_BODY, internal_ms=_ms("2026-10-29T21:04:24+00:00"))
WS_TRANSFER = gmail_message("ws2", "Wealthsimple <notifications@o.wealthsimple.com>", alerts.WS_TRANSFER_SUBJECT,
                            alerts.WS_TRANSFER_BODY)
RBC_ALERTS = "RBC Royal Bank Alerts <rbcroyalbankalerts@alerts.rbc.com>"


def rbc_deposit(message_id, body, when="2026-10-29T14:00:00+00:00"):
    return gmail_message(message_id, RBC_ALERTS, alerts.RBC_DEPOSIT_SUBJECT, body, internal_ms=_ms(when))


def incomes(db):
    return list(db.scalars(select(Transaction).where(Transaction.txn_type == "INCOME").order_by(Transaction.timestamp)))


def add_purchase(db, amount, when="2026-10-20T12:00:00+00:00", category="Grocery", purpose="Shared"):
    db.add(Transaction(source="test", timestamp=datetime.fromisoformat(when), amount=Decimal(amount),
                       merchant_raw="Somewhere", category=category, purpose=purpose))
    db.commit()


def summary(client, month="2026-10", **params):
    return client.get("/api/v1/summary", params={"month": month, **params}).json()


# --- parsing --------------------------------------------------------------------------------------

def test_wealthsimple_direct_deposit_email_is_pay():
    assert wealthsimple_email.is_transaction(alerts.WS_DIRECT_DEPOSIT_SUBJECT)
    alert = wealthsimple_email.parse(alerts.WS_DIRECT_DEPOSIT_SUBJECT, alerts.WS_DIRECT_DEPOSIT_BODY)
    assert (alert.amount, alert.merchant, alert.account_id, alert.income) == (Decimal("3250.40"), "NORTHWIND", "ws_cash", True)


def test_insurance_claim_email_is_money_back(db):
    body = alerts.WS_DIRECT_DEPOSIT_BODY.replace("$3,250.40", "$190.00").replace("From: NORTHWIND", "From: MANULIFE")
    alert = wealthsimple_email.parse(alerts.WS_DIRECT_DEPOSIT_SUBJECT, body)
    assert (alert.amount, alert.merchant, alert.income) == (Decimal("-190.00"), "Manulife", False)
    message = gmail_message("ws9", "Wealthsimple <notifications@o.wealthsimple.com>", alerts.WS_DIRECT_DEPOSIT_SUBJECT, body)
    assert process_message(db, FakeGmail([message]), "ws9") == "created"
    [claim] = db.scalars(select(Transaction))
    assert (claim.txn_type, claim.amount, claim.excluded) == ("REFUND", Decimal("-190.00"), False)


@pytest.mark.parametrize("subject", ["You earned a dividend", "Your order has been filled",
                                     "Get 5% cash back on your Wealthsimple credit card"])
def test_other_wealthsimple_mail_is_not_read(subject):
    assert not wealthsimple_email.is_transaction(subject)


@pytest.mark.parametrize(
    "text",
    [
        "A direct deposit of $3,250.40 from NORTHWIND has arrived",
        "You received $3,250.40 from NORTHWIND in your Cash account",
        "Your payroll deposit of $3,250.40 from NORTHWIND is here",
    ],
)
def test_wealthsimple_deposit_notifications(text):
    alert = wealthsimple.parse("Wealthsimple", text)
    assert (alert.amount, alert.merchant, alert.account_id, alert.income) == (Decimal("3250.40"), "NORTHWIND", "ws_cash", True)


def test_rbc_deposit_alert():
    assert rbc.is_transaction(alerts.RBC_DEPOSIT_SUBJECT)
    alert = rbc.parse(alerts.RBC_DEPOSIT_SUBJECT, alerts.RBC_DEPOSIT_BODY)
    assert (alert.amount, alert.merchant, alert.card_suffix, alert.income) == (
        Decimal("2980.20"), "Payroll Deposit PAIE/PAYROLL", "4150", True)
    bare = rbc.parse(alerts.RBC_DEPOSIT_SUBJECT, alerts.RBC_DEPOSIT_NO_PAYER_BODY)
    assert (bare.amount, bare.merchant, bare.card_suffix) == (Decimal("2980.20"), "Deposit", "4150")
    # Card alerts are unchanged.
    assert not rbc.parse(alerts.RBC_PURCHASE_SUBJECT, alerts.RBC_PURCHASE_BODY).income


# --- ingest ---------------------------------------------------------------------------------------

def test_wealthsimple_pay_email_becomes_alexs_income(db, client):
    assert process_message(db, FakeGmail([WS_PAY]), "ws1") == "created"
    [pay] = incomes(db)
    assert (pay.account_id, pay.amount, pay.category, pay.purpose, pay.excluded) == (
        "ws_cash", Decimal("3250.40"), "Salary", "Alex", True)
    assert pay.income_source_id == db.scalar(select(IncomeSource.id).where(IncomeSource.earner == "Alex"))
    assert pay.reconciliation_status == "SETTLED"


@pytest.fixture(autouse=True)
def alexs_mailbox(monkeypatch):
    """The Gmail account is Alex's: "You made a transfer" means Alex."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "gmail_owner", "Alex")


def test_transfer_email_is_not_income(db):
    assert process_message(db, FakeGmail([WS_TRANSFER]), "ws2") == "created"
    assert incomes(db) == []


def test_rbc_pay_lands_on_sams_chequing(db):
    assert process_message(db, FakeGmail([rbc_deposit("r1", alerts.RBC_DEPOSIT_BODY)]), "r1") == "created"
    # An alert without the payer is still her pay: right account, right amount.
    later = rbc_deposit("r2", alerts.RBC_DEPOSIT_NO_PAYER_BODY, when="2026-11-12T14:00:00+00:00")
    assert process_message(db, FakeGmail([later]), "r2") == "created"
    assert [(t.account_id, t.purpose, t.category) for t in incomes(db)] == [("rbc_sam_cheq", "Sam", "Salary")] * 2


def test_deposit_that_isnt_pay_is_not_income(db):
    small = rbc_deposit("r3", alerts.RBC_SMALL_DEPOSIT_BODY)
    assert process_message(db, FakeGmail([small]), "r3") == "ignored"
    assert incomes(db) == []


def test_pay_from_phone_and_email_is_stored_once(client, db):
    resp = client.post("/api/v1/notifications", json={
        "package": "com.wealthsimple.trade", "title": "Direct deposit",
        "text": "A direct deposit of $3,250.40 from NORTHWIND has arrived",
        "posted_at": "2026-10-29T21:03:00+00:00", "key": "0|com.wealthsimple.trade|7|null|1"})
    assert resp.json()["status"] == "created"
    assert resp.json()["transaction"]["txn_type"] == "INCOME"
    assert process_message(db, FakeGmail([WS_PAY]), "ws1") == "duplicate"
    assert len(incomes(db)) == 1


def test_e_transfer_received_is_not_income(client, db):
    resp = client.post("/api/v1/notifications", json={
        "package": "com.wealthsimple.trade", "title": "Wealthsimple",
        "text": "You received $500.00 by e-Transfer from Alex", "posted_at": "2026-10-29T21:03:00+00:00", "key": "k"})
    assert resp.json()["status"] == "unparsed"  # kept as raw text only, like any money text that isn't spending
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0


# --- totals ---------------------------------------------------------------------------------------

def test_income_is_in_the_summary_and_not_in_spending(client, db):
    process_message(db, FakeGmail([WS_PAY]), "ws1")
    process_message(db, FakeGmail([rbc_deposit("r1", alerts.RBC_DEPOSIT_BODY)]), "r1")
    add_purchase(db, "120.00")
    add_purchase(db, "30.00", category="Personal Care", purpose="Sam")

    body = summary(client)
    assert body["total_spent"] == 150.0
    assert {row["category"] for row in body["categories"]} == {"Grocery", "Sam Personal"}
    assert body["income"] == {
        "received": 6230.60, "by_person": {"Alex": 3250.40, "Sam": 2980.20},
        # October is over: nothing more is expected.
        "expected": 0.0, "expected_by_person": {},
        "left_after_spending": 6080.60, "projected_left": 6080.60,
    }

    sam = summary(client, purpose="Sam")
    assert (sam["total_spent"], sam["income"]["received"]) == (30.0, 2980.20)
    assert sam["income"]["by_person"] == {"Sam": 2980.20}


def test_income_cant_be_turned_into_spending(client, db):
    process_message(db, FakeGmail([WS_PAY]), "ws1")
    txn_id = str(incomes(db)[0].id)
    patch = lambda body: client.patch(f"/api/v1/transactions/{txn_id}", json=body)  # noqa: E731
    assert patch({"excluded": False}).status_code == 422
    assert patch({"category": "Grocery"}).status_code == 422
    assert patch({"purpose": "Parents"}).status_code == 422
    assert client.put(f"/api/v1/transactions/{txn_id}/items", json={"items": []}).status_code == 422
    ok = patch({"category": "Other income", "purpose": "Shared", "note": "bonus"})
    assert ok.status_code == 200
    assert (ok.json()["category"], ok.json()["purpose"], ok.json()["excluded"]) == ("Other income", "Shared", True)
    assert summary(client)["income"]["by_person"] == {"Shared": 3250.40}


def test_manual_income_and_the_month_view(client, db):
    resp = client.post("/api/v1/income", json={
        "amount": "250.00", "received_on": "2026-10-10", "earner": "Sam", "payer": "Tax refund",
        "category": "Other income", "account_id": "rbc_sam_cheq", "note": "CRA"})
    assert resp.status_code == 201
    assert (resp.json()["txn_type"], resp.json()["purpose"], resp.json()["edited_by"]) == ("INCOME", "Sam", "Alex")
    process_message(db, FakeGmail([WS_PAY]), "ws1")

    month = client.get("/api/v1/income", params={"month": "2026-10"}).json()
    assert (month["received"], month["by_person"]) == (3500.40, {"Sam": 250.0, "Alex": 3250.40})
    assert [d["merchant_raw"] for d in month["deposits"]] == ["NORTHWIND", "Tax refund"]
    # Alex's pay dates are the 15th and the last day; the Oct 29 deposit paid the 31st.
    alex = [(p["day"], p["status"]) for p in month["pay_dates"] if p["earner"] == "Alex"]
    assert alex == [("2026-10-15", "late"), ("2026-10-31", "received")]

    only = client.get("/api/v1/income", params={"month": "2026-10", "earner": "Sam"}).json()
    assert only["received"] == 250.0
    assert {p["earner"] for p in only["pay_dates"]} == {"Sam"}


def test_manual_income_needs_a_known_account(client):
    body = {"amount": "1.00", "received_on": "2026-10-10", "earner": "Alex", "payer": "X", "account_id": "nope"}
    assert client.post("/api/v1/income", json=body).status_code == 422


# --- pay schedule ---------------------------------------------------------------------------------

def _source(db, earner):
    return db.scalar(select(IncomeSource).where(IncomeSource.earner == earner))


def test_pay_dates(db):
    alex, sam = _source(db, "Alex"), _source(db, "Sam")
    assert income.pay_dates(alex, date(2026, 2, 1), date(2026, 3, 1)) == [date(2026, 2, 15), date(2026, 2, 28)]
    assert income.pay_dates(sam, date(2026, 11, 1), date(2026, 12, 1)) == [date(2026, 11, 12), date(2026, 11, 26)]


def test_expected_income_counts_paycheques_not_seen_yet(db):
    # Alex's mid-November pay arrived a day early.
    db.add(Transaction(source="test", timestamp=datetime(2026, 11, 14, 17, tzinfo=timezone.utc), amount=Decimal("3250.40"),
                       merchant_raw="NORTHWIND", txn_type="INCOME", excluded=True, category="Salary", purpose="Alex",
                       account_id="ws_cash", income_source_id=_source(db, "Alex").id))
    db.commit()
    start, end = date(2026, 11, 1), date(2026, 12, 1)
    expected = income.expected_income(db, start, end, today=date(2026, 11, 14))
    assert expected == {"Alex": Decimal("3250.40"), "Sam": Decimal("5960.40")}
    assert income.expected_income(db, start, end, today=date(2026, 11, 14), earner="Alex") == {"Alex": Decimal("3250.40")}
    assert income.expected_income(db, start, end, today=date(2026, 12, 2)) == {}


def test_new_source_claims_recent_deposits(client, db):
    when = datetime.now(TZ) - timedelta(days=5)
    resp = client.post("/api/v1/income", json={"amount": "900.00", "received_on": when.date().isoformat(),
                                               "earner": "Sam", "payer": "ACME TUTORING", "category": "Other income"})
    txn_id = resp.json()["id"]
    source = client.post("/api/v1/income/sources", json={
        "name": "Tutoring", "earner": "Sam", "payer_pattern": "ACME", "amount": "900.00",
        "cadence": "monthly", "anchor_date": when.date().isoformat()}).json()
    txn = db.get(Transaction, txn_id)
    assert str(txn.income_source_id) == source["id"]
    # Chosen by hand when it was entered, so it stays Other income.
    assert txn.category == "Other income"

    assert client.patch(f"/api/v1/income/sources/{source['id']}", json={"amount": "950.00"}).json()["amount"] == 950.0
    assert len(client.get("/api/v1/income/sources").json()) == 3
    assert client.delete(f"/api/v1/income/sources/{source['id']}").status_code == 204
    assert db.get(Transaction, txn_id) is not None


# --- screenshots ----------------------------------------------------------------------------------

def test_screenshot_pay_is_added_as_income(client, db, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", screens.RBC_SAM_CHEQUING).json()
    actions = {r["merchant"]: r["action"] for r in preview["rows"]}
    assert actions["Payroll Deposit PAIE/PAYROLL"] == "income"
    # The transfer the app can't place has to be decided first (tests/test_savings.py).
    undecided = {str(r["index"]): "skip" for r in preview["rows"] if r["action"] == "transfer" and not r["suggested"]}
    assert client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": undecided}).status_code == 202
    [pay] = incomes(db)
    assert (pay.amount, pay.purpose, pay.account_id, pay.source) == (Decimal("2980.20"), "Sam", "rbc_sam_cheq",
                                                                    "screenshot")


def test_screenshot_pay_already_known_from_email_is_matched(client, db, screenshot):  # noqa: F811
    process_message(db, FakeGmail([rbc_deposit("r1", alerts.RBC_DEPOSIT_BODY)]), "r1")
    preview = screenshot(client, "rbc_sam_cheq", screens.RBC_SAM_CHEQUING[:1]).json()
    assert preview["rows"][0]["action"] == "match"
    client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {}})
    assert len(incomes(db)) == 1


def test_other_deposits_wait_for_a_decision(client, db, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", [screens.E_TRANSFER_RECEIVED]).json()
    assert preview["rows"][0]["action"] == "deposit"
    commit = f"/api/v1/statements/{preview['id']}/commit"
    assert client.post(commit, json={"decisions": {}}).status_code == 422
    assert client.post(commit, json={"decisions": {"0": "transfer"}}).status_code == 202
    # Own money from an account the app can't tell: a transfer into this one, counted nowhere.
    [row] = db.scalars(select(Transaction))
    assert (row.txn_type, row.amount, row.excluded) == ("TRANSFER", Decimal("60.00"), True)
    assert (row.account_id, row.counterpart_account_id) == (None, "rbc_sam_cheq")


def test_a_deposit_can_be_called_income(client, db, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", [screens.E_TRANSFER_RECEIVED]).json()
    client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {"0": "income"}})
    [row] = incomes(db)
    assert (row.amount, row.category, row.purpose) == (Decimal("60.00"), "Other income", "Sam")


def test_income_decision_is_only_for_deposits(client, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", screens.RBC_SAM_CHEQUING[1:2]).json()
    resp = client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {"0": "income"}})
    assert resp.status_code == 422
