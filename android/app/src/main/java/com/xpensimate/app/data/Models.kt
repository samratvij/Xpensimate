package com.xpensimate.app.data

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Mirrors the backend's /api/v1 JSON (backend/app/schemas.py). Money arrives as JSON numbers.

@Serializable
data class Transaction(
    val id: String,
    @SerialName("account_id") val accountId: String? = null,
    val source: String,
    val timestamp: String,
    val amount: Double,
    @SerialName("txn_type") val txnType: String,
    /** Transfers: the own account the money went to (accountId is where it came from; null = not known). */
    @SerialName("counterpart_account_id") val counterpartAccountId: String? = null,
    val currency: String,
    @SerialName("merchant_raw") val merchantRaw: String,
    @SerialName("merchant_normalized") val merchantNormalized: String? = null,
    val category: String,
    @SerialName("category_is_manual") val categoryIsManual: Boolean,
    val purpose: String,
    @SerialName("purpose_is_manual") val purposeIsManual: Boolean,
    @SerialName("inference_confidence") val inferenceConfidence: Double,
    @SerialName("categorized_by") val categorizedBy: String,
    val excluded: Boolean = false,
    @SerialName("is_itemized") val isItemized: Boolean,
    @SerialName("reconciliation_status") val reconciliationStatus: String,
    @SerialName("budget_line") val budgetLine: String,
    val items: List<TransactionItem> = emptyList(),
    /** Set when [items] came from a matched receipt (edited there); null for a manual split. */
    @SerialName("receipt_id") val receiptId: String? = null,
    /** Free text: where exactly, what for, who it was for. */
    val note: String? = null,
    /** Who last changed it by hand (whose API token). */
    @SerialName("edited_by") val editedBy: String? = null,
) {
    val merchant: String get() = merchantNormalized ?: merchantRaw

    /** Money earned (pay): never spending; its category is an income kind and its purpose who earned it. */
    val isIncome: Boolean get() = txnType == "INCOME"

    /** Money moved between the household's own accounts: never spending. */
    val isTransfer: Boolean get() = txnType == "TRANSFER"

    /** A guess the user should glance at: picked by Claude (or nothing) with low confidence, never confirmed. */
    val needsCheck: Boolean
        get() = !excluded && !isIncome && !categoryIsManual && !purposeIsManual &&
            (categorizedBy == "none" || (categorizedBy == "llm" && inferenceConfidence < 0.6))
}

@Serializable
data class TransactionItem(
    val id: String,
    @SerialName("sku_name") val skuName: String,
    val category: String,
    val purpose: String,
    val price: Double,
    val quantity: Double,
    @SerialName("budget_line") val budgetLine: String,
    val note: String? = null,
)

/** One line of a manual split; price is a decimal string ("30.00") so cents stay exact. */
@Serializable
data class SplitLine(
    @SerialName("sku_name") val skuName: String,
    val price: String,
    val category: String,
    val purpose: String,
    val note: String? = null,
)

/** Lines must add up to the charge; an empty list removes the split. */
@Serializable
data class SplitRequest(val items: List<SplitLine>)

@Serializable
data class TransactionPatch(
    val category: String? = null,
    val purpose: String? = null,
    val remember: Boolean = false,
    val excluded: Boolean? = null,
    /** "" clears the note; null (not sent) leaves it as is. */
    val note: String? = null,
)

@Serializable
data class LineSpend(
    val category: String,
    val spent: Double,
    /** Still to come this month: bills not yet paid plus the rest of the usual spend on a necessity. */
    val expected: Double,
    @SerialName("bills_due") val billsDue: Double = 0.0,
    val estimated: Double = 0.0,
    val projected: Double,
    @SerialName("monthly_limit") val monthlyLimit: Double? = null,
    @SerialName("percent_used") val percentUsed: Double? = null,
    @SerialName("over_warning") val overWarning: Boolean,
    @SerialName("over_limit") val overLimit: Boolean,
    @SerialName("projected_over_limit") val projectedOverLimit: Boolean,
)

