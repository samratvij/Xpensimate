from datetime import datetime, timezone

from sqlalchemy import func, select

from app.daemons.gmail_sync import backfill, process_history, process_message, start_watch
from app.db.models import GmailSyncState, RawIngestLog, Transaction
from tests.fixtures import alerts
from tests.gmail_fakes import FakeGmail, gmail_message, history_page, not_found

EMAIL = "me@example.com"
TANGERINE = gmail_message(
    "t1", "Tangerine <donotreply@tangerine.ca>", alerts.TANGERINE_CC_SUBJECT, alerts.TANGERINE_CC_BODY
)
PC = gmail_message("p1", "PC Financial <info@account.pcfinancial.ca>", alerts.PC_MASTERCARD_SUBJECT,
                   alerts.PC_MASTERCARD_BODY)
NEWSLETTER = gmail_message("n1", "deals@shop.example", "Sale!", "Save $20.00 at our store today.")
OTP = gmail_message("o1", "donotreply@ctfs.com", alerts.CTFS_OTP_SUBJECT, alerts.CTFS_OTP_BODY)
GARBLED = gmail_message("g1", "donotreply@tangerine.ca", alerts.TANGERINE_CC_SUBJECT, "Log in to see the details.")


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_bank_alert_becomes_pending_transaction(db):
    assert process_message(db, FakeGmail([TANGERINE]), "t1") == "created"
    txn = db.scalar(select(Transaction))
    assert (txn.source, txn.source_ref, txn.account_id) == ("gmail_pubsub", "t1", "tangerine_mastercard")
    assert txn.reconciliation_status == "PENDING_MATCH"
    assert txn.timestamp == datetime(2026, 11, 3, 15, 30, tzinfo=timezone.utc)
    assert (str(txn.amount), txn.merchant_raw, txn.category) == ("11.29", "DOORDASHDASHPASS", "Uncategorized")
    log = db.scalar(select(RawIngestLog))
    assert log.payload["parsed"]["card_suffix"] == "1234"


def test_already_ingested_message_is_not_refetched(db):
    gmail = FakeGmail([TANGERINE])
    process_message(db, gmail, "t1")
    assert process_message(db, gmail, "t1") == "duplicate"
    assert gmail.fetched == ["t1"]


def test_non_bank_mail_is_ignored_without_storing_it(db):
    assert process_message(db, FakeGmail([NEWSLETTER]), "n1") == "ignored"
    assert count(db, Transaction) == 0
    assert count(db, RawIngestLog) == 0


def test_bank_non_transaction_mail_is_ignored_without_storing_it(db):
    assert process_message(db, FakeGmail([OTP]), "o1") == "ignored"
    assert count(db, RawIngestLog) == 0


def test_sent_copy_is_ignored(db):
    sent = {**TANGERINE, "labelIds": ["SENT"]}
    assert process_message(db, FakeGmail([sent]), "t1") == "ignored"


def test_unparsed_alert_is_kept_for_later(db):
    assert process_message(db, FakeGmail([GARBLED]), "g1") == "unparsed"
    assert count(db, Transaction) == 0
    log = db.scalar(select(RawIngestLog))
    assert log.payload["parse_status"] == "unparsed"
    assert log.payload["body"] == "Log in to see the details."


def test_start_watch_initialises_then_only_renews(db):
    gmail = FakeGmail()
    assert start_watch(db, gmail, EMAIL, "projects/p/topics/t") is True
    db.get(GmailSyncState, EMAIL).history_id = 250
    db.commit()
    assert start_watch(db, gmail, EMAIL, "projects/p/topics/t") is False
    state = db.get(GmailSyncState, EMAIL)
    assert state.history_id == 250
    assert state.watch_expires_at is not None
    assert gmail.watch_calls[0] == {
        "topicName": "projects/p/topics/t", "labelIds": ["INBOX"], "labelFilterBehavior": "include",
    }


def test_process_history_pages_and_advances(db):
    db.add(GmailSyncState(email=EMAIL, history_id=100))
    db.commit()
    gmail = FakeGmail(
        [TANGERINE, PC, NEWSLETTER],
        history_pages=[history_page("t1", "n1", history_id=120, next_page=1), history_page("p1", "t1", history_id=130)],
    )
    assert process_history(db, gmail, EMAIL) == {"created": 2, "ignored": 1}
    assert gmail.fetched == ["t1", "n1", "p1"]
    assert db.get(GmailSyncState, EMAIL).history_id == 130
    assert count(db, Transaction) == 2


