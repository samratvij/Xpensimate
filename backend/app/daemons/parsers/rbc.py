import html
import re

from app.daemons.parsers.common import ParsedAlert, clean_merchant, parse_amount, signed

NAME = "RBC Royal Bank"
# Alerts come from rbcroyalbankalerts@alerts.rbc.com; Interac e-Transfer mail (payments.interac.ca) is not matched.
SENDER_DOMAINS = ("rbc.com", "rbcroyalbank.com")

# Real alert (Nov 2026), subject "You made a purchase.":
#   "... a purchase of $22.59 was made on your RBC Royal Bank credit card account ************1234 on
#    November 04, 2026 towards CANADIAN TIRE STORE #2."
# followed by "Label:" lines whose value is on the next line (Account, Purchase Amount, Transaction Date,
# Transaction Description). The plain-text part still carries HTML entities (&rsquo;, &#8209;).
# A household can have several RBC cards, so the account comes from the card's last four digits
# (accounts.card_last4), looked up by gmail_sync; account_id is left empty here.
SUBJECTS = re.compile(r"\b(purchase|transaction|charge|refund|credit)\b", re.IGNORECASE)
NOT_A_PURCHASE = re.compile(
    r"statement|payment (received|posted|due)|e-?transfer|deposit|sign[- ]?in|code|password|offer|reward",
    re.IGNORECASE,
)
DEBIT = re.compile(r"\b(debit card|client card|chequing|savings account)\b", re.IGNORECASE)
MASKED_CARD = re.compile(r"[*x•]{4,}\s*(\d{4})\b", re.IGNORECASE)
PROSE = re.compile(
    r"(?:purchase|transaction|refund|credit)\s+of\s+(?P<amount>-?\$\s?[\d,]+\.\d{2}).*?"
    r"\b(?:towards|at)\s+(?P<merchant>.+?)\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# Deposit alerts into a chequing or savings account (Sam's pay lands in RBC Chequing). No real sample yet:
# any subject naming a deposit is read; the account comes from its masked number, the payer from a
# Description/From line or "from X" in the text. One that can't be read is kept as unparsed to fix against.
DEPOSIT_SUBJECT = re.compile(r"\bdeposit", re.IGNORECASE)
NOT_A_DEPOSIT = re.compile(r"statement|sign[- ]?in|code|password|offer|reward|e-?transfer|request", re.IGNORECASE)
PAYER_PROSE = re.compile(r"\bfrom\s+(?P<payer>[A-Z][\w&'./ -]{1,60}?)(?=\s+(?:has|was|is|on|to|into)\b|[.,]|$)",
                         re.MULTILINE)


def is_deposit(subject: str) -> bool:
    return bool(DEPOSIT_SUBJECT.search(subject)) and not NOT_A_DEPOSIT.search(subject)


def is_transaction(subject: str) -> bool:
    return is_deposit(subject) or (bool(SUBJECTS.search(subject)) and not NOT_A_PURCHASE.search(subject))


def parse_deposit(text: str) -> ParsedAlert | None:
    amount_text = field(text, r"(?:Deposit |Transaction )?Amount")
    amount = parse_amount(amount_text or text)
    if amount is None or amount <= 0:
        return None
    payer = field(text, r"(?:Transaction |Deposit )?Description|From|Payer|Depositor")
    if not payer and (prose := PAYER_PROSE.search(text)):
        payer = prose["payer"]
    account = field(text, r"Account|To") or text
    suffix = MASKED_CARD.search(account) or MASKED_CARD.search(text)
    return ParsedAlert(amount=abs(amount), merchant=clean_merchant(payer or "Deposit"), account_id="",
                       card_suffix=suffix[1] if suffix else None, income=True)


def _plain(body: str) -> str:
    text = html.unescape(body).replace("‑", "-").replace("’", "'")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def field(text: str, label: str) -> str | None:
    """Value of 'Label: value', or of 'Label:' with the value on the next line."""
    match = re.search(rf"^(?:{label})[ \t]*:[ \t]*(.*)$\n?(.*)$", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return (match[1] or match[2]).strip() or None


def parse(subject: str, body: str) -> ParsedAlert | None:
    text = _plain(body)
    if is_deposit(subject):
        return parse_deposit(text)
    if DEBIT.search(text):
        return None
    amount_text = field(text, r"(?:Purchase |Transaction |Refund )?Amount")
    merchant = field(text, r"Transaction Description|Merchant(?: name)?")
    amount = parse_amount(amount_text) if amount_text else None
    prose = PROSE.search(text)
    if prose:
        amount = amount if amount is not None else parse_amount(prose["amount"])
        merchant = merchant or prose["merchant"]
    if amount is None or not merchant:
        return None
    account = field(text, r"Account") or text
    suffix = MASKED_CARD.search(account) or MASKED_CARD.search(text)
    return ParsedAlert(
        amount=signed(amount, f"{subject}\n{prose[0] if prose else ''}"),
        merchant=clean_merchant(merchant),
        account_id="",
        card_suffix=suffix[1] if suffix else None,
    )
