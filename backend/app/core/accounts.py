"""Kinds of account. Spending accounts (chequing, credit cards) are where purchases and pay show up; savings,
investment and retirement accounts hold the household's money; a loan is owed; an asset is anything else of value.

The kinds are deliberately general. Regional names for the same thing (a Canadian TFSA, a US 401(k), a UK ISA)
are accepted in household.toml and stored as the general kind, so the rest of the app needs no regional rules.
"""

SPENDING_KINDS = ("chequing", "credit_card")
SAVINGS_KINDS = ("savings",)
INVESTMENT_KINDS = ("investment", "retirement")
LIABILITY_KINDS = ("credit_card", "loan")
ACCOUNT_KINDS = (*SPENDING_KINDS, *SAVINGS_KINDS, *INVESTMENT_KINDS, "loan", "asset")

# What the apps call each kind.
KIND_LABELS = {
    "chequing": "Chequing / current",
    "credit_card": "Credit card",
    "savings": "Savings",
    "investment": "Investment",
    "retirement": "Retirement",
    "loan": "Loan",
    "asset": "Other asset",
}

ALIASES = {
    "checking": "chequing", "current": "chequing", "debit": "chequing", "cash": "chequing",
    "credit": "credit_card", "card": "credit_card",
    "hisa": "savings",
    "tfsa": "investment", "fhsa": "investment", "non_registered": "investment", "brokerage": "investment",
    "isa": "investment", "resp": "investment",
    "rrsp": "retirement", "rrif": "retirement", "lira": "retirement", "401k": "retirement", "403b": "retirement",
    "ira": "retirement", "roth_ira": "retirement", "pension": "retirement", "sipp": "retirement",
    "mortgage": "loan", "line_of_credit": "loan",
}


def normalize_kind(value: str) -> str | None:
    """The general kind for `value` ("Checking", "TFSA", "401(k)"), or None if it isn't one."""
    key = "".join(c if c.isalnum() else "_" for c in value.strip().lower()).strip("_")
    key = {"401_k": "401k", "403_b": "403b"}.get(key, key)
    return key if key in ACCOUNT_KINDS else ALIASES.get(key)