@Serializable
data class Summary(
    val month: String,
    val purpose: String? = null,
    @SerialName("total_spent") val totalSpent: Double,
    @SerialName("total_expected") val totalExpected: Double,
    @SerialName("total_bills_due") val totalBillsDue: Double = 0.0,
    @SerialName("total_estimated") val totalEstimated: Double = 0.0,
    val categories: List<LineSpend>,
    val income: IncomeTotals = IncomeTotals(),
    /** Household only: null when the summary is for one person. */
    val savings: SavingsTotals? = null,
)

/** Income less spending (and as a rate), and money moved into and out of savings and investment accounts. */
@Serializable
data class SavingsTotals(
    val kept: Double = 0.0,
    val rate: Double? = null,
    @SerialName("to_savings") val toSavings: Double = 0.0,
    @SerialName("from_savings") val fromSavings: Double = 0.0,
    @SerialName("to_investments") val toInvestments: Double = 0.0,
    @SerialName("from_investments") val fromInvestments: Double = 0.0,
    @SerialName("net_to_savings") val netToSavings: Double = 0.0,
    @SerialName("net_to_investments") val netToInvestments: Double = 0.0,
    val moved: Double = 0.0,
    /** The monthly savings target and how far "moved" is towards it; status met | on_track | behind. */
    val target: Double? = null,
    @SerialName("target_percent") val targetPercent: Double? = null,
    @SerialName("target_status") val targetStatus: String? = null,
)

/** The month's income: received and still expected (by who earned it), and what's left after spending. */
@Serializable
data class IncomeTotals(
    val received: Double = 0.0,
    @SerialName("by_person") val byPerson: Map<String, Double> = emptyMap(),
    val expected: Double = 0.0,
    @SerialName("expected_by_person") val expectedByPerson: Map<String, Double> = emptyMap(),
    @SerialName("left_after_spending") val leftAfterSpending: Double = 0.0,
    @SerialName("projected_left") val projectedLeft: Double = 0.0,
)

val INCOME_KINDS = listOf("Salary", "Other income")
/** The household's people, from /taxonomy when the app starts. Compose state, so screens redraw when it arrives. */
object HouseholdPeople {
    var names by mutableStateOf<List<String>>(emptyList())
}

/** Who income can belong to: each person in the household, or Shared. */
val EARNERS: List<String> get() = HouseholdPeople.names + "Shared"

@Serializable
data class PayDate(
    @SerialName("source_id") val sourceId: String,
    val name: String,
    val earner: String,
    val day: String,
    val amount: Double,
    /** received | upcoming | late */
    val status: String,
)

@Serializable
data class IncomeMonth(
    val month: String,
    val received: Double,
    @SerialName("by_person") val byPerson: Map<String, Double> = emptyMap(),
    val expected: Double,
    @SerialName("left_after_spending") val leftAfterSpending: Double = 0.0,
    val deposits: List<Transaction> = emptyList(),
    @SerialName("pay_dates") val payDates: List<PayDate> = emptyList(),
)

/** Income entered by hand; amount is a decimal string so cents stay exact. */
@Serializable
data class IncomeIn(
    val amount: String,
    @SerialName("received_on") val receivedOn: String,
    val earner: String,
    val payer: String,
    val category: String,
    @SerialName("account_id") val accountId: String? = null,
)

@Serializable
data class Taxonomy(
    val categories: List<String>,
    val purposes: List<String>,
    @SerialName("budget_lines") val budgetLines: List<String> = emptyList(),
    // Budget lines that belong to a person or family rather than a category: line -> person | abroad | family.
    @SerialName("person_lines") val personLines: Map<String, String> = emptyMap(),
    // ISO 4217 code the household's amounts are shown in.
    val currency: String = "CAD",
    // The household's people (without Shared).
    val people: List<String> = emptyList(),
)

@Serializable
data class Account(
    val id: String,
    val institution: String,
    @SerialName("account_name") val accountName: String,
    val owner: String = "Shared",
    /** chequing | savings | credit_card | tfsa | rrsp | fhsa | non_registered | loan | asset */
    val kind: String = "chequing",
)

@Serializable
data class Receipt(
    val id: String,
    val merchant: String,
    val timestamp: String,
    val total: Double,
    val tax: Double,
    @SerialName("needs_review") val needsReview: Boolean,
    @SerialName("matched_transaction_id") val matchedTransactionId: String? = null,
    val items: List<ReceiptItem> = emptyList(),
)

