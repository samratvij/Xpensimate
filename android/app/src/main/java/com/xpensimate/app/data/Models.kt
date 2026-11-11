package com.xpensimate.app.data

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

    /** A guess the user should glance at: picked by Claude (or nothing) with low confidence, never confirmed. */
    val needsCheck: Boolean
        get() = !excluded && !categoryIsManual && !purposeIsManual &&
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
    val expected: Double,
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
    val categories: List<LineSpend>,
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
)

@Serializable
data class Account(
    val id: String,
    val institution: String,
    @SerialName("account_name") val accountName: String,
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
