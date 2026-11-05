from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.taxonomy import DEFAULT_PURPOSE, UNCATEGORIZED
from app.db.models import Account, CategorizationExample, CategorizationRule, Transaction, TransactionItem
from app.services.llm import classify_with_claude
from app.services.categories import category_names
from app.services.merchant import matches_pattern, merchant_key, normalize_merchant

# A merchant's previous categorization is reused only if it was at least this confident.
HISTORY_MIN_CONFIDENCE = Decimal("0.60")
MANUAL_HISTORY_CONFIDENCE = Decimal("0.95")
MAX_HINT_PAIRS = 6
# An imported past categorization is "this same purchase" when the merchant and amount agree and the dates are this close.
EXACT_MATCH_DAYS = 2
MAX_HINT_NOTES = 8


@dataclass(frozen=True)
class Classification:
    merchant_normalized: str
    category: str = UNCATEGORIZED
    purpose: str = DEFAULT_PURPOSE
    confidence: Decimal = Decimal("0.00")
    source: str = "none"  # rule | history | llm | none


def _rule_applies(rule: CategorizationRule, account_id: str | None, local: datetime) -> bool:
    if rule.account_id is not None and rule.account_id != account_id:
        return False
    if rule.weekdays_only and local.weekday() >= 5:
        return False
    if rule.hour_start is not None and local.hour < rule.hour_start:
        return False
    if rule.hour_end is not None and local.hour >= rule.hour_end:
        return False
    return True


def match_rule(
    db: Session, *, merchant_raw: str, merchant: str, account_id: str | None, local: datetime
) -> CategorizationRule | None:
    """Stage 1: highest priority wins, then account-specific, then the longest (most specific) pattern."""
    candidates = [
        rule
        for rule in db.scalars(select(CategorizationRule))
        if matches_pattern(rule.merchant_pattern, merchant, merchant_raw) and _rule_applies(rule, account_id, local)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.priority, r.account_id is not None, len(r.merchant_pattern)))


