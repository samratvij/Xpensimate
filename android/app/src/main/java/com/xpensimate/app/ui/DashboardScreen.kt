package com.xpensimate.app.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.TrendingDown
import androidx.compose.material.icons.automirrored.filled.TrendingUp
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
import com.xpensimate.app.data.LineSpend
import com.xpensimate.app.data.MonthTotal
import com.xpensimate.app.data.Summary
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
            summary = runCatching { api.summary(month.toString(), purpose) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            trendJob.await()?.let { trend = it }
            refreshing = false
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(vm: DashboardViewModel, onOpenLine: (line: String, month: YearMonth, purpose: String?) -> Unit) {
    // Reload when coming back from an edit screen (budgets, a transaction), keeping what's shown meanwhile.
    LaunchedEffect(Unit) { vm.load() }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.summary) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                DashboardContent(vm, state.value) { onOpenLine(it, vm.month, vm.purpose) }
            }
        }
    }
}

@Composable
private fun DashboardContent(vm: DashboardViewModel, summary: Summary, onOpenLine: (String) -> Unit) {
    // Budgeted lines first (closest to their limit on top), then everything else by size.
    val lines = summary.categories.sortedWith(
        compareByDescending<LineSpend> { it.monthlyLimit != null }
            .thenByDescending { line -> line.monthlyLimit?.let { line.projected / it } ?: 0.0 }
            .thenByDescending { it.projected }
    )
    val previous = vm.trend.firstOrNull { YearMonth.parse(it.month) == vm.month.minusMonths(1) }?.spent
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { HeroCard(summary, vm.month, previous) }
        if (vm.trend.isNotEmpty()) item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Last $TREND_MONTHS months", style = MaterialTheme.typography.titleSmall)
                    val shown = vm.trend.firstOrNull { YearMonth.parse(it.month) == vm.month }
                    Text(
                        shown?.let { m ->
                            "${vm.month.title()}: ${m.spent.money()} spent" +
                                if (m.expected > 0) " + ${m.expected.money()} bills due" else ""
                        } ?: "Tap a month to open it",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.size(12.dp))
                    MonthlyBars(vm.trend, vm.month, vm::selectMonth)
                }
            }
        }
        item {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = vm.purpose == null, onClick = { vm.selectPurpose(null) }, label = { Text("Everyone") })
                vm.purposes.forEach { who ->
                    FilterChip(selected = vm.purpose == who, onClick = { vm.selectPurpose(who) }, label = { Text(who) })
                }
            }
        }
        if (lines.isEmpty()) item { Text("No spending this month yet.", Modifier.padding(8.dp)) }
        items(lines, key = { it.category }) { LineCard(it) { onOpenLine(it.category) } }
    }
}

@Composable
private fun HeroCard(summary: Summary, month: YearMonth, previous: Double?) {
    val projected = summary.totalSpent + summary.totalExpected
    val budget = summary.categories.sumOf { it.monthlyLimit ?: 0.0 }
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(if (month == YearMonth.now()) "Spent so far this month" else "Spent in ${month.title()}",
                style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onPrimaryContainer)
            Row(verticalAlignment = Alignment.Bottom) {
                Text(summary.totalSpent.money(), style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onPrimaryContainer)
                if (budget > 0) Text("  of ${budget.money()}", style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.padding(bottom = 6.dp))
            }
            if (budget > 0) BudgetProgress(summary.totalSpent, projected, budget, over = summary.totalSpent > budget,
                warn = projected > budget)
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                if (summary.totalExpected > 0) HeroStat("Bills still due", summary.totalExpected.money())
                if (summary.totalExpected > 0) HeroStat("Projected", projected.money())
                previous?.takeIf { it > 0 }?.let { prev ->
                    // Mid-month, compare where the month is heading, not a few days against a whole month.
                    val current = month == YearMonth.now()
                    val change = ((if (current) projected else summary.totalSpent) - prev) / prev * 100
                    Column {
                        Text(if (current) "Projected vs last month" else "vs last month", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(if (change >= 0) Icons.AutoMirrored.Filled.TrendingUp else Icons.AutoMirrored.Filled.TrendingDown,
                                contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                            Spacer(Modifier.width(4.dp))
                            Text("${if (change >= 0) "+" else "−"}${abs(change).roundToInt()}%", style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.onPrimaryContainer)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HeroStat(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
        Text(value, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
    }
}

@Composable
private fun LineCard(line: LineSpend, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                CategoryBadge(line.category)
                Column(Modifier.weight(1f)) {
                    Text(line.category, style = MaterialTheme.typography.titleSmall)
                    val notes = buildList {
                        if (line.expected > 0) add("+${line.expected.money()} bills due")
                        line.monthlyLimit?.let { add("${(line.spent / it * 100).roundToInt()}% of ${it.money()}") }
                    }
                    if (notes.isNotEmpty()) Text(notes.joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                        color = colors.onSurfaceVariant)
                }
                Text(line.spent.money(), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            }
            line.monthlyLimit?.let { limit ->
                BudgetProgress(line.spent, line.projected, limit, over = line.overLimit, warn = line.overWarning || line.projectedOverLimit)
                when {
                    line.overLimit -> StatusNote(Icons.Filled.Error, ChartColors.critical, "Over budget by ${(line.spent - limit).money()}")
                    line.projectedOverLimit -> StatusNote(Icons.Filled.Warning, ChartColors.warning,
                        "On track to go over with bills still due")
                    line.overWarning -> StatusNote(Icons.Filled.Warning, ChartColors.warning, "Nearing the budget")
                }
            }
        }
    }
}

@Composable
private fun StatusNote(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: androidx.compose.ui.graphics.Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(16.dp))
        Text(text, style = MaterialTheme.typography.bodySmall)
    }
}