def test_expired_history_falls_back_to_search(db):
    db.add(GmailSyncState(email=EMAIL, history_id=5))
    db.commit()
    gmail = FakeGmail([TANGERINE, PC], history_error=not_found(), profile_history_id=900)
    assert process_history(db, gmail, EMAIL) == {"created": 2}
    assert db.get(GmailSyncState, EMAIL).history_id == 900


def test_backfill_is_idempotent(db):
    gmail = FakeGmail([TANGERINE, NEWSLETTER])
    assert backfill(db, gmail) == {"created": 1, "ignored": 1}
    assert backfill(db, gmail) == {"duplicate": 1, "ignored": 1}
    assert count(db, Transaction) == 1


def test_message_deleted_before_fetch_is_skipped(db):
    db.add(GmailSyncState(email=EMAIL, history_id=100))
    db.commit()
    gmail = FakeGmail([TANGERINE], history_pages=[history_page("deleted-1", "t1", history_id=140)])
    assert process_history(db, gmail, EMAIL) == {"gone": 1, "created": 1}
    assert db.get(GmailSyncState, EMAIL).history_id == 140


RBC = gmail_message("r1", "RBC Royal Bank Alerts <rbcroyalbankalerts@alerts.rbc.com>",
                    alerts.RBC_PURCHASE_SUBJECT, alerts.RBC_PURCHASE_BODY)


def test_rbc_alert_lands_on_the_account_with_that_card(db):
    from app.db.models import Account

    db.get(Account, "rbc_sam").card_last4 = "4321"
    db.get(Account, "rbc_visa").card_last4 = "7788"
    db.commit()
    assert process_message(db, FakeGmail([RBC]), "r1") == "created"
    txn = db.scalar(select(Transaction))
    assert (txn.account_id, str(txn.amount), txn.merchant_raw) == ("rbc_sam", "22.59", "CANADIAN TIRE STORE #2")


def test_card_digits_beat_the_parsers_default_account(db):
    """A second card at a bank (here a Tangerine card on another account) needs only accounts.card_last4."""
    from app.db.models import Account

    db.get(Account, "tangerine_cheq").card_last4 = "1234"
    db.commit()
    assert process_message(db, FakeGmail([TANGERINE]), "t1") == "created"
    assert db.scalar(select(Transaction)).account_id == "tangerine_cheq"


def test_shared_card_digits_fall_back_to_the_parsers_default_account(db):
    from app.db.models import Account

    db.get(Account, "tangerine_cheq").card_last4 = "1234"
    db.get(Account, "pc_money").card_last4 = "1234"
    db.commit()
    assert process_message(db, FakeGmail([TANGERINE]), "t1") == "created"
    assert db.scalar(select(Transaction)).account_id == "tangerine_mastercard"


def test_alert_for_an_unknown_card_is_kept_and_retried_once_the_card_is_known(db):
    from app.db.models import Account

    gmail = FakeGmail([RBC])
    assert backfill(db, gmail) == {"unparsed": 1}
    assert db.scalar(select(RawIngestLog)).payload["parse_status"] == "unknown_card"
    db.get(Account, "rbc_sam").card_last4 = "4321"
    db.commit()
    assert backfill(db, gmail) == {"created": 1}


def test_unparsed_alert_is_retried_by_a_later_backfill(db, monkeypatch):
    from app.daemons.parsers import rbc

    message = gmail_message("r2", "noreply@alerts.rbc.com", "Purchase alert", "You spent $8.75 at TIM HORTONS today.")
    gmail = FakeGmail([message])
    assert backfill(db, gmail) == {"unparsed": 1}
    # Once the parser understands the real wording, the next backfill picks the charge up.
    monkeypatch.setattr(rbc, "parse", lambda subject, body: rbc.ParsedAlert(
        amount=__import__("decimal").Decimal("8.75"), merchant="TIM HORTONS", account_id="rbc_visa"))
    assert backfill(db, gmail) == {"created": 1}
    assert count(db, Transaction) == 1
