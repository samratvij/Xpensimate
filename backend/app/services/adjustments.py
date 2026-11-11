"""Charges the bank replaced: an order that changed after it was placed.

DoorDash, Walmart and the like first hold the order's original total, then charge the final amount once the
order is done (a refund for a mistake, an item removed). The bank alerts on both, then quietly drops the hold,
but the app has already recorded the hold as a purchase, so the order counts twice.

Two places look for this:
  statement import  an app charge that isn't on the statement, with a charge from the same merchant on the
                    statement within a few days, was a hold. Importing excludes it unless the user keeps it.
                    (app/statements/reconcile.py)
  insights          before the statement is in: a second charge, at a different amount, from the same merchant
                    and card soon after a charge from a merchant known to adjust orders. A card asks whether the
                    first was only a hold. (app/services/insights.py)
"""
import re
from datetime import timedelta
from decimal import Decimal

from app.services.merchant import merchant_key
from app.services.reconciler import merchant_score

CENT = Decimal("0.01")
# Statement import: how long after a hold its final charge can be dated. A day early too: alerts are dated when
# the email arrives, statements by the transaction date.
STATEMENT_WINDOW_DAYS = (-1, 7)
STATEMENT_MERCHANT_SCORE = 0.6
# Insights: merchants whose final charge often differs from the first one, and how long the order can take.
# Delivery orders settle the same day; grocery orders when they are picked up or delivered.
ADJUSTING_MERCHANTS = {
    "DOORDASH": 1,
    "UBEREATS": 1,
    "SKIPTHEDISHES": 1,
    "INSTACART": 3,
    "WALMART": 7,
    "VOILA": 7,
}
ALERT_MERCHANT_SCORE = 0.8
# Their subscriptions are fixed charges, not orders.
NOT_ORDERS = ("DASHPASS", "UBERONE")


def _letters(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


def _brand(name: str) -> str | None:
    letters = _letters(name)
    if any(word in letters for word in NOT_ORDERS):
        return None
    return next((b for b in ADJUSTING_MERCHANTS if b in letters), None)


def same_merchant(a: str, b: str, min_score: float, *, brands: bool = False) -> bool:
    """The same shop under two spellings. With brands, a merchant that adjusts orders matches however each side
    spells it ("WAL-MART #1061" and "Walmart"): a statement and an alert name it differently."""
    if merchant_key(a).lower() == merchant_key(b).lower():
        return True
    if brands and (brand := _brand(a)) is not None and brand == _brand(b):
        return True
    return merchant_score(a, b) >= min_score


def adjust_window(merchant: str) -> timedelta | None:
    """How long after a charge from this merchant its final charge can come; None if it isn't one that adjusts."""
    brand = _brand(merchant)
    return timedelta(days=ADJUSTING_MERCHANTS[brand]) if brand else None


def suspected_holds(txns: list) -> list[tuple]:
    """(hold, final) pairs among live charges, oldest first: a charge from a merchant that adjusts orders, then a
    different amount from the same merchant on the same card within that merchant's window. A charge that a
    statement already confirmed was real, and a bill's payment is expected, so neither is a hold. Each charge is
    in at most one pair."""
    txns = sorted(txns, key=lambda t: t.timestamp)
    used: set = set()
    pairs = []
    for i, held in enumerate(txns):
        window = adjust_window(held.merchant_raw)
        if (window is None or held.id in used or held.statement_import_id is not None or held.account_id is None
                or held.recurring_id is not None):
            continue
        for final in txns[i + 1:]:
            if final.timestamp - held.timestamp > window:
                break
            if (final.id in used or final.account_id != held.account_id or final.recurring_id is not None
                    or abs(final.amount - held.amount) <= CENT
                    or not same_merchant(held.merchant_raw, final.merchant_raw, ALERT_MERCHANT_SCORE)):
                continue
            used |= {held.id, final.id}
            pairs.append((held, final))
            break
    return pairs
