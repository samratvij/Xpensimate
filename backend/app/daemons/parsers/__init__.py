"""Bank alert email parsers.

Each issuer module exposes NAME, SENDER_DOMAINS, is_transaction(subject) and
parse(subject, body) -> ParsedAlert | None. Only mail whose subject matches a known transaction-alert
template is parsed (and, if parsing fails, kept for inspection); everything else from the bank
(statements, offers, one-time codes) is ignored and never stored.
"""
from app.daemons.parsers import ctfs, pc_financial, rbc, tangerine, wealthsimple_email
from app.daemons.parsers.common import ParsedAlert

ISSUERS = (tangerine, pc_financial, ctfs, rbc, wealthsimple_email)


def issuer_for_sender(sender: str):
    """Match the From header's domain (or a subdomain of it) to an issuer module."""
    address = sender.rsplit("<", 1)[-1].rstrip(">").strip().lower()
    domain = address.rsplit("@", 1)[-1]
    for issuer in ISSUERS:
        if any(domain == d or domain.endswith("." + d) for d in issuer.SENDER_DOMAINS):
            return issuer
    return None


def gmail_query(newer_than: str = "1d") -> str:
    """Mail from the issuers' domains. An issuer that sends a lot of other mail (Wealthsimple) narrows its part with
    GMAIL_FILTER, so a search-based catch-up doesn't fetch every message and run into Gmail's per-minute quota."""
    plain = " OR ".join(d for i in ISSUERS if not getattr(i, "GMAIL_FILTER", None) for d in i.SENDER_DOMAINS)
    parts = [f"from:({plain})"] + [
        f"(from:({' OR '.join(i.SENDER_DOMAINS)}) {i.GMAIL_FILTER})" for i in ISSUERS if getattr(i, "GMAIL_FILTER", None)
    ]
    return f"{{{' '.join(parts)}}} newer_than:{newer_than}"


__all__ = ["ISSUERS", "ParsedAlert", "gmail_query", "issuer_for_sender"]
