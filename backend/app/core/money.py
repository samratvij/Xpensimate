"""Amounts in text the server writes itself (push notifications, insight cards), in the household's currency.

The apps format amounts with the phone's or browser's own number style; this is the server's simpler
equivalent: a symbol for common currencies, the 3-letter code otherwise.
"""
from decimal import Decimal

from app.core.household import load_household

SYMBOLS = {
    "CAD": "$", "USD": "$", "AUD": "$", "NZD": "$", "SGD": "$", "HKD": "$", "MXN": "$",
    "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "CNY": "¥", "KRW": "₩", "ILS": "₪", "NGN": "₦",
    "PHP": "₱", "THB": "฿", "TRY": "₺", "VND": "₫", "BRL": "R$", "ZAR": "R", "PLN": "zł ",
}
NO_CENTS = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF"}


def money(amount: Decimal | float, *, whole_from: Decimal | None = None, currency: str | None = None) -> str:
    """"$1,234.56", "−€12.00", "AED 59.33". With `whole_from`, amounts at or above it drop the cents."""
    code = currency or load_household().currency
    amount = Decimal(str(amount))
    sign = "−" if amount < 0 else ""
    amount = abs(amount)
    cents = code not in NO_CENTS and (whole_from is None or amount < whole_from)
    number = f"{amount:,.2f}" if cents else f"{amount:,.0f}"
    symbol = SYMBOLS.get(code, f"{code} ")
    return f"{sign}{symbol}{number}"
