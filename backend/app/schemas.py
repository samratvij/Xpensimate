import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, computed_field

from app.core.accounts import ACCOUNT_KINDS
from app.core.taxonomy import DEFAULT_PURPOSE, budget_line, check_earner, check_purpose

NOTE_MAX = 1000

# Money stays Decimal internally but goes over the wire as a JSON number.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
# One of the household's purposes, checked against household.toml as it is now.
Purpose = Annotated[str, AfterValidator(check_purpose)]


class _BudgetLine(BaseModel):
    category: str
    purpose: str

    @computed_field
    @property
    def budget_line(self) -> str:
        return budget_line(self.category, self.purpose)


class TransactionPush(BaseModel):
    account_id: str | None = None
    timestamp: AwareDatetime
    amount: Decimal = Field(max_digits=10, decimal_places=2)
    # The household's currency when not given.
    currency: str | None = Field(None, min_length=3, max_length=3)
    merchant: str = Field(min_length=1, max_length=255)
    # Stable id of the upstream event, used to drop duplicates.
    source_ref: str | None = Field(None, max_length=255)


class TransactionPatch(BaseModel):
    category: str | None = Field(None, max_length=64)
    purpose: Purpose | None = None
    # Also save this as a rule so future transactions from the same merchant get it.
    remember: bool = False
    # True: not an expense (left out of totals, budgets and matching). False: count it again.
    excluded: bool | None = None
    # Free text; "" clears it, None leaves it as is.
    note: str | None = Field(None, max_length=NOTE_MAX)


class TransactionItemOut(_BudgetLine):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku_name: str
    category: str
    category_is_manual: bool
    purpose: str
    purpose_is_manual: bool
    price: Money
    quantity: Money
    note: str | None = None


class TransactionOut(_BudgetLine):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: str | None
    source: str
    timestamp: datetime
    amount: Money
    txn_type: str
    # Transfers: the own account the money went to (account_id is where it came from).
    counterpart_account_id: str | None = None
    currency: str
    merchant_raw: str
    merchant_normalized: str | None
    category: str
    category_is_manual: bool
    purpose: str
    purpose_is_manual: bool
    inference_confidence: Money
    categorized_by: str
    excluded: bool
    is_itemized: bool
    reconciliation_status: str
    edited_by: str | None
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class TransactionDetail(TransactionOut):
    items: list[TransactionItemOut]
    # Set when the lines came from a matched receipt (edit them there); None for a manual split or no lines.
    receipt_id: uuid.UUID | None = None


