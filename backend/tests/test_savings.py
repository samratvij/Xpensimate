from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.daemons.gmail_sync import process_message
from app.daemons.parsers import gmail_query, wealthsimple_email
from app.db.models import RawIngestLog, Transaction
from app.services import savings
from tests.fixtures import alerts, screens
from tests.gmail_fakes import FakeGmail, gmail_message
from tests.test_statements import screenshot  # noqa: F401 - fixture

WS = "Wealthsimple <notifications@o.wealthsimple.com>"


def _ms(when: str) -> int:
    return int(datetime.fromisoformat(when).timestamp() * 1000)


def ws_mail(message_id, subject, body, when="2026-10-15T13:00:00+00:00"):
    return gmail_message(message_id, WS, subject, body, internal_ms=_ms(when))


def transfers(db):
    return list(db.scalars(select(Transaction).where(Transaction.txn_type == "TRANSFER").order_by(Transaction.timestamp)))


def add_money(db, amount, kind, when="2026-10-20T12:00:00+00:00", purpose="Shared"):
    is_income = kind == "INCOME"
    db.add(Transaction(source="test", timestamp=datetime.fromisoformat(when), amount=Decimal(amount),
                       merchant_raw="x", txn_type=kind, excluded=is_income,
                       category="Salary" if is_income else "Grocery", purpose=purpose))
    db.commit()


def summary(client, month="2026-10", **params):
    return client.get("/api/v1/summary", params={"month": month, **params}).json()


# --- where a move goes ----------------------------------------------------------------------------

@pytest.mark.parametrize(("src", "dst", "expected"), [
    ("chequing", "savings", "to_savings"),
    ("chequing", "investment", "to_investments"),
    ("savings", "investment", "to_investments"),
    ("savings", "chequing", "from_savings"),
    ("retirement", "chequing", "from_investments"),
    ("chequing", "chequing", None),
    ("chequing", "credit_card", None),
    ("investment", "retirement", None),
    ("chequing", None, None),
])
def test_flow_follows_the_accounts_kinds(src, dst, expected):
    assert savings.flow(src, dst) == expected


