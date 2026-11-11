from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import RawIngestLog, Transaction
from app.daemons.parsers import wealthsimple

NOW = datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc).isoformat()


def send(client, title, text, key="0|com.wealthsimple.trade|1|null|10514", package="com.wealthsimple.trade", at=NOW):
    resp = client.post("/api/v1/notifications", json={"package": package, "title": title, "text": text,
                                                      "posted_at": at, "key": key})
    return resp


@pytest.mark.parametrize(
    ("title", "text", "amount", "merchant", "account"),
    [
        ("Wealthsimple", "You spent $12.34 at Tim Hortons", "12.34", "Tim Hortons", "ws_credit"),
        ("Credit card", "Purchase of $1,204.50 at BEST BUY #123 with your card ending in 4321.", "1204.50", "BEST BUY #123", "ws_credit"),
        ("Card purchase", "$9.29 at Corner Creamery Toronto", "9.29", "Corner Creamery Toronto", "ws_credit"),
        ("Cash card", "You spent $5.00 at Second Cup using your Cash card", "5.00", "Second Cup", "ws_cash"),
        ("Refund", "You were refunded $423.73 from Sports Outlet Midtown", "-423.73", "Sports Outlet Midtown", "ws_credit"),
        # Real sample: the merchant is the title.
        ("NORTHSIDE BOXING ", "You made a recurring payment for $75.40 with your credit card.", "75.40",
         "NORTHSIDE BOXING", "ws_credit"),
        # Insurance claims paid into the Cash account offset the full-price charge.
        ("Deposit received", "You received $190.00 from MANULIFE in your Cash account", "-190.00", "Manulife", "ws_cash"),
        ("Manulife", "$185.50 was deposited into your Cash account", "-185.50", "Manulife", "ws_cash"),
        ("Direct deposit", "A direct deposit of $45.00 from Sun Life Financial has arrived", "-45.00", "Sun Life", "ws_cash"),
    ],
)
def test_purchase_phrasings(title, text, amount, merchant, account):
    alert = wealthsimple.parse(title, text)
    assert (str(alert.amount), alert.merchant, alert.account_id) == (amount, merchant, account)


@pytest.mark.parametrize(
    "text",
    [
        "Your payment of $374.88 was received",
        "You received $500.00 by e-Transfer from Alex",
        "Your order to buy 3 shares of VFV was filled",
        "A purchase of $20.00 at METRO was declined",
        "You earned $2.40 in cash back",
        "You made a recurring payment for $75.40 with your credit card.",  # no merchant in a generic title
        "A deposit of $190.00 from Manulife failed",
    ],
)
def test_non_spending_is_ignored(text):
    assert wealthsimple.parse("Wealthsimple", text) is None


def test_notification_becomes_a_transaction_once(client, db):
    first = send(client, "Wealthsimple", "You spent $12.34 at Tim Hortons").json()
    assert first["status"] == "created"
    txn = first["transaction"]
    assert (txn["account_id"], txn["amount"], txn["category"], txn["source"]) == ("ws_credit", 12.34, "Eating Out", "android_push")
    assert send(client, "Wealthsimple", "You spent $12.34 at Tim Hortons").json()["status"] == "duplicate"
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_unreadable_money_text_is_kept_and_promos_are_dropped(client, db):
    assert send(client, "Wealthsimple", "Card activity: 12.34 CAD — Tims").json()["status"] == "ignored"
    assert send(client, "Wealthsimple", "New charge $12.34 posted").json()["status"] == "unparsed"
    assert send(client, "Get 1% cash back", "Upgrade to Premium today").json()["status"] == "ignored"
    logs = list(db.scalars(select(RawIngestLog).where(RawIngestLog.source == "android_notification")))
    assert [l.payload["text"] for l in logs] == ["New charge $12.34 posted"]


def test_other_apps_are_rejected(client):
    assert send(client, "Hi", "You spent $1.00 at X", package="com.whatsapp").status_code == 422


def test_statement_import_later_confirms_instead_of_duplicating(client):
    send(client, "Wealthsimple", "You spent $9.29 at Corner Creamery Toronto", at="2026-10-27T20:00:00+00:00")
    from tests.fixtures import statements as csvs
    body = client.post("/api/v1/statements/preview",
                       files={"file": ("ws.csv", csvs.WEALTHSIMPLE.encode(), "text/csv")}).json()
    creamery = next(r for r in body["rows"] if r["merchant"] == "Corner Creamery Toronto")
    assert creamery["action"] == "match"


# --- Google Wallet --------------------------------------------------------------------------------------

from app.daemons.parsers import google_wallet  # noqa: E402
from app.db.models import Account  # noqa: E402
from app.services.ingest import NewTransaction, ingest_transaction  # noqa: E402

GPAY = "com.google.android.apps.walletnfcrel"


@pytest.mark.parametrize(
    ("title", "text", "amount", "merchant", "last4"),
    [
        ("Tim Hortons", "$12.34 with Mastercard ••4455", "12.34", "Tim Hortons", "4455"),
        ("Google Wallet", "You paid $45.00 at LCBO using Visa •••• 7788", "45.00", "LCBO", "7788"),
        ("Shoppers Drug Mart", "Refunded $13.87 to Mastercard ending in 4455", "-13.87", "Shoppers Drug Mart", "4455"),
    ],
)
def test_wallet_phrasings(title, text, amount, merchant, last4):
    alert = google_wallet.parse(title, text)
    assert (str(alert.amount), alert.merchant, alert.card_suffix) == (amount, merchant, last4)


@pytest.mark.parametrize("text", ["Card added to Google Wallet", "Payment declined: $5.00 at Metro", "Your card is ready to use"])
def test_wallet_non_spending(text):
    assert google_wallet.parse("Google Wallet", text) is None