class SplitLine(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sku_name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(max_digits=10, decimal_places=2)
    category: str = Field(min_length=1, max_length=64)
    purpose: Purpose = DEFAULT_PURPOSE
    note: str | None = Field(None, max_length=NOTE_MAX)


class SplitIn(BaseModel):
    """Lines that must add up exactly to the charge. An empty list removes the split."""

    items: list[SplitLine] = Field(max_length=50)


class BudgetIn(BaseModel):
    monthly_limit: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    warning_threshold_percent: Decimal = Field(Decimal("80.00"), gt=0, le=100)


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    monthly_limit: Money
    warning_threshold_percent: Money


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    institution: str
    account_name: str
    currency: str
    kind: str = "chequing"
    owner: str = "Shared"


class TaxonomyOut(BaseModel):
    categories: list[str]
    purposes: list[str]
    # What budgets are set against: every category, plus the person and family lines.
    budget_lines: list[str]
    # The budget lines that belong to a person or family, not a category, with how to draw them
    # (person | abroad | family).
    person_lines: dict[str, str]
    # ISO 4217 code to show amounts in.
    currency: str
    # The household's people (who can earn income or own an account), without Shared.
    people: list[str] = []


class CategorySpend(BaseModel):
    # A budget line or a category, depending on the summary's group_by.
    category: str
    spent: Money
    # Still to come this month: recurring bills still due plus the estimate for everyday necessities
    # (bills_due + estimated), and spent + expected.
    expected: Money
    bills_due: Money
    estimated: Money
    projected: Money
    monthly_limit: Money | None
    warning_threshold_percent: Money | None
    percent_used: float | None
    over_warning: bool
    over_limit: bool
    projected_over_limit: bool


class IncomeTotals(BaseModel):
    """Money earned in the month (by who earned it; filtered to one person with the summary's purpose)."""

    received: Money
    by_person: dict[str, Money]
    # Paycheques of the household's income sources not seen yet this month (none for a past month).
    expected: Money
    expected_by_person: dict[str, Money]
    # Income received less money spent; projected adds what is still expected on both sides.
    left_after_spending: Money
    projected_left: Money


class SavingsTotals(BaseModel):
    """The household's month: what income was left after spending, and what was moved into savings and
    investment accounts (transfers between own accounts; market returns are never counted)."""

    # Income less spending, and that as a share of income (None without income).
    kept: Money
    rate: float | None
    to_savings: Money
    from_savings: Money
    to_investments: Money
    from_investments: Money
    net_to_savings: Money
    net_to_investments: Money
    # net_to_savings + net_to_investments
    moved: Money
    # The household's monthly savings target and how far "moved" is towards it (None without a target).
    target: Money | None = None
    target_percent: float | None = None
    # met | on_track | behind
    target_status: str | None = None


class SummaryOut(BaseModel):
    month: str
    group_by: str
    purpose: str | None
    total_spent: Money
    total_expected: Money
    total_bills_due: Money
    total_estimated: Money
    categories: list[CategorySpend]
    income: IncomeTotals
    # Household only: None when the summary is for one person.
    savings: SavingsTotals | None = None


class RecategorizeOut(BaseModel):
    examined: int
    changed: int


class RuleIn(BaseModel):
    merchant_pattern: str = Field(min_length=2, max_length=128)
    account_id: str | None = None
    target_category: str = Field(max_length=64)
    target_purpose: Purpose | None = None
    priority: int = Field(5, ge=0, le=100)
    hour_start: int | None = Field(None, ge=0, le=23)
    hour_end: int | None = Field(None, ge=1, le=24)
    weekdays_only: bool = False
    confidence: Decimal = Field(Decimal("1.00"), ge=0, le=1, decimal_places=2)


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_pattern: str
    account_id: str | None
    target_category: str
    target_purpose: str | None
    priority: int
    hour_start: int | None
    hour_end: int | None
    weekdays_only: bool
    confidence: Money


class ReceiptItemOut(_BudgetLine):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    sku_name: str
    raw_text: str | None
    category: str
    category_is_manual: bool
    purpose: str
    purpose_is_manual: bool
    price: Money
    quantity: Money
    note: str | None = None


class ReceiptItemPatch(BaseModel):
    category: str | None = Field(None, max_length=64)
    purpose: Purpose | None = None
    # "" clears it, None leaves it as is.
    note: str | None = Field(None, max_length=NOTE_MAX)


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant: str
    timestamp: datetime
    total: Money
    tax: Money
    needs_review: bool
    matched_transaction_id: uuid.UUID | None
    created_by: str | None
    created_at: datetime


class ReceiptDetail(ReceiptOut):
    items: list[ReceiptItemOut]


class CandidateOut(BaseModel):
    transaction: TransactionOut
    amount_diff: Money
    hours_apart: float
    merchant_score: float
    tip: bool


class MatchIn(BaseModel):
    transaction_id: uuid.UUID


class DeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    name: str | None = Field(None, max_length=128)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str | None
    created_at: datetime
    last_seen_at: datetime


class PushTestOut(BaseModel):
    devices: int
    delivered: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    month: str
    budget_line: str
    level: str
    monthly_limit: Money
    spent: Money
    transaction_id: uuid.UUID | None
    sent_at: datetime | None
    created_at: datetime


class RecurringIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    merchant_pattern: str | None = Field(None, min_length=2, max_length=128)
    category: str = Field(max_length=64)
    purpose: Purpose = DEFAULT_PURPOSE
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    amount_tolerance: Decimal = Field(Decimal("0.25"), ge=0, le=1, decimal_places=2)
    cadence: Literal["weekly", "biweekly", "monthly"]
    anchor_date: date
    active: bool = True


class RecurringPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    merchant_pattern: str | None = Field(None, min_length=2, max_length=128)
    category: str | None = Field(None, max_length=64)
    purpose: Purpose | None = None
    amount: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    amount_tolerance: Decimal | None = Field(None, ge=0, le=1, decimal_places=2)
    cadence: Literal["weekly", "biweekly", "monthly"] | None = None
    anchor_date: date | None = None
    active: bool | None = None


class RecurringOut(_BudgetLine):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    merchant_pattern: str | None
    amount: Money
    amount_tolerance: Money
    cadence: str
    anchor_date: date
    active: bool
    origin: str


class ExpectedOut(BaseModel):
    recurring: RecurringOut
    due: date
    status: str
    budget_line: str


class MarkPaidIn(BaseModel):
    paid_on: date | None = None
    amount: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)


class StatementImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    bank_format: str
    account_id: str
    period_start: date | None
    period_end: date | None
    status: str
    processed: int
    result: dict[str, Any] | None
    created_at: datetime
    # Counts per planned action (match, add, payment, unclear, imported, outside).
    counts: dict[str, int] = {}


