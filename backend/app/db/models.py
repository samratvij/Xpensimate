import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.household import load_household
from app.core.taxonomy import DEFAULT_PURPOSE, UNCATEGORIZED
from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CategoryRow(Base):
    """An editable "what" category, in display order."""

    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    institution: Mapped[str] = mapped_column(String(64))
    account_name: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), server_default="CAD")
    # Last 4 digits of the card, as shown in Google Wallet notifications and bank alerts. Comma-separated when one
    # account has several cards (e.g. a shared credit card with a card for each person).
    card_last4: Mapped[str | None] = mapped_column(String(64))
    # Whose it is (a purpose: a person, or Shared).
    owner: Mapped[str] = mapped_column(String(32), server_default=DEFAULT_PURPOSE)
    # chequing | credit_card | savings | investment | retirement | loan | asset (app/core/accounts.py)
    kind: Mapped[str] = mapped_column(String(16), server_default="chequing")

    @property
    def card_digits(self) -> list[str]:
        """Every card number's last four digits on this account."""
        return [d.strip() for d in (self.card_last4 or "").split(",") if d.strip()]


class RawIngestLog(Base):
    __tablename__ = "raw_ingest_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # One row per upstream event (notification key, Gmail message id, ...).
        UniqueConstraint("source", "source_ref", name="uq_transactions_source_ref"),
        Index("idx_transactions_timestamp", "timestamp"),
        Index("idx_transactions_reconciliation", "reconciliation_status", "is_itemized"),
        Index("idx_transactions_purpose", "purpose"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Signed: purchases are positive, refunds negative.
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    txn_type: Mapped[str] = mapped_column(String(16), server_default="PURCHASE")
    currency: Mapped[str] = mapped_column(String(3), default=lambda: load_household().currency, server_default="CAD")
    merchant_raw: Mapped[str] = mapped_column(String(255))
    merchant_normalized: Mapped[str | None] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(64), server_default=UNCATEGORIZED)
    category_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    purpose: Mapped[str] = mapped_column(String(64), server_default=DEFAULT_PURPOSE)
    purpose_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    inference_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0.00")
    # sheet (imported history) | rule | history | llm | recurring | manual | statement | none: what produced category/purpose
    categorized_by: Mapped[str] = mapped_column(String(16), server_default="none")
    is_itemized: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # SETTLED | PENDING_MATCH | RECONCILED
    reconciliation_status: Mapped[str] = mapped_column(String(32), server_default="SETTLED")
    # Not spending (e.g. a credit card bill payment): kept so it isn't re-imported, but left out of totals.
    excluded: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Set when a bank statement row confirmed this transaction (or created it).
    statement_import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("statement_imports.id", ondelete="SET NULL"), index=True
    )
    # Set when this charge is a payment of a recurring expense.
    recurring_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_expenses.id", ondelete="SET NULL"), index=True
    )
    # Who last changed it by hand (whose API token), or entered it (a bill marked paid). None: only automation.
    edited_by: Mapped[str | None] = mapped_column(String(32))
    # The household's own words on what this was (free text, for remembering and planning).
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", passive_deletes=True
    )


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)
    sku_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    category_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    purpose: Mapped[str] = mapped_column(String(64), server_default=DEFAULT_PURPOSE)
    purpose_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Line total (quantity already applied), with its share of tax/tip once reconciled.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default="1.0")
    # A receipt line's note is copied from its receipt item (that's where it's edited).
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    transaction: Mapped[Transaction] = relationship(back_populates="items")


class BudgetEnvelope(Base):
    __tablename__ = "budget_envelopes"

    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    warning_threshold_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="80.00")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (Index("idx_receipts_timestamp_total", "timestamp", "total"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    merchant: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    tax: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default="0.00")
    image_path: Mapped[str | None] = mapped_column(Text)
    raw_ocr_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Line items + tax don't add up to the total (within a few cents): show the user before trusting it.
    needs_review: Mapped[bool] = mapped_column(Boolean, server_default="false")
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL")
    )
    # Who scanned it.
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()

    items: Mapped[list["ReceiptItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", passive_deletes=True, order_by="ReceiptItem.position"
    )


class ReceiptItem(Base):
    """A receipt line before reconciliation; copied into transaction_items once matched."""

    __tablename__ = "receipt_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    sku_name: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    category_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    purpose: Mapped[str] = mapped_column(String(64), server_default=DEFAULT_PURPOSE)
    purpose_is_manual: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Line total (quantity already applied); negative for discounts.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default="1.0")
    note: Mapped[str | None] = mapped_column(Text)

    receipt: Mapped[Receipt] = relationship(back_populates="items")


