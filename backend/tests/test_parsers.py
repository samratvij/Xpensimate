from decimal import Decimal

import pytest

from app.daemons.gmail_client import message_text
from app.daemons.parsers import ctfs, gmail_query, issuer_for_sender, pc_financial, rbc, tangerine
from tests.fixtures import alerts
from tests.gmail_fakes import gmail_message


def test_tangerine_credit_card_alert():
    assert tangerine.is_transaction(alerts.TANGERINE_CC_SUBJECT)
    alert = tangerine.parse(alerts.TANGERINE_CC_SUBJECT, alerts.TANGERINE_CC_BODY)
    assert (alert.amount, alert.merchant, alert.account_id, alert.card_suffix) == (
        Decimal("11.29"), "DOORDASHDASHPASS", "tangerine_mastercard", "1234",
    )


def test_pc_mastercard_alert():
    assert pc_financial.is_transaction(alerts.PC_MASTERCARD_SUBJECT)
    alert = pc_financial.parse(alerts.PC_MASTERCARD_SUBJECT, alerts.PC_MASTERCARD_BODY)
    assert (alert.amount, alert.merchant, alert.account_id, alert.card_suffix) == (
        Decimal("32.70"), "MAPLE DENTAL CENTRE", "pc_mastercard", "5678",
    )


def test_pc_money_alert_with_blank_merchant():
    alert = pc_financial.parse(alerts.PC_MONEY_SUBJECT, alerts.PC_MONEY_BODY)
    assert (alert.amount, alert.merchant, alert.account_id) == (Decimal("420.00"), "Unknown merchant", "pc_money")


def test_ctfs_labelled_alert():
    alert = ctfs.parse(alerts.CTFS_PURCHASE_SUBJECT, alerts.CTFS_PURCHASE_BODY)
    assert (alert.amount, alert.merchant, alert.account_id, alert.card_suffix) == (
        Decimal("1234.56"), "CANADIAN TIRE #123", "ctfs_triangle", "3456",
    )


def test_refund_is_negative():
    body = alerts.TANGERINE_CC_BODY.replace("a transaction of", "a refund transaction of")
    assert tangerine.parse(alerts.TANGERINE_CC_SUBJECT, body).amount == Decimal("-11.29")


@pytest.mark.parametrize(
    ("issuer", "subject"),
    [
        (pc_financial, alerts.PC_LOW_BALANCE_SUBJECT),
        (pc_financial, "The interest rate is increasing on your PC Money™ Account savings balance"),
        (tangerine, "Your Money-Back World Mastercard® Statement is ready"),
        (tangerine, "0% interest for 12 months*. Transfer your balance and save on interest."),
        (tangerine, "A message about inactivity in your Chequing Account"),
        (ctfs, alerts.CTFS_OTP_SUBJECT),
        (ctfs, "30x Total CT Money at Canadian Tire. October 30."),
    ],
)
def test_non_transaction_subjects_are_not_alerts(issuer, subject):
    assert not issuer.is_transaction(subject)


def test_unexpected_wording_does_not_parse():
    assert tangerine.parse(alerts.TANGERINE_CC_SUBJECT, "Your card was used. Log in for details.") is None
    assert pc_financial.parse(alerts.PC_MONEY_SUBJECT, "Something changed on your account.") is None


@pytest.mark.parametrize(
    ("sender", "issuer"),
    [
        ("Tangerine <donotreply@tangerine.ca>", tangerine),
        ("PC Financial <info@account.pcfinancial.ca>", pc_financial),
        ("Canadian Tire Bank <donotreply@ctfs.com>", ctfs),
        ("RBC Royal Bank Alerts <rbcroyalbankalerts@alerts.rbc.com>", rbc),
        ("SAM GUPTA <notify@payments.interac.ca>", None),
        ("Phish <alerts@rbc.com.example.net>", None),
        ("Phish <alerts@tangerine.ca.example.com>", None),
        ("someone@nottangerine.ca", None),
        ("friend@gmail.com", None),
    ],
)
def test_issuer_for_sender(sender, issuer):
    assert issuer_for_sender(sender) is issuer


def test_gmail_query_covers_all_issuers():
    query = gmail_query("1d")
    assert all(d in query for d in ("tangerine.ca", "pcfinancial.ca", "ctfs.com", "rbc.com"))
    assert query.endswith("newer_than:1d")


def test_message_text_falls_back_to_html():
    message = gmail_message(
        "m1", "a@tangerine.ca", "s",
        "<html><style>p{}</style><p>A purchase of&nbsp;<b>$5.00</b></p><p>at TIM HORTONS on your card</p></html>",
        html=True,
    )
    assert message_text(message["payload"]) == "A purchase of $5.00\nat TIM HORTONS on your card"


def test_message_text_strips_preview_padding():
    message = gmail_message("m1", "a@pcfinancial.ca", "s", "͏‌ ͏‌\nMerchant: FOO\n")
    assert message_text(message["payload"]) == "Merchant: FOO"


def test_rbc_real_alert():
    assert rbc.is_transaction(alerts.RBC_PURCHASE_SUBJECT)
    alert = rbc.parse(alerts.RBC_PURCHASE_SUBJECT, alerts.RBC_PURCHASE_BODY)
    # Account comes from the card suffix (several RBC cards in the household).
    assert (alert.amount, alert.merchant, alert.account_id, alert.card_suffix) == (
        Decimal("22.59"), "CANADIAN TIRE STORE #2", "", "4321",
    )


def test_rbc_sentence_without_labels():
    alert = rbc.parse(alerts.RBC_PURCHASE_SUBJECT, alerts.RBC_SENTENCE_ONLY_BODY)
    assert (alert.amount, alert.merchant, alert.card_suffix) == (Decimal("1076.40"), "FARM BOY #12", "4321")


def test_rbc_refund_is_negative():
    assert rbc.is_transaction(alerts.RBC_REFUND_SUBJECT)
    alert = rbc.parse(alerts.RBC_REFUND_SUBJECT, alerts.RBC_REFUND_BODY)
    assert (alert.amount, alert.merchant) == (Decimal("-19.99"), "UNIQLO CANADA")


def test_rbc_debit_card_alert_is_not_counted():
    assert rbc.parse(alerts.RBC_DEBIT_SUBJECT, alerts.RBC_DEBIT_BODY) is None


@pytest.mark.parametrize("subject", [
    "Your RBC credit card statement is ready",
    "Payment received on your RBC Visa",
    "Interac e-Transfer: you've received $50.00",
    "Your RBC sign-in code",
    "Earn more Avion rewards",
])
def test_rbc_non_purchase_mail_is_ignored(subject):
    assert not rbc.is_transaction(subject)
