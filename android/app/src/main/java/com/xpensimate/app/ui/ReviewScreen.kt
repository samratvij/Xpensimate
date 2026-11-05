package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.TrendingDown
import androidx.compose.material.icons.automirrored.filled.TrendingUp
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import com.xpensimate.app.data.MonthReview
import com.xpensimate.app.data.ReviewLine
import kotlinx.coroutines.launch
import java.time.YearMonth
import kotlin.math.abs
import kotlin.math.roundToInt

class ReviewViewModel(private val api: Api) : ViewModel() {
    // Month-end review defaults to the month that just ended.
    var month by mutableStateOf(YearMonth.now().minusMonths(1))
        private set
    var review by mutableStateOf<Load<MonthReview>>(Load.Loading)
        private set

    fun selectMonth(value: YearMonth) { month = value; review = Load.Loading; load() }

    fun load() {
        viewModelScope.launch {
            review = runCatching { api.review(month.toString()) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }
}

@Composable
fun ReviewScreen(vm: ReviewViewModel, onOpenTransaction: (String) -> Unit, onImport: () -> Unit) {
    LaunchedEffect(Unit) { vm.load() }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.review) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> ReviewContent(state.value, onOpenTransaction, onImport)
        }
    }
}

@Composable
private fun ReviewContent(r: MonthReview, onOpenTransaction: (String) -> Unit, onImport: () -> Unit) {
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        item { SummaryCard(r) }

        if (r.needsCheck.isNotEmpty()) item {
            SectionCard("Needs a look (${r.needsCheck.size})", "Guesses Claude wasn't sure about. Tap to confirm or fix.") {
                r.needsCheck.forEach { t ->
                    Row(Modifier.fillMaxWidth().clickable { onOpenTransaction(t.id) }.padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        CategoryBadge(t.budgetLine, 32.dp)
                        Column(Modifier.weight(1f)) {
                            Text(t.merchant, maxLines = 1)
                            Text("Guessed ${t.budgetLine} · ${(t.inferenceConfidence * 100).roundToInt()}% sure",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text(t.amount.money(), fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        item {
            val spending = r.lines.filter { it.spent != 0.0 || it.monthlyLimit != null }
            val scale = spending.maxOfOrNull { maxOf(it.spent, it.monthlyLimit ?: 0.0) } ?: 1.0
            SectionCard("Where it went", "Bars against each budget (the tick). Change is vs your 3-month average.") {
                spending.filter { it.spent > 0 }.forEach { LineRow(it, scale) }
                val unused = spending.filter { it.spent <= 0 && it.monthlyLimit != null }
                if (unused.isNotEmpty()) {
                    Text("Nothing spent: " + unused.joinToString { it.budgetLine }, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
                }
            }
        }

        if (r.topMerchants.isNotEmpty()) item {
            SectionCard("Top merchants", null) {
                r.topMerchants.forEachIndexed { i, m ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("${i + 1}", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.width(24.dp))
                        Text(m.merchant, Modifier.weight(1f), maxLines = 1)
                        Text("${m.count}×  ", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(m.spent.money(), fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        item {
            SectionCard("Statements", "Import each card's statement to catch anything the alerts missed.") {
                r.accounts.forEach { a ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Icon(
                            if (a.statementImported) Icons.Filled.CheckCircle else Icons.Filled.ErrorOutline,
                            contentDescription = null,
                            tint = if (a.statementImported) ChartColors.good else ChartColors.warning,
                        )
                        Column(Modifier.weight(1f)) {
                            Text(a.accountName)
                            Text(
                                (if (a.statementImported) "Statement imported" else "No statement imported yet") +
                                    " · ${a.transactions} charges",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(a.spent.money())
                    }
                }
                if (r.accounts.any { !it.statementImported }) TextButton(onClick = onImport) { Text("Import a statement") }
                if (r.unmatchedReceipts > 0) Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Icon(Icons.Filled.ReceiptLong, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("${r.unmatchedReceipts} receipt(s) still waiting for their card charge", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun SummaryCard(r: MonthReview) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Spent in ${YearMonth.parse(r.month).title()}", style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(r.totalSpent.money(), style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.SemiBold)
            if (r.totalBudget > 0) {
                BudgetProgress(r.totalSpent, r.totalSpent, r.totalBudget, over = r.totalSpent > r.totalBudget,
                    warn = r.totalSpent > r.totalBudget * 0.8)
                Text(
                    if (r.totalSpent <= r.totalBudget) "${(r.totalBudget - r.totalSpent).money()} under the ${r.totalBudget.money()} budget"
                    else "${(r.totalSpent - r.totalBudget).money()} over the ${r.totalBudget.money()} budget",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                Comparison("vs last month", r.totalSpent, r.previousTotal)
                Comparison("vs 3-month average", r.totalSpent, r.average3m)
            }
        }
    }
}

/** A change as an arrow + percentage in text colors (spending more isn't a "status", just a direction). */
@Composable
private fun Comparison(label: String, now: Double, before: Double) {
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (before <= 0) {
            Text("—", style = MaterialTheme.typography.titleMedium)
        } else {
            val change = (now - before) / before * 100
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(if (change >= 0) Icons.AutoMirrored.Filled.TrendingUp else Icons.AutoMirrored.Filled.TrendingDown,
                    contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("${if (change >= 0) "+" else "−"}${abs(change).roundToInt()}%", style = MaterialTheme.typography.titleMedium)
            }
        }
    }
}

@Composable
private fun LineRow(line: ReviewLine, scale: Double) {
    val over = line.monthlyLimit != null && line.spent > line.monthlyLimit
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            CategoryBadge(line.budgetLine, 32.dp)
            Column(Modifier.weight(1f)) {
                Text(line.budgetLine)
                val usual = if (line.average3m > 0) {
                    val change = (line.spent - line.average3m) / line.average3m * 100
                    "${if (change >= 0) "+" else "−"}${abs(change).roundToInt()}% vs usual ${line.average3m.money()}"
                } else "new this month"
                Text(usual + if (over) " · over budget" else "", style = MaterialTheme.typography.bodySmall,
                    color = if (over) ChartColors.critical else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(line.spent.money(), fontWeight = FontWeight.SemiBold)
                line.monthlyLimit?.let { Text("of ${it.money()}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
        ValueBar(line.spent, scale, line.monthlyLimit)
    }
    HorizontalDivider()
}

@Composable
fun SectionCard(title: String, subtitle: String?, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            Spacer(Modifier.size(8.dp))
            content()
        }
    }
}