class CategorizationRule(Base):
    __tablename__ = "categorization_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    merchant_pattern: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    target_category: Mapped[str] = mapped_column(String(64))
    # None leaves purpose to the default rather than asserting one.
    target_purpose: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, server_default="1")
    # Optional local-time window [hour_start, hour_end) and weekday-only restriction.
    hour_start: Mapped[int | None] = mapped_column(Integer)
    hour_end: Mapped[int | None] = mapped_column(Integer)
    weekdays_only: Mapped[bool] = mapped_column(Boolean, server_default="false")
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="1.00")
    # starter (seeded generic) | history (derived from imported history) | user (API or "remember")
    origin: Mapped[str] = mapped_column(String(16), server_default="user")
    created_at: Mapped[datetime] = _created_at()


class GmailSyncState(Base):
    """Single row per mailbox: where Gmail history processing has reached and when the watch expires."""

    __tablename__ = "gmail_sync_state"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    history_id: Mapped[int] = mapped_column(BigInteger)
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Heartbeat for the health screen: the worker's last successful look at the mailbox, and its last failure.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()


class CategorizationExample(Base):
    """A past, human-categorized transaction used as context for Claude (imported history)."""

    __tablename__ = "categorization_examples"

    id: Mapped[uuid.UUID] = _uuid_pk()
    merchant_key: Mapped[str] = mapped_column(String(255), index=True)
    merchant_raw: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    spent_on: Mapped[date | None] = mapped_column(Date)
    trip: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Device(Base):
    """A phone that receives push notifications (Firebase Cloud Messaging registration token)."""

    __tablename__ = "devices"

    token: Mapped[str] = mapped_column(String(512), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = _updated_at()


class BudgetAlert(Base):
    """One alert per budget line, month and level; the unique key is what makes alerts fire only once."""

    __tablename__ = "budget_alerts"
    __table_args__ = (UniqueConstraint("month", "budget_line", "level", name="uq_budget_alerts_once"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM, local time
    budget_line: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16))  # WARNING | EXCEEDED
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    spent: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class RecurringExpense(Base):
    """A bill or membership that repeats on a cadence; drives "expected" spending in the summary."""

    __tablename__ = "recurring_expenses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128))
    # Whole-word pattern that identifies its charges; None for things paid outside cards (e.g. rent by e-transfer).
    merchant_pattern: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64), server_default=DEFAULT_PURPOSE)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # Charges within this fraction of `amount` count as payments (bills like Hydro vary month to month).
    amount_tolerance: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default="0.25")
    cadence: Mapped[str] = mapped_column(String(16))  # weekly | biweekly | monthly
    # A known due date; the schedule repeats from it (monthly keeps its day of month).
    anchor_date: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # When it was last switched off; a charge after this means it didn't really stop.
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(16), server_default="user")  # detected | user
    created_at: Mapped[datetime] = _created_at()


class InsightDismissal(Base):
    """An insight card someone swiped away; its key says how long that lasts (see app/services/insights.py)."""

    __tablename__ = "insight_dismissals"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    dismissed_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()


class StatementImport(Base):
    """One uploaded bank statement CSV: parsed rows and their planned actions, then the import's outcome."""

    __tablename__ = "statement_imports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    filename: Mapped[str] = mapped_column(String(255))
    bank_format: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    # preview | importing | done | failed
    status: Mapped[str] = mapped_column(String(16), server_default="preview")
    # The preview: one entry per statement row with its planned action (see app/statements/reconcile.py).
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    missing: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    processed: Mapped[int] = mapped_column(Integer, server_default="0")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