def _from_history(db: Session, merchant: str, exclude_id=None) -> Transaction | None:
    query = (
        select(Transaction)
        .where(Transaction.merchant_normalized == merchant, Transaction.category != UNCATEGORIZED)
        .order_by(Transaction.category_is_manual.desc(), Transaction.timestamp.desc())
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(Transaction.id != exclude_id)
    prior = db.scalar(query)
    if prior is None:
        return None
    if not prior.category_is_manual and prior.inference_confidence < HISTORY_MIN_CONFIDENCE:
        return None
    return prior


def exact_example(db: Session, merchant_raw: str, amount: Decimal, on: date) -> CategorizationExample | None:
    """The household's own categorization of this very purchase (from imported history, categorization_examples), if there is one."""
    return db.scalar(
        select(CategorizationExample)
        .where(
            CategorizationExample.merchant_key == merchant_key(merchant_raw),
            # Imported history records spending as negative amounts.
            CategorizationExample.amount == -amount,
            CategorizationExample.spent_on.between(on - timedelta(days=EXACT_MATCH_DAYS), on + timedelta(days=EXACT_MATCH_DAYS)),
            CategorizationExample.category.is_not(None),
        )
        .limit(1)
    )


def history_hint(db: Session, merchant_raw: str) -> str | None:
    """Summary of how the household categorized this merchant before, and their notes on it, for Claude."""
    parts = []
    examples = list(
        db.scalars(
            select(CategorizationExample)
            .where(CategorizationExample.merchant_key == merchant_key(merchant_raw))
            .order_by(CategorizationExample.spent_on.desc().nulls_last())
        )
    )
    if examples:
        pairs = Counter((e.category or "?", e.purpose) for e in examples)
        lines = [f"{n}x {category} / {purpose}" for (category, purpose), n in pairs.most_common(MAX_HINT_PAIRS)]
        parts.append(f"The household categorized this merchant {len(examples)} times before: " + "; ".join(lines) + ".")
    notes = [f"{e.note} ({e.category or '?'} / {e.purpose})" for e in examples if e.note]
    notes = _app_notes(db, normalize_merchant(merchant_raw)) + notes
    if notes:
        parts.append("Their notes on recent purchases here: " + "; ".join(notes[:MAX_HINT_NOTES]) + ".")
    return "\n".join(parts) or None


def _app_notes(db: Session, merchant_normalized: str) -> list[str]:
    """Notes written in the app on this merchant's charges and split/receipt lines, newest first."""
    charges = db.execute(
        select(Transaction.note, Transaction.category, Transaction.purpose, Transaction.timestamp)
        .where(Transaction.merchant_normalized == merchant_normalized, Transaction.note.is_not(None))
        .order_by(Transaction.timestamp.desc())
        .limit(MAX_HINT_NOTES)
    ).all()
    lines = db.execute(
        select(TransactionItem.note, TransactionItem.category, TransactionItem.purpose, Transaction.timestamp,
               TransactionItem.sku_name)
        .join(Transaction, TransactionItem.transaction_id == Transaction.id)
        .where(Transaction.merchant_normalized == merchant_normalized, TransactionItem.note.is_not(None))
        .order_by(Transaction.timestamp.desc())
        .limit(MAX_HINT_NOTES)
    ).all()
    found = [(r.timestamp, f"{r.note} ({r.category} / {r.purpose})") for r in charges]
    found += [(r.timestamp, f"{r.sku_name}: {r.note} ({r.category} / {r.purpose})") for r in lines]
    return [text for _, text in sorted(found, key=lambda f: f[0], reverse=True)]


def categorize(
    db: Session,
    *,
    merchant: str,
    account_id: str | None,
    amount: Decimal,
    timestamp: datetime,
    currency: str = "CAD",
    exclude_id=None,
    bank_category: str | None = None,
) -> Classification:
    settings = get_settings()
    normalized = normalize_merchant(merchant)
    local = timestamp.astimezone(ZoneInfo(settings.timezone))

    if example := exact_example(db, merchant, amount, local.date()):
        return Classification(
            merchant_normalized=normalized,
            category=example.category,
            purpose=example.purpose,
            confidence=Decimal("1.00"),
            source="sheet",
        )

    if rule := match_rule(db, merchant_raw=merchant, merchant=normalized, account_id=account_id, local=local):
        return Classification(
            merchant_normalized=normalized,
            category=rule.target_category,
            purpose=rule.target_purpose or DEFAULT_PURPOSE,
            confidence=rule.confidence,
            source="rule",
        )

    if prior := _from_history(db, normalized, exclude_id):
        return Classification(
            merchant_normalized=normalized,
            category=prior.category,
            purpose=prior.purpose,
            confidence=MANUAL_HISTORY_CONFIDENCE if prior.category_is_manual else prior.inference_confidence,
            source="history",
        )

    if settings.anthropic_api_key:
        account = db.get(Account, account_id) if account_id else None
        result = classify_with_claude(
            merchant_raw=merchant,
            merchant=normalized,
            amount=amount,
            currency=currency,
            account=f"{account.institution} {account.account_name}" if account else None,
            local_time=local,
            categories=category_names(db),
            history="\n".join(
                h for h in (history_hint(db, merchant), bank_category and f"The bank labels it: {bank_category}.") if h
            ) or None,
        )
        if result is not None:
            confidence = min(max(Decimal(str(round(result.confidence, 2))), Decimal("0.00")), Decimal("1.00"))
            return Classification(
                merchant_normalized=normalized,
                category=result.category,
                purpose=result.purpose,
                confidence=confidence,
                source="llm",
            )

    return Classification(merchant_normalized=normalized)


def apply_classification(txn: Transaction, result: Classification) -> None:
    """Write a classification onto a transaction without touching user overrides."""
    txn.merchant_normalized = result.merchant_normalized
    if not txn.category_is_manual:
        txn.category = result.category
    if not txn.purpose_is_manual:
        txn.purpose = result.purpose
    txn.inference_confidence = result.confidence
    txn.categorized_by = result.source