class StatementPreviewOut(StatementImportOut):
    rows: list[dict[str, Any]]
    # App transactions for this account and period that aren't on the statement.
    missing: list[dict[str, Any]]


class StatementCommitIn(BaseModel):
    # Row index -> "add" | "skip" | "transfer" | "income" (deposits only) | "match:<transaction id>", for unclear
    # rows and deposits, or to override a plan.
    decisions: dict[str, str] = {}
    # Ids of missing transactions with "replaced_by" that are real after all: left as they are.
    keep: list[str] = []


class PhoneNotificationIn(BaseModel):
    package: str = Field(max_length=128)
    title: str = Field("", max_length=500)
    text: str = Field("", max_length=4000)
    posted_at: AwareDatetime
    # Android's notification key; with posted_at it identifies one notification across retries.
    key: str = Field(max_length=512)


class PhoneNotificationOut(BaseModel):
    status: str  # created | duplicate | ignored | unparsed
    transaction: TransactionOut | None = None


class MonthTotal(BaseModel):
    month: str
    spent: Money
    # Still to come (only the current month has any): bills still due plus estimated necessities.
    expected: Money
    bills_due: Money
    estimated: Money
    by_line: dict[str, Money]


class ReviewLine(BaseModel):
    budget_line: str
    spent: Money
    monthly_limit: Money | None
    previous: Money
    average_3m: Money


class AccountCoverage(BaseModel):
    account_id: str
    institution: str
    account_name: str
    transactions: int
    spent: Money
    statement_imported: bool
    statement_period: str | None


class MerchantTotal(BaseModel):
    merchant: str
    spent: Money
    count: int


class ReviewOut(BaseModel):
    month: str
    total_spent: Money
    previous_total: Money
    average_3m: Money
    total_budget: Money
    lines: list[ReviewLine]
    needs_check: list[TransactionOut]
    accounts: list[AccountCoverage]
    top_merchants: list[MerchantTotal]
    unmatched_receipts: int


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    kind: str
    severity: Literal["alert", "warn", "info"]
    title: str
    body: str
    # Dollars per month at stake; what the cards are ranked by within a severity.
    impact: Money
    # What tapping the card does: open_line (line), open_transaction (transaction_id), open_bills,
    # track_recurring (recurring: a RecurringIn body to POST to /recurring), remove_hold (transaction_id: offer to
    # mark it not a transaction; tapping the card opens it).
    action: Literal["open_line", "open_transaction", "open_bills", "track_recurring", "remove_hold"]
    line: str | None = None
    transaction_id: uuid.UUID | None = None
    recurring: dict[str, Any] | None = None


class DismissIn(BaseModel):
    key: str = Field(min_length=1, max_length=255)


class MeOut(BaseModel):
    # Whose token this request used.
    user: str


class CategoryOut(BaseModel):
    name: str
    transactions: int
    monthly_limit: Money | None
    # Everyday necessity: its usual monthly amount counts as still to come.
    estimated: bool = False
    # New recurring charges here become bills with renewal reminders.
    subscriptions: bool = False
    # Receipts may be matched to a charge that includes a tip.
    tipped: bool = False


class CategoryRoles(BaseModel):
    estimated: bool | None = None
    subscriptions: bool | None = None
    tipped: bool | None = None


