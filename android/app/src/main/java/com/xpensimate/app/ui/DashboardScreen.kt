package com.xpensimate.app.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.RowScope
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.Balances
import com.xpensimate.app.data.SavingsPlan
import com.xpensimate.app.data.DismissInsight
import com.xpensimate.app.data.EARNERS
import com.xpensimate.app.data.Insight
import com.xpensimate.app.data.LineSpend
import com.xpensimate.app.data.MonthTotal
import com.xpensimate.app.data.Summary
import com.xpensimate.app.data.TransactionPatch
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import java.time.YearMonth
import kotlin.math.abs
import kotlin.math.roundToInt

private const val TREND_MONTHS = 6

class DashboardViewModel(private val api: Api) : ViewModel() {
    var month by mutableStateOf(YearMonth.now())
        private set
    var purpose by mutableStateOf<String?>(null)
        private set
    var purposes by mutableStateOf<List<String>>(emptyList())
        private set
    var summary by mutableStateOf<Load<Summary>>(Load.Loading)
        private set
    var trend by mutableStateOf<List<MonthTotal>>(emptyList())
        private set
    /** Net worth (household view only). */
    var balances by mutableStateOf<Balances?>(null)
        private set
    /** The savings target and goals (household view of the current month only). */
    var plan by mutableStateOf<SavingsPlan?>(null)
        private set
    // Insights are about the current month only; empty while another month is shown.
    var insights by mutableStateOf<List<Insight>>(emptyList())
        private set
    var message by mutableStateOf<String?>(null)
    var refreshing by mutableStateOf(false)
        private set

    init {
        viewModelScope.launch { runCatching { api.taxonomy() }.onSuccess { purposes = it.purposes } }
    }

    fun selectMonth(value: YearMonth) { month = value; load() }
    fun selectPurpose(value: String?) { purpose = value; load() }

    fun load(pull: Boolean = false) {
        viewModelScope.launch {
            if (pull) refreshing = true else if (summary !is Load.Ok) summary = Load.Loading
            val trendJob = async { runCatching { api.trends(TREND_MONTHS, purpose) }.getOrNull() }
            val balancesJob = async { if (purpose == null) runCatching { api.balances(month.toString()) }.getOrNull() else null }
            val planJob = async {
                if (purpose == null && month == YearMonth.now()) runCatching { api.savingsPlan() }.getOrNull() else null
            }
            val insightsJob = async {
                if (month == YearMonth.now()) runCatching { api.insights(purpose) }.getOrNull() else emptyList()
            }
            summary = runCatching { api.summary(month.toString(), purpose) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            trendJob.await()?.let { trend = it }
            balances = balancesJob.await()
            plan = planJob.await()
            insightsJob.await()?.let { insights = it }
            refreshing = false
        }
    }

    fun dismiss(insight: Insight) {
        insights = insights - insight
        viewModelScope.launch {
            runCatching { api.dismissInsight(DismissInsight(insight.key)) }.onFailure { message = it.friendly(); load() }
        }
    }

    /** The first charge of an order that changed was only a hold: mark it not a transaction. */
    fun removeHold(insight: Insight) {
        val id = insight.transactionId ?: return
        insights = insights - insight
        viewModelScope.launch {
            runCatching { api.updateTransaction(id, TransactionPatch(excluded = true)) }
                .onSuccess { message = "Marked ${it.merchantNormalized ?: it.merchantRaw} ${it.amount.money()} as not a transaction"; load() }
                .onFailure { message = it.friendly(); load() }
        }
    }

    /** Add a repeating charge the server spotted as a bill, so it's expected (and watched) from now on. */
    fun track(insight: Insight) {
        val body = insight.recurring ?: return
        viewModelScope.launch {
            runCatching { api.createRecurring(body) }
                .onSuccess { message = "Added ${it.name} to Bills"; load() }
                .onFailure { message = it.friendly() }
        }
    }
}

/** Where an insight card leads. */
class InsightNav(
    val openLine: (String) -> Unit,
    val openTransaction: (String) -> Unit,
    val openBills: () -> Unit,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(
    vm: DashboardViewModel,
    onOpenTransaction: (String) -> Unit,
    onOpenBills: () -> Unit,
    onOpenLine: (line: String, month: YearMonth, purpose: String?) -> Unit,
    onOpenIncome: (month: YearMonth) -> Unit = {},
    onOpenBalances: (month: YearMonth) -> Unit = {},
    onOpenSavings: () -> Unit = {},
) {
    // Reload when coming back from an edit screen (budgets, a transaction), keeping what's shown meanwhile.
    LaunchedEffect(Unit) { vm.load() }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.summary) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                val openLine = { line: String -> onOpenLine(line, vm.month, vm.purpose) }
                DashboardContent(vm, state.value, InsightNav(openLine, onOpenTransaction, onOpenBills), openLine,
                    onOpenIncome = { onOpenIncome(vm.month) }, onOpenBalances = { onOpenBalances(vm.month) },
                    onOpenSavings = onOpenSavings)
            }
        }
    }
}