@Serializable
data class ReceiptItem(
    val id: String,
    @SerialName("sku_name") val skuName: String,
    @SerialName("raw_text") val rawText: String? = null,
    val category: String,
    @SerialName("category_is_manual") val categoryIsManual: Boolean,
    val purpose: String,
    @SerialName("purpose_is_manual") val purposeIsManual: Boolean,
    val price: Double,
    val quantity: Double,
    @SerialName("budget_line") val budgetLine: String,
    val note: String? = null,
)

/** A null field (not sent) is left as is; note "" clears it. */
@Serializable
data class ItemPatch(val category: String? = null, val purpose: String? = null, val note: String? = null)

@Serializable
data class MatchCandidate(
    val transaction: Transaction,
    @SerialName("amount_diff") val amountDiff: Double,
    @SerialName("hours_apart") val hoursApart: Double,
    val tip: Boolean,
)

@Serializable
data class MatchRequest(@SerialName("transaction_id") val transactionId: String)

@Serializable
data class Recurring(
    val id: String,
    val name: String,
    @SerialName("merchant_pattern") val merchantPattern: String? = null,
    val category: String,
    val purpose: String,
    val amount: Double,
    val cadence: String,
    val active: Boolean,
    @SerialName("budget_line") val budgetLine: String,
)

@Serializable
data class ExpectedPayment(
    val recurring: Recurring,
    val due: String,
    val status: String,
    @SerialName("budget_line") val budgetLine: String,
)

@Serializable
data class MarkPaid(@SerialName("paid_on") val paidOn: String? = null, val amount: Double? = null)

@Serializable
data class DeviceRegistration(val token: String, val name: String?)

@Serializable
data class PushTestResult(val devices: Int, val delivered: Int)