class CategoryName(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class CategoryEdit(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # The current name this entry keeps or renames; None for a new category.
    original: str | None = None


class CategoryList(BaseModel):
    categories: list[CategoryEdit] = Field(min_length=1, max_length=100)


class HealthCheck(BaseModel):
    name: str
    status: Literal["ok", "warning", "error", "off"]
    detail: str


class HealthOut(BaseModel):
    checked_at: datetime
    checks: list[HealthCheck]


Earner = Annotated[str, AfterValidator(check_earner)]


class IncomeIn(BaseModel):
    """Income entered by hand (a deposit no alert or screenshot reported)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    received_on: date
    earner: Earner
    payer: str = Field(min_length=1, max_length=255)
    account_id: str | None = None
    category: Literal["Salary", "Other income"] = "Salary"
    note: str | None = Field(None, max_length=NOTE_MAX)


IncomeCadence = Literal["weekly", "biweekly", "semimonthly", "monthly"]


class IncomeSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    earner: Earner
    account_id: str | None = None
    payer_pattern: str = Field(min_length=2, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    amount_tolerance: Decimal = Field(Decimal("0.25"), ge=0, le=1, decimal_places=2)
    cadence: IncomeCadence
    anchor_date: date
    active: bool = True


class IncomeSourcePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    earner: Earner | None = None
    account_id: str | None = None
    payer_pattern: str | None = Field(None, min_length=2, max_length=128)
    amount: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    amount_tolerance: Decimal | None = Field(None, ge=0, le=1, decimal_places=2)
    cadence: IncomeCadence | None = None
    anchor_date: date | None = None
    active: bool | None = None


class IncomeSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    earner: str
    account_id: str | None
    payer_pattern: str
    amount: Money
    amount_tolerance: Money
    cadence: str
    anchor_date: date
    active: bool


class PayDateOut(BaseModel):
    source_id: uuid.UUID
    name: str
    earner: str
    day: date
    amount: Money
    status: str  # received | upcoming | late


class IncomeMonthOut(IncomeTotals):
    month: str
    earner: str | None
    deposits: list[TransactionOut]
    pay_dates: list[PayDateOut]


class AccountBalanceOut(BaseModel):
    account_id: str
    account_name: str
    institution: str
    kind: str
    owner: str
    # savings | investments | chequing | credit_cards | loans | other
    group: str
    # Credit cards and loans: what is owed. None: no balance recorded yet.
    balance: Money | None
    as_of: date | None
    # The balance is from an earlier month, carried forward.
    stale: bool
    source: str | None
    previous_balance: Money | None = None
    previous_as_of: date | None = None
    change: Money | None = None
    # Savings and investments: money moved in from the household's accounts (net) between the two balances, and
    # the rest of the change (market return, or interest).
    contributions: Money | None = None
    market_return: Money | None = None


class BalancesOut(BaseModel):
    month: str
    as_of: date
    assets: Money
    liabilities: Money
    net_worth: Money
    previous_net_worth: Money | None
    change: Money | None
    groups: dict[str, Money]
    contributions: Money
    market_return: Money
    # Accounts with no balance recorded at all (left out of net worth).
    missing: int
    accounts: list[AccountBalanceOut]


class BalanceIn(BaseModel):
    as_of: date
    # What the bank shows; for a credit card or loan, the amount owed.
    balance: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    note: str | None = Field(None, max_length=NOTE_MAX)


class BalanceSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: str
    as_of: date
    balance: Money
    source: str
    note: str | None
    created_by: str | None


class BalanceReadRow(BaseModel):
    shown_name: str
    balance: Money
    owed: bool
    currency: str
    # The household's account it was matched to; None when it couldn't tell (the person picks one).
    account_id: str | None
    account_name: str | None


class BalanceReadOut(BaseModel):
    """What a screenshot showed; nothing is saved until POST /balances/batch."""

    as_of: date
    rows: list[BalanceReadRow]


class BalanceBatchRow(BaseModel):
    account_id: str
    balance: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class BalanceBatchIn(BaseModel):
    as_of: date
    rows: list[BalanceBatchRow] = Field(min_length=1, max_length=60)
    source: Literal["screenshot", "manual"] = "screenshot"


class SavingsTargetIn(BaseModel):
    monthly_amount: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    alert_day: int = Field(25, ge=1, le=28)
    warning_percent: Decimal = Field(Decimal("80.00"), gt=0, le=100)


class SavingsTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    monthly_amount: Money
    alert_day: int
    warning_percent: Money


class TargetProgressOut(BaseModel):
    month: str
    target: Money
    saved: Money
    kept: Money
    percent: float
    remaining: Money
    status: str


# Account kinds a goal can draw on (app/core/accounts.py).
GoalKind = Literal[ACCOUNT_KINDS]


class GoalIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    target_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    target_date: date
    account_kinds: list[GoalKind] = []
    account_ids: list[str] = []


class GoalPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    target_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    target_date: date | None = None
    account_kinds: list[GoalKind] | None = None
    account_ids: list[str] | None = None
    active: bool | None = None


class GoalAccountOut(BaseModel):
    account_id: str
    account_name: str
    owner: str
    balance: Money | None
    as_of: date | None


class GoalOut(BaseModel):
    id: uuid.UUID
    name: str
    target_amount: Money
    target_date: date
    account_kinds: list[str]
    account_ids: list[str]
    active: bool
    current: Money
    remaining: Money
    percent: float
    months_left: int
    # What would reach the goal in the months left; None once reached or past the date.
    required_monthly: Money | None
    moved_this_month: Money
    oldest_balance: date | None
    accounts: list[GoalAccountOut]


class SavingsPlanOut(BaseModel):
    target: SavingsTargetOut | None
    progress: TargetProgressOut | None
    goals: list[GoalOut]