@Composable
private fun DashboardContent(
    vm: DashboardViewModel, summary: Summary, nav: InsightNav, onOpenLine: (String) -> Unit, onOpenIncome: () -> Unit,
    onOpenBalances: () -> Unit,
    onOpenSavings: () -> Unit,
) {
    // Budgeted lines first (closest to their limit on top), then everything else by size.
    val (budgeted, other) = summary.categories.partition { it.monthlyLimit != null }
    val budgetedSorted = budgeted.sortedByDescending { it.projected / it.monthlyLimit!! }
    val otherSorted = other.filter { it.spent > 0 || it.expected > 0 }.sortedByDescending { it.projected }
    val previous = vm.trend.firstOrNull { YearMonth.parse(it.month) == vm.month.minusMonths(1) }?.spent
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        vm.message?.let { text ->
            item { Text(text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 4.dp)) }
        }
        item(key = "hero") { HeroCard(summary, vm.month, previous) }
        if (vm.insights.isNotEmpty()) item(key = "insights") {
            InsightsSection(
                vm.insights,
                onOpen = { insight ->
                    when (insight.action) {
                        "open_line" -> insight.line?.let(nav.openLine)
                        "open_transaction", "remove_hold" -> insight.transactionId?.let(nav.openTransaction)
                        "open_bills" -> nav.openBills()
                    }
                },
                onTrack = vm::track,
                onRemoveHold = vm::removeHold,
                onDismiss = vm::dismiss,
            )
        }
        item(key = "money") {
            MoneyStrip(
                summary, vm.balances,
                showIncome = vm.purpose == null || vm.purpose in EARNERS,
                current = vm.month == YearMonth.now(),
                onOpenIncome = onOpenIncome, onOpenSavings = onOpenSavings, onOpenBalances = onOpenBalances,
            )
        }
        item(key = "who") {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = vm.purpose == null, onClick = { vm.selectPurpose(null) }, label = { Text("Everyone") })
                vm.purposes.forEach { who ->
                    FilterChip(selected = vm.purpose == who, onClick = { vm.selectPurpose(who) }, label = { Text(who) })
                }
            }
        }
        if (budgetedSorted.isEmpty() && otherSorted.isEmpty()) item { Text("No spending this month yet.", Modifier.padding(8.dp)) }
        if (budgetedSorted.isNotEmpty()) item(key = "budgeted") { LineGroup(null, budgetedSorted, onOpenLine) }
        if (otherSorted.isNotEmpty()) item(key = "other") { LineGroup("Not budgeted", otherSorted, onOpenLine) }
        if (vm.trend.isNotEmpty()) item(key = "trend") {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Last $TREND_MONTHS months", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.size(12.dp))
                    MonthlyBars(vm.trend, vm.month, vm::selectMonth)
                }
            }
        }
    }
}

