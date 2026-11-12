package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.EARNERS
import com.xpensimate.app.data.INCOME_KINDS
import com.xpensimate.app.data.IncomeIn
import com.xpensimate.app.data.IncomeMonth
import com.xpensimate.app.data.IncomeTotals
import com.xpensimate.app.data.PayDate
import com.xpensimate.app.data.SavingsTotals
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import kotlin.math.abs

class IncomeViewModel(private val api: Api, start: YearMonth) : ViewModel() {
    var month by mutableStateOf(start)
        private set
    var income by mutableStateOf<Load<IncomeMonth>>(Load.Loading)
        private set
    var saving by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)

    fun selectMonth(value: YearMonth) { month = value; load() }

    fun load() {
        viewModelScope.launch {
            if (income !is Load.Ok) income = Load.Loading
            income = runCatching { api.income(month.toString()) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun add(body: IncomeIn, onDone: () -> Unit) {
        viewModelScope.launch {
            saving = true
            error = null
            runCatching { api.addIncome(body) }
                .onSuccess { onDone(); load() }
                .onFailure { error = it.friendly() }
            saving = false
        }
    }
}

private val payDay = DateTimeFormatter.ofPattern("EEE, MMM d")

@Composable
fun IncomeScreen(vm: IncomeViewModel, onOpenTransaction: (String) -> Unit) {
    LaunchedEffect(Unit) { vm.load() }
    var adding by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.income) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> {
                val inc = state.value
                LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(16.dp)) {
                                Column(Modifier.weight(1f)) {
                                    Text("Received", style = MaterialTheme.typography.labelMedium)
                                    Text("+${inc.received.money()}", style = MaterialTheme.typography.titleLarge,
                                        fontWeight = FontWeight.SemiBold, color = ChartColors.good)
                                }
                                Column(Modifier.weight(1f)) {
                                    Text("Still expected", style = MaterialTheme.typography.labelMedium)
                                    Text(inc.expected.money(), style = MaterialTheme.typography.titleLarge)
                                }
                            }
                            inc.byPerson.forEach { (who, amount) ->
                                HorizontalDivider()
                                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
                                    Text(who, Modifier.weight(1f))
                                    Text(amount.money(), fontWeight = FontWeight.SemiBold)
                                }
                            }
                            if (inc.received > 0) {
                                HorizontalDivider()
                                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
                                    Text("Left after spending", Modifier.weight(1f))
                                    val left = inc.leftAfterSpending
                                    Text((if (left < 0) "−" else "") + abs(left).money(), fontWeight = FontWeight.SemiBold,
                                        color = if (left < 0) ChartColors.critical else MaterialTheme.colorScheme.onSurface)
                                }
                            }
                        }
                    }
                    if (inc.payDates.isNotEmpty()) {
                        item { Text("Pay dates", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }
                        items(inc.payDates, key = { "${it.sourceId}${it.day}" }) { PayDateRow(it) }
                    }
                    item { Text("Deposits", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary) }
                    if (inc.deposits.isEmpty()) item { Text("No income recorded this month.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    items(inc.deposits, key = { it.id }) { txn -> TransactionRow(txn) { onOpenTransaction(txn.id) } }
                    item {
                        OutlinedButton(onClick = { vm.error = null; adding = true }, modifier = Modifier.fillMaxWidth()) {
                            Text("Add income by hand")
                        }
                        Text("Pay arrives on its own from Wealthsimple and RBC deposit alerts, and from screenshots of an " +
                            "account's transactions. Add anything else here.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 8.dp))
                    }
                }
            }
        }
    }
    if (adding) AddIncomeDialog(vm.saving, vm.error, onDismiss = { adding = false }) { body -> vm.add(body) { adding = false } }
}

@Composable
private fun PayDateRow(pay: PayDate) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(pay.name)
            Text(LocalDate.parse(pay.day).format(payDay), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(pay.amount.money())
            when (pay.status) {
                "received" -> Text("✓ Received", style = MaterialTheme.typography.bodySmall, color = ChartColors.good)
                "late" -> Text("Not seen yet", style = MaterialTheme.typography.bodySmall, color = ChartColors.critical)
                else -> Text("Upcoming", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    HorizontalDivider()
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AddIncomeDialog(saving: Boolean, error: String?, onDismiss: () -> Unit, onAdd: (IncomeIn) -> Unit) {
    var amount by remember { mutableStateOf("") }
    var payer by remember { mutableStateOf("") }
    var day by remember { mutableStateOf(LocalDate.now().toString()) }
    var earner by remember { mutableStateOf(EARNERS.first()) }
    var kind by remember { mutableStateOf(INCOME_KINDS.first()) }
    val cents = amount.replace(",", "").removePrefix("$").toBigDecimalOrNull()?.takeIf { it > BigDecimal.ZERO && it.scale() <= 2 }
    val date = runCatching { LocalDate.parse(day) }.getOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add income") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(amount, { amount = it }, label = { Text("Amount") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(payer, { payer = it }, label = { Text("From (payer)") }, singleLine = true)
                OutlinedTextField(day, { day = it }, label = { Text("Date (YYYY-MM-DD)") }, singleLine = true,
                    isError = date == null)
                Text("Who earned it", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    EARNERS.forEach { FilterChip(selected = earner == it, onClick = { earner = it }, label = { Text(it) }) }
                }
                Text("Kind", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    INCOME_KINDS.forEach { FilterChip(selected = kind == it, onClick = { kind = it }, label = { Text(it) }) }
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(
                enabled = !saving && cents != null && payer.isNotBlank() && date != null,
                onClick = {
                    onAdd(IncomeIn(amount = cents!!.setScale(2).toPlainString(), receivedOn = date!!.toString(),
                        earner = earner, payer = payer.trim(), category = kind))
                },
            ) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** The dashboard's income card: pay received (by person), still expected, and what's left after spending. */