def test_wallet_card_picks_the_account(client, db):
    db.get(Account, "pc_mastercard").card_last4 = "4455"
    db.commit()
    body = send(client, "Tim Hortons", "$12.34 with Mastercard ••4455", package=GPAY, key="g1").json()
    assert (body["status"], body["transaction"]["account_id"]) == ("created", "pc_mastercard")
    unknown = send(client, "Metro", "$8.00 with Visa ••9999", package=GPAY, key="g2").json()
    assert unknown["transaction"]["account_id"] is None


def test_bank_email_and_wallet_notification_are_one_purchase(client, db):
    db.get(Account, "pc_mastercard").card_last4 = "4455"
    db.commit()
    first = send(client, "Maple Dental", "$32.70 with Mastercard ••4455", package=GPAY, key="g3",
                 at="2026-11-02T20:43:00+00:00").json()
    email, created = ingest_transaction(db, NewTransaction(
        source="gmail_pubsub", source_ref="msg-1", account_id="pc_mastercard",
        timestamp=datetime(2026, 11, 2, 20, 45, tzinfo=timezone.utc), amount=Decimal("32.70"),
        merchant="MAPLE DENTAL CENTRE"), raw_payload={})
    assert (created, str(email.id)) == (False, first["transaction"]["id"])
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_two_real_purchases_from_one_feed_stay_two(client):
    a = send(client, "Wealthsimple", "You spent $4.50 at Second Cup", key="k1", at="2026-11-04T13:00:00+00:00").json()
    b = send(client, "Wealthsimple", "You spent $4.50 at Second Cup", key="k2", at="2026-11-04T16:00:00+00:00").json()
    assert (a["status"], b["status"]) == ("created", "created")


def test_insurance_claim_offsets_the_full_charge(client, db):
    from app.db.models import CategorizationRule
    db.add(CategorizationRule(merchant_pattern="Manulife", target_category="Health & Fitness", target_purpose="Alex",
                              priority=5, origin="history"))
    db.commit()
    body = send(client, "Deposit received", "You received $190.00 from MANULIFE in your Cash account").json()
    assert body["status"] == "created"
    txn = body["transaction"]
    assert (txn["amount"], txn["merchant_raw"], txn["account_id"], txn["category"], txn["purpose"]) == (
        -190.0, "Manulife", "ws_cash", "Health & Fitness", "Alex")


# Google Pay showed a Wealthsimple card with digits that are another bank's card's last four.
WS_VIA_WALLET = ("LYFT   *TEMP AUTH HOLD", "$19.97 with Wealthsimple VIP ••9149")
WS_OWN = ("LYFT   *TEMP AUTH HOLD 🚕", "You spent $19.97 with your credit card.")


def test_wallet_reads_the_card_name():
    alert = google_wallet.parse(*WS_VIA_WALLET)
    assert (alert.card_name, alert.card_suffix, alert.account_id) == ("Wealthsimple VIP", "9149", "ws_credit")
    assert google_wallet.parse("Corner Cafe", "$12.34 with Mastercard ••9775").card_name == "Mastercard"


def test_wallet_card_name_beats_colliding_digits(client, db):
    db.get(Account, "ctfs_triangle").card_last4 = "9149"
    db.get(Account, "ws_credit").card_last4 = "9527"
    db.commit()
    body = send(client, *WS_VIA_WALLET, package=GPAY, key="g9", at="2026-11-07T01:06:07+00:00").json()
    assert body["transaction"]["account_id"] == "ws_credit"


def test_wealthsimple_card_in_wallet_is_one_purchase(client, db):
    db.get(Account, "ctfs_triangle").card_last4 = "9149"
    db.commit()
    wallet = send(client, *WS_VIA_WALLET, package=GPAY, key="g10", at="2026-11-07T01:06:07+00:00").json()
    own = send(client, *WS_OWN, key="w10", at="2026-11-07T01:06:48+00:00").json()
    assert (wallet["status"], own["status"]) == ("created", "duplicate")
    assert own["transaction"]["id"] == wallet["transaction"]["id"]
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_wallet_on_the_wrong_account_is_still_one_purchase_and_takes_the_banks_account(client, db):
    # Wallet names no bank, so its digits pick Triangle; Wealthsimple's own notice a minute later is the same charge.
    db.get(Account, "ctfs_triangle").card_last4 = "9149"
    db.commit()
    wallet = send(client, "LYFT   *TEMP AUTH HOLD", "$18.94 with Visa ••9149", package=GPAY, key="g11",
                  at="2026-11-07T04:45:47+00:00").json()
    assert wallet["transaction"]["account_id"] == "ctfs_triangle"
    own = send(client, "LYFT   *TEMP AUTH HOLD 🚕", "You spent $18.94 with your credit card.", key="w11",
               at="2026-11-07T04:46:24+00:00").json()
    assert own["status"] == "duplicate"
    txn = db.get(Transaction, wallet["transaction"]["id"])
    db.refresh(txn)
    assert txn.account_id == "ws_credit"
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_same_charge_on_two_cards_hours_apart_stays_two(client, db):
    db.get(Account, "pc_mastercard").card_last4 = "9775"
    db.commit()
    a = send(client, "Corner Cafe", "$4.50 with Mastercard ••9775", package=GPAY, key="g12", at="2026-11-04T13:00:00+00:00").json()
    b = send(client, "Wealthsimple", "You spent $4.50 at Corner Cafe", key="w12", at="2026-11-04T16:00:00+00:00").json()
    assert (a["status"], b["status"]) == ("created", "created")