@Serializable
data class BudgetAlert(
    val id: String,
    val month: String,
    @SerialName("budget_line") val budgetLine: String,
    val level: String,
    @SerialName("monthly_limit") val monthlyLimit: Double,
    val spent: Double,
    @SerialName("transaction_id") val transactionId: String? = null,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class Budget(
    val category: String,
    @SerialName("monthly_limit") val monthlyLimit: Double,
    @SerialName("warning_threshold_percent") val warningThresholdPercent: Double,
)

@Serializable
data class BudgetUpdate(
    @SerialName("monthly_limit") val monthlyLimit: Double,
    @SerialName("warning_threshold_percent") val warningThresholdPercent: Double,
)

@Serializable
data class TxnBrief(
    val id: String,
    val merchant: String,
    val amount: String,
    val date: String,
    val source: String,
    /** Statement "missing" only: the statement charge that replaced this one (a hold for an order that changed). */
    @SerialName("replaced_by") val replacedBy: ReplacedBy? = null,
)

@Serializable
data class ReplacedBy(val index: Int, val merchant: String, val amount: String, val date: String)

@Serializable
data class StatementLine(
    val index: Int,
    val date: String,
    val merchant: String,
    val amount: String,
    val kind: String,
    @SerialName("bank_category") val bankCategory: String? = null,
    val action: String,
    val transaction: TxnBrief? = null,
    val pays: TxnBrief? = null,
    val candidates: List<TxnBrief> = emptyList(),
    /** Deposits and transfers: what the app thinks it is ("move:<account>", "transfer"); applies unless changed. */
    val suggested: String? = null,
    /** Screenshots only: not yet posted by the bank. */
    val pending: Boolean = false,
)

@Serializable
data class StatementImport(
    val id: String,
    val filename: String,
    @SerialName("account_id") val accountId: String,
    @SerialName("period_start") val periodStart: String? = null,
    @SerialName("period_end") val periodEnd: String? = null,
    val status: String,
    val processed: Int,
    val result: kotlinx.serialization.json.JsonObject? = null,
    val counts: Map<String, Int> = emptyMap(),
    val rows: List<StatementLine> = emptyList(),
    val missing: List<TxnBrief> = emptyList(),
)

@Serializable
data class StatementCommit(
    val decisions: Map<String, String>,
    /** Replaced charges that were a separate purchase after all: left as they are. */
    val keep: List<String> = emptyList(),
)

@Serializable
data class PhoneNotification(
    @SerialName("package") val packageName: String,
    val title: String,
    val text: String,
    @SerialName("posted_at") val postedAt: String,
    val key: String,
)

@Serializable
data class PhoneNotificationResult(val status: String)

@Serializable
data class MonthTotal(
    val month: String,
    val spent: Double,
    val expected: Double,
    @SerialName("by_line") val byLine: Map<String, Double> = emptyMap(),
)

@Serializable
data class ReviewLine(
    @SerialName("budget_line") val budgetLine: String,
    val spent: Double,
    @SerialName("monthly_limit") val monthlyLimit: Double? = null,
    val previous: Double,
    @SerialName("average_3m") val average3m: Double,
)

@Serializable
data class AccountCoverage(
    @SerialName("account_id") val accountId: String,
    val institution: String,
    @SerialName("account_name") val accountName: String,
    val transactions: Int,
    val spent: Double,
    @SerialName("statement_imported") val statementImported: Boolean,
    @SerialName("statement_period") val statementPeriod: String? = null,
)

@Serializable
data class MerchantTotal(val merchant: String, val spent: Double, val count: Int)

@Serializable
data class MonthReview(
    val month: String,
    @SerialName("total_spent") val totalSpent: Double,
    @SerialName("previous_total") val previousTotal: Double,
    @SerialName("average_3m") val average3m: Double,
    @SerialName("total_budget") val totalBudget: Double,
    val lines: List<ReviewLine>,
    @SerialName("needs_check") val needsCheck: List<Transaction>,
    val accounts: List<AccountCoverage>,
    @SerialName("top_merchants") val topMerchants: List<MerchantTotal>,
    @SerialName("unmatched_receipts") val unmatchedReceipts: Int,
)

@Serializable
data class CategoryInfo(
    val name: String,
    val transactions: Int,
    @SerialName("monthly_limit") val monthlyLimit: Double? = null,
)

@Serializable
data class CategoryNameBody(val name: String)

@Serializable
data class HealthCheck(val name: String, val status: String, val detail: String)

@Serializable
data class HealthReport(@SerialName("checked_at") val checkedAt: String, val checks: List<HealthCheck>)

/** One card at the top of the Budget screen; see app/services/insights.py on the server for the kinds. */
@Serializable
data class Insight(
    val key: String,
    val kind: String,
    val severity: String, // alert | warn | info
    val title: String,
    val body: String,
    val impact: Double,
    val action: String, // open_line | open_transaction | open_bills | track_recurring | remove_hold
    val line: String? = null,
    @SerialName("transaction_id") val transactionId: String? = null,
    // For track_recurring: the body to POST to /recurring.
    val recurring: Map<String, String>? = null,
)

@Serializable
data class DismissInsight(val key: String)

/** One account's balance at the end of a month (credit cards and loans: what is owed). */
@Serializable
data class AccountBalance(
    @SerialName("account_id") val accountId: String,
    @SerialName("account_name") val accountName: String,
    val kind: String,
    val owner: String,
    /** savings | investments | chequing | credit_cards | loans | other */
    val group: String,
    val balance: Double? = null,
    @SerialName("as_of") val asOf: String? = null,
    /** From an earlier month, carried forward. */
    val stale: Boolean = false,
    val source: String? = null,
    @SerialName("previous_as_of") val previousAsOf: String? = null,
    val change: Double? = null,
    val contributions: Double? = null,
    @SerialName("market_return") val marketReturn: Double? = null,
)

@Serializable
data class Balances(
    val month: String,
    @SerialName("as_of") val asOf: String,
    val assets: Double,
    val liabilities: Double,
    @SerialName("net_worth") val netWorth: Double,
    val change: Double? = null,
    val contributions: Double = 0.0,
    @SerialName("market_return") val marketReturn: Double = 0.0,
    val missing: Int = 0,
    val accounts: List<AccountBalance> = emptyList(),
)

/** A balance typed in; amount as a decimal string so cents stay exact. */
@Serializable
data class BalanceIn(@SerialName("as_of") val asOf: String, val balance: String)

@Serializable
data class BalanceReadRow(
    @SerialName("shown_name") val shownName: String,
    val balance: Double,
    val owed: Boolean,
    val currency: String,
    @SerialName("account_id") val accountId: String? = null,
)

/** What a screenshot showed; nothing is saved until confirmed. */
@Serializable
data class BalanceRead(@SerialName("as_of") val asOf: String, val rows: List<BalanceReadRow>)

@Serializable
data class BalanceBatchRow(@SerialName("account_id") val accountId: String, val balance: String)

@Serializable
data class BalanceBatch(@SerialName("as_of") val asOf: String, val rows: List<BalanceBatchRow>)

@Serializable
data class Me(val user: String)

// Chat assistant (private to whoever's token asks)

@Serializable
data class ChatAsk(
    val message: String,
    @SerialName("thread_id") val threadId: String? = null,
)

@Serializable
data class ChatAction(val tool: String, val summary: String)

@Serializable
data class ChatSource(val url: String, val title: String? = null)

@Serializable
data class ChatReply(
    @SerialName("thread_id") val threadId: String,
    @SerialName("message_id") val messageId: String,
    val text: String,
    val actions: List<ChatAction> = emptyList(),
    val sources: List<ChatSource> = emptyList(),
    val model: String,
    @SerialName("questions_left") val questionsLeft: Int? = null,
)

@Serializable
data class ChatThread(
    val id: String,
    val title: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class ChatMessage(
    val id: String,
    /** question | answer */
    val kind: String,
    val text: String? = null,
    val actions: List<ChatAction>? = null,
    val sources: List<ChatSource>? = null,
)

@Serializable
data class ChatThreadDetail(
    val id: String,
    val title: String,
    val messages: List<ChatMessage>,
    @SerialName("can_continue") val canContinue: Boolean,
)

@Serializable
data class ChatMemory(val id: String, val fact: String, @SerialName("created_at") val createdAt: String)

@Serializable
data class ChatMemoryIn(val fact: String)

@Serializable
data class ChatUsage(
    val used: Int,
    val limit: Int? = null,
    val left: Int? = null,
    val available: Boolean,
    /** Answers can draw on the web (Claude only). */
    @SerialName("web_search") val webSearch: Boolean = false,
)

// Savings target and goals

@Serializable
data class SavingsTarget(
    @SerialName("monthly_amount") val monthlyAmount: Double,
    @SerialName("alert_day") val alertDay: Int = 25,
    @SerialName("warning_percent") val warningPercent: Double = 80.0,
)

@Serializable
data class SavingsTargetIn(
    @SerialName("monthly_amount") val monthlyAmount: String,
    @SerialName("alert_day") val alertDay: Int,
    @SerialName("warning_percent") val warningPercent: Double,
)

@Serializable
data class TargetProgress(
    val month: String,
    val target: Double,
    val saved: Double,
    val kept: Double,
    val percent: Double,
    val remaining: Double,
    /** met | on_track | behind */
    val status: String,
)

@Serializable
data class GoalAccount(
    @SerialName("account_id") val accountId: String,
    @SerialName("account_name") val accountName: String,
    val owner: String,
    val balance: Double? = null,
    @SerialName("as_of") val asOf: String? = null,
)

@Serializable
data class Goal(
    val id: String,
    val name: String,
    @SerialName("target_amount") val targetAmount: Double,
    @SerialName("target_date") val targetDate: String,
    @SerialName("account_kinds") val accountKinds: List<String> = emptyList(),
    @SerialName("account_ids") val accountIds: List<String> = emptyList(),
    val active: Boolean = true,
    val current: Double,
    val remaining: Double,
    val percent: Double,
    @SerialName("months_left") val monthsLeft: Int,
    /** What would reach the goal in the months left; null once reached or past the date. */
    @SerialName("required_monthly") val requiredMonthly: Double? = null,
    @SerialName("moved_this_month") val movedThisMonth: Double = 0.0,
    @SerialName("oldest_balance") val oldestBalance: String? = null,
    val accounts: List<GoalAccount> = emptyList(),
)

@Serializable
data class SavingsPlan(
    val target: SavingsTarget? = null,
    val progress: TargetProgress? = null,
    val goals: List<Goal> = emptyList(),
)

/** A new goal, or the fields to change (null: unchanged). Amount as a decimal string. */
@Serializable
data class GoalIn(
    val name: String? = null,
    @SerialName("target_amount") val targetAmount: String? = null,
    @SerialName("target_date") val targetDate: String? = null,
    @SerialName("account_kinds") val accountKinds: List<String>? = null,
    @SerialName("account_ids") val accountIds: List<String>? = null,
)
