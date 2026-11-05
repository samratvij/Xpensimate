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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.ExpectedPayment
import com.xpensimate.app.data.MarkPaid
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter

class BillsViewModel(private val api: Api) : ViewModel() {
    var month by mutableStateOf(YearMonth.now())
        private set
    var expected by mutableStateOf<Load<List<ExpectedPayment>>>(Load.Loading)
        private set
    var refreshing by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)

    init { load() }

    fun selectMonth(value: YearMonth) { month = value; expected = Load.Loading; load() }

    fun load(pull: Boolean = false) {
        viewModelScope.launch {
            if (pull) refreshing = true
            expected = runCatching { api.expected(month.toString()) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            refreshing = false
        }
    }

    /** Records an off-card payment (rent by e-transfer, a transfer to parents) as a transaction. */
    fun markPaid(item: ExpectedPayment, amount: Double) {
        viewModelScope.launch {
            error = null
            // Paid late still counts in the month it was due; paying ahead is recorded today.
            val paidOn = paidDate(item)
            runCatching { api.markPaid(item.recurring.id, MarkPaid(paidOn = paidOn.toString(), amount = amount)) }
                .onSuccess { load() }
                .onFailure { error = it.friendly() }
        }
    }
}

private val dueFormat = DateTimeFormatter.ofPattern("EEE, MMM d")

private fun paidDate(item: ExpectedPayment): LocalDate = minOf(LocalDate.parse(item.due), LocalDate.now())

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BillsScreen(vm: BillsViewModel) {
    var paying by remember { mutableStateOf<ExpectedPayment?>(null) }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp)) }
        when (val state = vm.expected) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                val payments = state.value
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                    item {
                        val due = payments.filter { it.status != "paid" }.sumOf { it.recurring.amount }
                        val paid = payments.filter { it.status == "paid" }.sumOf { it.recurring.amount }
                        Card(Modifier.fillMaxWidth().padding(16.dp)) {
                            Row(Modifier.padding(16.dp)) {
                                Column(Modifier.weight(1f)) {
                                    Text("Still due", style = MaterialTheme.typography.labelMedium)
                                    Text(due.money(), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                                }
                                Column(Modifier.weight(1f)) {
                                    Text("Paid", style = MaterialTheme.typography.labelMedium)
                                    Text(paid.money(), style = MaterialTheme.typography.titleLarge)
                                }
                            }
                        }
                    }
                    if (payments.isEmpty()) item { Text("No recurring bills this month.", Modifier.padding(24.dp)) }
                    items(payments, key = { "${it.recurring.id}-${it.due}" }) { payment ->
                        BillRow(payment) { paying = payment }
                        HorizontalDivider()
                    }
                }
            }
        }
    }
    paying?.let { payment ->
        var amount by remember(payment) { mutableStateOf("%.2f".format(payment.recurring.amount)) }
        AlertDialog(
            onDismissRequest = { paying = null },
            title = { Text("Mark ${payment.recurring.name} paid?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Records a ${payment.budgetLine} payment on ${paidDate(payment).format(dueFormat)}. " +
                        "Use this for bills that don't come through a card.")
                    OutlinedTextField(value = amount, onValueChange = { amount = it }, label = { Text("Amount") }, singleLine = true)
                }
            },
            confirmButton = {
                TextButton(
                    enabled = amount.toDoubleOrNull()?.let { it > 0 } == true,
                    onClick = { vm.markPaid(payment, amount.toDouble()); paying = null },
                ) { Text("Mark paid") }
            },
            dismissButton = { TextButton(onClick = { paying = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun BillRow(payment: ExpectedPayment, onMarkPaid: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(payment.recurring.name, style = MaterialTheme.typography.bodyLarge)
            Text(
                "${LocalDate.parse(payment.due).format(dueFormat)} · ${payment.budgetLine}",
                style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(payment.recurring.amount.money(), fontWeight = FontWeight.SemiBold)
            when (payment.status) {
                "paid" -> Text("✓ Paid", style = MaterialTheme.typography.labelMedium, color = colors.primary)
                else -> TextButton(onClick = onMarkPaid) {
                    Text(if (payment.status == "overdue") "Overdue · mark paid" else "Mark paid",
                        color = if (payment.status == "overdue") colors.error else colors.primary)
                }
            }
        }
    }
}
