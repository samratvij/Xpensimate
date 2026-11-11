"""Wealthsimple emails (notifications@o.wealthsimple.com). Card spending comes from the phone's notifications
(parsers/wealthsimple.py); from email only pay is read: "Direct deposit received".

Real email (Oct 2026), plain-text part:
    We got your direct deposit!
    Amount: $3,250.40
    From: NORTHWIND
    Your deposit is now available in your chequing account.

A health-insurance claim paid into Cash ("From: MANULIFE") is money back, as from the phone (parsers/wealthsimple.py).

Moves between the household's own Wealthsimple accounts are transfers (app/services/savings.py). The email
names the two accounts as Wealthsimple shows them, e.g. subject "You made a transfer" (or "<Name> made a
transfer"), body "From: Chequing / To: 🏠 Family (Joint Chequing)", sometimes "Initiated by: <Name>". The
parser keeps the names; app/services/accounts.py matches them to the household's accounts by name, kind and
whose they are.
"""
import re

from app.daemons.parsers.common import ParsedAlert, clean_merchant, labelled, parse_amount
from app.daemons.parsers.wealthsimple import insurer_in

NAME = "Wealthsimple"
SENDER_DOMAINS = ("wealthsimple.com",)

SUBJECTS = re.compile(r"\b(direct deposit received|made a transfer)\b", re.IGNORECASE)
TRANSFER_SUBJECT = re.compile(r"\bmade a transfer\b", re.IGNORECASE)
# Wealthsimple sends a lot of other mail (orders, dividends, offers): searches only ask Gmail for these.
GMAIL_FILTER = '(subject:"Direct deposit received" OR subject:"made a transfer")'

# Who made the move, when it wasn't the mailbox's owner: "<Name> made a transfer" or "Initiated by: <Name>".
INITIATOR = re.compile(r"^\s*(?!you\b)([A-Z][\w'-]+)\b[^\n]*made a transfer|initiated by:\s*([A-Z][\w'-]+)",
                       re.IGNORECASE | re.MULTILINE)


def account_label(name: str) -> str:
    return re.sub(r"^[^\w]+", "", name).strip()  # drop a leading emoji


def parse_transfer(subject: str, body: str) -> ParsedAlert | None:
    amount_text, src, dst = labelled(body, "Amount"), labelled(body, "From"), labelled(body, "To")
    amount = parse_amount(amount_text) if amount_text else None
    if amount is None or amount <= 0 or not src or not dst:
        return None
    who = INITIATOR.search(f"{subject}\n{body}")
    src, dst = account_label(src), account_label(dst)
    if src.lower() == dst.lower():
        return None
    # The account ids are left to the caller, which knows the household's accounts.
    return ParsedAlert(amount=amount, merchant=f"Wealthsimple transfer: {clean_merchant(src)} → {clean_merchant(dst)}",
                       account_id="", transfer=True, from_name=src, to_name=dst,
                       person=(who[1] or who[2]) if who else None)


def is_transaction(subject: str) -> bool:
    return bool(SUBJECTS.search(subject))


def parse(subject: str, body: str) -> ParsedAlert | None:
    if TRANSFER_SUBJECT.search(subject):
        return parse_transfer(subject, body)
    amount_text, payer = labelled(body, "Amount"), labelled(body, "From")
    amount = parse_amount(amount_text) if amount_text else None
    if amount is None or amount <= 0 or not payer:
        return None
    if insurer := insurer_in(payer):
        # A health-insurance claim paid out: money back against the original charge, as from the phone.
        return ParsedAlert(amount=-amount, merchant=insurer, account_id="ws_cash")
    # The deposit lands in the mailbox owner's chequing (Wealthsimple Cash).
    return ParsedAlert(amount=amount, merchant=clean_merchant(payer), account_id="ws_cash", income=True)
