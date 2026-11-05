package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import com.xpensimate.app.data.ExpectedPayment
import com.xpensimate.app.data.LineSpend
import com.xpensimate.app.data.Transaction
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter

data class LineDetail(val spend: LineSpend?, val transactions: List<Transaction>, val due: List<ExpectedPayment>)

class LineDetailViewModel(
    private val api: Api,
    val line: String,
    val month: YearMonth,
    private val purpose: String?,
) : ViewModel() {
    var detail by mutableStateOf<Load<LineDetail>>(Load.Loading)
        private set

    fun load() {
        viewModelScope.launch {
            detail = runCatching {
                val txns = async { api.transactions(month.startUtc(), month.endUtc(), purpose, budgetLine = line, limit = 500) }
                val summary = async { api.summary(month.toString(), purpose) }
                val due = async { api.expected(month.toString()) }
                LineDetail(
                    spend = summary.await().categories.firstOrNull { it.category == line },
                    transactions = txns.await(),
                    due = due.await().filter {
                        it.budgetLine == line && it.status != "paid" && (purpose == null || it.recurring.purpose == purpose)
                    },
                )
            }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }
}

private val dueDay = DateTimeFormatter.ofPattern("EEE, MMM d")

@Composable
fun LineDetailScreen(vm: LineDetailViewModel, onOpenTransaction: (String) -> Unit) {
    // Reload on return from a transaction, which may have moved to another line.
    LaunchedEffect(Unit) { vm.load() }
    when (val state = vm.detail) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> {
            val d = state.value
            val byDay = d.transactions.groupBy { LocalDate.ofInstant(parseInstant(it.timestamp), zone) }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                item { LineHeader(vm.line, vm.month, d.spend, d.transactions.size) }
                if (d.due.isNotEmpty()) {
                    item {
                        Text("Bills still due", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(start = 16.dp, top = 16.dp, bottom = 4.dp))
                    }
                    items(d.due, key = { "due-${it.recurring.id}-${it.due}" }) { p ->
                        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(p.recurring.name)
                                Text("${LocalDate.parse(p.due).format(dueDay)} · ${if (p.status == "overdue") "overdue" else "due"}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (p.status == "overdue") ChartColors.critical else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text(p.recurring.amount.money(), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                if (d.transactions.isEmpty()) {
                    item { Text("Nothing spent here in ${vm.month.title()}.", Modifier.padding(24.dp)) }
                }
                byDay.forEach { (day, txns) ->
                    item(key = day.toString()) {
                        Text(day.format(dayHeader), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(start = 16.dp, top = 16.dp, bottom = 4.dp))
                    }
                    items(txns, key = { it.id }) { txn -> TransactionRow(txn) { onOpenTransaction(txn.id) } }
                }
            }
        }
    }
}

@Composable
private fun LineHeader(line: String, month: YearMonth, spend: LineSpend?, count: Int) {
    Card(Modifier.fillMaxWidth().padding(16.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                CategoryBadge(line, 48.dp)
                Column {
                    Text(line, style = MaterialTheme.typography.titleLarge)
                    Text("${month.title()} · $count charge${if (count == 1) "" else "s"}", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Row(verticalAlignment = Alignment.Bottom) {
                Text((spend?.spent ?: 0.0).money(), style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
                spend?.monthlyLimit?.let {
                    Text("  of ${it.money()}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 4.dp))
                }
            }
            val limit = spend?.monthlyLimit
            if (spend != null && limit != null) {
                BudgetProgress(spend.spent, spend.projected, limit, over = spend.overLimit,
                    warn = spend.overWarning || spend.projectedOverLimit)
            }
            if ((spend?.expected ?: 0.0) > 0) {
                Text("+${spend!!.expected.money()} in bills still due · ${spend.projected.money()} projected",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