/** The month in one glance: spent against the total budget, and one line on where it's heading. */
@Composable
private fun HeroCard(summary: Summary, month: YearMonth, previous: Double?) {
    val projected = summary.totalSpent + summary.totalExpected
    val budget = summary.categories.sumOf { it.monthlyLimit ?: 0.0 }
    val current = month == YearMonth.now()
    val onColor = MaterialTheme.colorScheme.onPrimaryContainer
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(summary.totalSpent.wholeMoney(), style = MaterialTheme.typography.displaySmall,
                    fontWeight = FontWeight.SemiBold, color = onColor)
                Text(if (budget > 0) "  of ${budget.wholeMoney()}" else "  spent", style = MaterialTheme.typography.titleMedium,
                    color = onColor.copy(alpha = 0.75f), modifier = Modifier.padding(bottom = 6.dp))
            }
            if (budget > 0) BudgetProgress(summary.totalSpent, projected, budget, over = summary.totalSpent > budget,
                warn = projected > budget)
            // Mid-month, compare where the month is heading, not a few days against a whole month.
            val compared = if (current) projected else summary.totalSpent
            val parts = listOfNotNull(
                if (current && summary.totalExpected > 0) "Heading for ${projected.wholeMoney()}" else null,
                previous?.takeIf { it > 0 }?.let { prev ->
                    val change = ((compared - prev) / prev * 100).roundToInt()
                    when {
                        change == 0 -> "same as last month"
                        change > 0 -> "$change% more than last month"
                        else -> "${-change}% less than last month"
                    }
                },
            )
            if (parts.isNotEmpty()) Text(parts.joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = onColor)
        }
    }
}

/** Income, savings and net worth as three small figures; each opens its own screen for the detail. */
@Composable
private fun MoneyStrip(
    summary: Summary, balances: Balances?, showIncome: Boolean, current: Boolean,
    onOpenIncome: () -> Unit, onOpenSavings: () -> Unit, onOpenBalances: () -> Unit,
) {
    val savings = summary.savings?.takeIf { it.target != null }
    if (!showIncome && savings == null && balances == null) return
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            if (showIncome) {
                val income = summary.income
                MiniStat("Income", income.received.wholeMoney(),
                    if (current && income.expected > 0) "+${income.expected.wholeMoney()} due" else null, onOpenIncome)
            }
            savings?.let { sav ->
                MiniStat("Saved", sav.moved.wholeMoney(), "of ${sav.target!!.wholeMoney()}", onOpenSavings,
                    subColor = if (sav.targetStatus == "behind") ChartColors.warning else null)
            }
            balances?.let { worth ->
                MiniStat("Net worth", (if (worth.netWorth < 0) "−" else "") + abs(worth.netWorth).wholeMoney(),
                    worth.change?.let { (if (it < 0) "−" else "+") + abs(it).wholeMoney() }, onOpenBalances)
            }
        }
    }
}

@Composable
private fun RowScope.MiniStat(label: String, value: String, sub: String?, onClick: () -> Unit, subColor: Color? = null) {
    Column(Modifier.weight(1f).clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, maxLines = 1)
        Text(sub ?: " ", style = MaterialTheme.typography.labelSmall, maxLines = 1,
            color = subColor ?: MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Budget lines as a tight list in one card; the line screen holds the detail. */
@Composable
private fun LineGroup(title: String?, lines: List<LineSpend>, onOpenLine: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        title?.let {
            Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 4.dp, top = 4.dp))
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(vertical = 4.dp)) {
                lines.forEachIndexed { i, line ->
                    if (i > 0) HorizontalDivider(Modifier.padding(start = 60.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.4f))
                    LineRow(line) { onOpenLine(line.category) }
                }
            }
        }
    }
}

@Composable
private fun LineRow(line: LineSpend, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CategoryBadge(line.category, size = 32.dp)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(line.category, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                when {
                    line.overLimit -> Icon(Icons.Filled.Error, "Over budget", tint = ChartColors.critical, modifier = Modifier.size(16.dp))
                    line.projectedOverLimit -> Icon(Icons.Filled.Warning, "On track to go over", tint = ChartColors.warning,
                        modifier = Modifier.size(16.dp))
                    line.overWarning -> Icon(Icons.Filled.Warning, "Nearing the budget", tint = ChartColors.warning,
                        modifier = Modifier.size(16.dp))
                }
                Text(
                    buildAnnotatedString {
                        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(line.spent.wholeMoney()) }
                        line.monthlyLimit?.let { withStyle(SpanStyle(color = colors.onSurfaceVariant)) { append(" / ${it.wholeMoney()}") } }
                    },
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            line.monthlyLimit?.let { limit ->
                BudgetProgress(line.spent, line.projected, limit, over = line.overLimit, warn = line.overWarning || line.projectedOverLimit)
            }
        }
    }
}

/** What a line still expects this month: bills not yet paid, and the rest of the usual spend on a necessity. */
internal fun LineSpend.stillToCome(): List<String> = buildList {
    if (billsDue > 0) add("+${billsDue.money()} bills due")
    if (estimated > 0) add("~${estimated.money()} more at the usual pace")
}
