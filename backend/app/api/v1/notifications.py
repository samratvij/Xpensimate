import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.daemons.parsers import enbd, google_wallet, wealthsimple
from app.db.base import get_db
from app.db.models import RawIngestLog
from app.schemas import PhoneNotificationIn, PhoneNotificationOut
from app.services import income
from app.services.accounts import account_for_alert
from app.services.ingest import NewTransaction, ingest_transaction

router = APIRouter(prefix="/notifications", tags=["notifications"])

PHONE_NOTIFICATION = "android_notification"
ANDROID_PUSH = "android_push"
PARSERS = {wealthsimple.PACKAGE: wealthsimple, google_wallet.PACKAGE: google_wallet,
           **{package: enbd for package in enbd.SMS_PACKAGES}}


@router.post("", response_model=PhoneNotificationOut)
def receive_notification(body: PhoneNotificationIn, db: Session = Depends(get_db)) -> PhoneNotificationOut:
    """A Wealthsimple, Google Wallet or bank SMS notification captured on the phone, parsed here so parsing can
    improve without an app update."""
    parser = PARSERS.get(body.package)
    if parser is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only Wealthsimple, Google Wallet and supported bank SMS notifications are accepted")

    parsed = parser.parse(body.title, body.text)
    if parsed is None:
        # Keep money-looking text we couldn't read (to fix the parser); drop promos and the like.
        keep = enbd.has_amount if parser is enbd else wealthsimple.has_amount
        if keep(f"{body.title} {body.text}"):
            db.add(RawIngestLog(source=PHONE_NOTIFICATION,
                                payload={**body.model_dump(mode="json"), "parse_status": "unparsed"}))
            db.commit()
            return PhoneNotificationOut(status="unparsed")
        return PhoneNotificationOut(status="ignored")

    account_id = account_for_alert(db, parsed.card_suffix, parsed.account_id, parsed.card_name)
    if parsed.income and not income.is_pay(db, account_id, parsed.merchant, parsed.amount):
        return PhoneNotificationOut(status="ignored")  # money in that isn't pay (a transfer, a gift)
    ref =hashlib.sha1(f"{body.key}|{body.posted_at.isoformat()}".encode()).hexdigest()[:24]
    txn, created = ingest_transaction(
        db,
        NewTransaction(
            source=ANDROID_PUSH,
            source_ref=f"{parser.REF_PREFIX}:{ref}",
            account_id=account_id,
            timestamp=body.posted_at,
            amount=parsed.amount,
            merchant=parsed.merchant,
            currency=parsed.currency,
            reconciliation_status="PENDING_MATCH",
            income=parsed.income,
        ),
        raw_payload={**body.model_dump(mode="json"), "parsed": {"amount": str(parsed.amount), "merchant": parsed.merchant}},
    )
    return PhoneNotificationOut(status="created" if created else "duplicate", transaction=txn)