# --- Wealthsimple transfer emails -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def alexs_mailbox(monkeypatch):
    """The Gmail account is Alex's: "You made a transfer" means Alex."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "gmail_owner", "Alex")


def test_wealthsimple_transfer_emails_name_both_accounts():
    alex = wealthsimple_email.parse(alerts.WS_TRANSFER_SUBJECT, alerts.WS_TRANSFER_BODY)
    assert (alex.amount, alex.from_name, alex.to_name, alex.person, alex.transfer) == (
        Decimal("450.00"), "Checking (Chequing)", "Family (Joint Chequing)", None, True)
    assert alex.merchant == "Wealthsimple transfer: Checking (Chequing) → Family (Joint Chequing)"
    sam = wealthsimple_email.parse(alerts.WS_SAM_TRANSFER_SUBJECT, alerts.WS_SAM_TRANSFER_BODY)
    assert sam.person == "Sam"


@pytest.mark.parametrize(("shown", "person", "expected"), [
    ("Checking (Chequing)", "Alex", "ws_cash"),
    ("Chequing", "Sam", "ws_sam_cheq"),
    ("Family (Joint Chequing)", "Sam", "ws_joint"),
    ("TFSA", "Alex", "ws_tfsa"),
    ("FHSA", "Sam", "ws_sam_fhsa"),
    ("Savings", "Alex", "ws_savings"),
    ("Crypto (Crypto)", "Alex", None),
])
def test_the_households_accounts_are_found_by_name_kind_and_owner(db, shown, person, expected):
    from app.services.accounts import account_named
    assert account_named(db, "Wealthsimple", shown, person) == expected


def test_unknown_wealthsimple_account_name_is_kept_unparsed(db):
    body = alerts.WS_TRANSFER_BODY.replace("Family (Joint Chequing)", "Crypto (Crypto)")
    assert process_message(db, FakeGmail([ws_mail("t9", alerts.WS_TRANSFER_SUBJECT, body)]), "t9") == "unparsed"
    assert db.scalar(select(RawIngestLog)).payload["parse_status"] == "unparsed"


def test_search_asks_for_wealthsimple_transfers_too():
    assert '(from:(wealthsimple.com) (subject:"Direct deposit received" OR subject:"made a transfer"))' in gmail_query("1d")


def test_transfers_are_flows_not_spending(client, db):
    mails = [
        ws_mail("t1", alerts.WS_TRANSFER_SUBJECT, alerts.WS_TRANSFER_BODY, "2026-10-15T13:01:30+00:00"),
        ws_mail("t2", alerts.WS_SAM_TRANSFER_SUBJECT, alerts.WS_SAM_TRANSFER_BODY, "2026-10-15T13:09:00+00:00"),
        ws_mail("t3", alerts.WS_TRANSFER_SUBJECT, alerts.WS_FROM_JOINT_BODY, "2026-10-16T12:37:35+00:00"),
        ws_mail("t4", alerts.WS_TRANSFER_SUBJECT, alerts.WS_TFSA_TRANSFER_BODY, "2026-10-20T12:00:00+00:00"),
    ]
    gmail = FakeGmail(mails)
    assert [process_message(db, gmail, m["id"]) for m in mails] == ["created"] * 4
    assert process_message(db, gmail, "t1") == "duplicate"
    rows = transfers(db)
    assert [(t.account_id, t.counterpart_account_id, t.amount, t.purpose, t.excluded) for t in rows] == [
        ("ws_cash", "ws_joint", Decimal("450.00"), "Alex", True),
        ("ws_sam_cheq", "ws_joint", Decimal("200.00"), "Sam", True),
        ("ws_joint", "ws_cash", Decimal("650.00"), "Shared", True),
        ("ws_cash", "ws_tfsa", Decimal("500.00"), "Alex", True),
    ]
    add_money(db, "7000.00", "INCOME", purpose="Alex")
    add_money(db, "2000.00", "PURCHASE")

    body = summary(client)
    assert body["total_spent"] == 2000.0
    assert body["savings"] == {
        "kept": 5000.0, "rate": 71.4,
        "to_savings": 0.0, "from_savings": 0.0, "to_investments": 500.0, "from_investments": 0.0,
        "net_to_savings": 0.0, "net_to_investments": 500.0, "moved": 500.0,
        # Against the $5,000 monthly target (tests/test_goals.py); October is over, so short of it is behind.
        "target": 5000.0, "target_percent": 10.0, "target_status": "behind",
    }
    # Savings are the household's: not shown for one person.
    assert summary(client, purpose="Alex")["savings"] is None


def test_no_income_means_no_rate(client, db):
    add_money(db, "50.00", "PURCHASE")
    assert summary(client)["savings"]["rate"] is None


# --- statement and screenshot imports -------------------------------------------------------------

def test_transfers_on_a_bank_screenshot_ask_where_the_money_went(client, db, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", screens.RBC_SAM_CHEQUING).json()
    rows = {r["merchant"]: r for r in preview["rows"]}
    assert rows["Payroll Deposit PAIE/PAYROLL"]["action"] == "income"
    assert rows["ATM withdrawal - ATM00421"]["action"] == "add"
    # Sent to Alex: money between the two of you, unless you say otherwise.
    to_alex = rows["e-Transfer sent alex lee WB7TQP"]
    assert (to_alex["action"], to_alex["suggested"]) == ("transfer", "transfer")
    online = rows["Online Banking transfer - 2046"]
    assert (online["action"], online["suggested"]) == ("transfer", None)

    commit = f"/api/v1/statements/{preview['id']}/commit"
    assert client.post(commit, json={"decisions": {}}).status_code == 422
    decisions = {str(online["index"]): "move:rbc_sam_savings", str(rows["e-Transfer sent jordan wu KQ3MZ1"]["index"]): "add"}
    assert client.post(commit, json={"decisions": decisions}).status_code == 202

    moved = {t.merchant_raw: t for t in transfers(db)}
    saved = moved["Online Banking transfer - 2046"]
    assert (saved.account_id, saved.counterpart_account_id, saved.amount, saved.purpose) == (
        "rbc_sam_cheq", "rbc_sam_savings", Decimal("1650.40"), "Sam")
    between = moved["e-Transfer sent alex lee WB7TQP"]
    assert (between.account_id, between.counterpart_account_id) == ("rbc_sam_cheq", None)
    # "add": paid to someone for something, so it is spending like any purchase.
    spent = db.scalar(select(Transaction).where(Transaction.merchant_raw == "e-Transfer sent jordan wu KQ3MZ1"))
    assert (spent.txn_type, spent.excluded) == ("PURCHASE", False)

    october = summary(client)["savings"]
    assert (october["to_savings"], october["net_to_savings"]) == (1650.40, 1650.40)


def test_the_same_description_is_suggested_next_time(client, db, screenshot):  # noqa: F811
    row = screens.RBC_SAM_CHEQUING[3]
    first = screenshot(client, "rbc_sam_cheq", [row]).json()
    client.post(f"/api/v1/statements/{first['id']}/commit", json={"decisions": {"0": "move:rbc_sam_savings"}})
    later = {**row, "date": "2026-11-09", "merchant": "Online Banking transfer - 2047"}
    second = screenshot(client, "rbc_sam_cheq", [later]).json()
    assert second["rows"][0]["suggested"] == "move:rbc_sam_savings"
    # A suggestion is what happens unless the user says otherwise.
    client.post(f"/api/v1/statements/{second['id']}/commit", json={"decisions": {}})
    assert [t.counterpart_account_id for t in transfers(db)] == ["rbc_sam_savings"] * 2


def test_account_digits_in_the_description_suggest_the_account(client, screenshot):  # noqa: F811
    row = {"date": "2026-10-30", "merchant": "TFR-TO 4143", "amount": 300.0, "kind": "transfer", "pending": False}
    preview = screenshot(client, "rbc_sam_cheq", [row]).json()
    assert preview["rows"][0]["suggested"] == "move:rbc_sam_savings"


def test_money_into_savings_from_chequing(client, db, screenshot):  # noqa: F811
    row = {"date": "2026-10-30", "merchant": "Transfer from chequing", "amount": 300.0, "kind": "deposit", "pending": False}
    preview = screenshot(client, "pc_savings", [row]).json()
    assert preview["rows"][0]["action"] == "deposit"
    client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {"0": "move:pc_money"}})
    [t] = transfers(db)
    assert (t.account_id, t.counterpart_account_id, t.purpose) == ("pc_money", "pc_savings", "Alex")
    assert summary(client)["savings"]["to_savings"] == 300.0


def test_a_move_seen_from_the_other_account_is_matched(client, db, screenshot):  # noqa: F811
    process_message(db, FakeGmail([ws_mail("t1", alerts.WS_TRANSFER_SUBJECT, alerts.WS_TRANSFER_BODY)]), "t1")
    row = {"date": "2026-10-15", "merchant": "Transfer to Family", "amount": 450.0, "kind": "deposit", "pending": False}
    preview = screenshot(client, "ws_joint", [row]).json()
    assert preview["rows"][0]["action"] == "match"
    client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {}})
    assert len(transfers(db)) == 1


def test_move_decisions_are_checked(client, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", [screens.RBC_SAM_CHEQUING[3]]).json()
    commit = f"/api/v1/statements/{preview['id']}/commit"
    assert client.post(commit, json={"decisions": {"0": "move:nope"}}).status_code == 422
    assert client.post(commit, json={"decisions": {"0": "move:rbc_sam_cheq"}}).status_code == 422  # itself
    assert client.post(commit, json={"decisions": {"0": "income"}}).status_code == 422  # not a deposit


def test_a_transfer_can_be_counted_as_spending_after_all(client, db, screenshot):  # noqa: F811
    preview = screenshot(client, "rbc_sam_cheq", [screens.RBC_SAM_CHEQUING[2]]).json()
    client.post(f"/api/v1/statements/{preview['id']}/commit", json={"decisions": {}})
    [t] = transfers(db)
    resp = client.patch(f"/api/v1/transactions/{t.id}", json={"excluded": False})
    assert resp.status_code == 200
    assert (resp.json()["txn_type"], resp.json()["excluded"], resp.json()["counterpart_account_id"]) == ("PURCHASE", False, None)
    assert summary(client)["total_spent"] == 1000.0
