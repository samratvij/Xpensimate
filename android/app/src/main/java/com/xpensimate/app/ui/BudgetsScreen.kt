package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import com.xpensimate.app.data.Budget
import com.xpensimate.app.data.BudgetUpdate
import com.xpensimate.app.data.LineSpend
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import java.time.YearMonth

data class BudgetRow(val line: String, val budget: Budget?, val spend: LineSpend?, val personal: Boolean = false)

class BudgetsViewModel(private val api: Api) : ViewModel() {
    var rows by mutableStateOf<Load<List<BudgetRow>>>(Load.Loading)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    init { load() }

    fun load() {
        viewModelScope.launch {
            rows = runCatching {
                val taxonomy = async { api.taxonomy() }
                val budgets = async { api.budgets().associateBy { it.category } }
                val spend = async { api.summary(YearMonth.now().toString()).categories.associateBy { it.category } }
                val (t, b, s) = Triple(taxonomy.await(), budgets.await(), spend.await())
                PersonLines.kinds = t.personLines
                t.budgetLines.map { BudgetRow(it, b[it], s[it], personal = it in t.personLines) }
            }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun save(line: String, limit: Double, warningPercent: Double) = act { api.setBudget(line, BudgetUpdate(limit, warningPercent)) }
    fun remove(line: String) = act { api.removeBudget(line) }

    private fun act(block: suspend () -> Unit) {
        viewModelScope.launch {
            error = null
            runCatching { block() }.onSuccess { load() }.onFailure { error = it.friendly() }
        }
    }
}

@Composable
fun BudgetsScreen(vm: BudgetsViewModel) {
    var editing by remember { mutableStateOf<BudgetRow?>(null) }
    when (val state = vm.rows) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> {
            val (people, shared) = state.value.partition { it.personal }
            val total = state.value.sumOf { it.budget?.monthlyLimit ?: 0.0 }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                item {
                    Column(Modifier.padding(16.dp)) {
                        Text("Monthly budgets total ${total.money()}", style = MaterialTheme.typography.titleMedium)
                        Text("Tap a line to set its monthly limit. You'll get a notification at the warning level and when it's exceeded.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    }
                }
                item { SectionHeader("Shared") }
                items(shared, key = { it.line }) { row -> BudgetLineRow(row) { editing = row }; HorizontalDivider() }
                item { SectionHeader("People") }
                items(people, key = { it.line }) { row -> BudgetLineRow(row) { editing = row }; HorizontalDivider() }
            }
        }
    }
    editing?.let { row ->
        BudgetDialog(
            row = row,
            onDismiss = { editing = null },
            onSave = { limit, warning -> vm.save(row.line, limit, warning); editing = null },
            onRemove = { vm.remove(row.line); editing = null },
        )
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(title, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp))
}

@Composable
private fun BudgetLineRow(row: BudgetRow, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(row.line, style = MaterialTheme.typography.bodyLarge)
            Text(
                "${(row.spend?.spent ?: 0.0).money()} spent this month" +
                    ((row.spend?.expected ?: 0.0).takeIf { it > 0 }?.let { " · +${it.money()} expected" } ?: ""),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        val budget = row.budget
        if (budget != null) {
            Column(horizontalAlignment = Alignment.End) {
                Text(budget.monthlyLimit.money(), fontWeight = FontWeight.SemiBold)
                Text("warn at ${budget.warningThresholdPercent.toInt()}%", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            Text("Set budget", color = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
private fun BudgetDialog(row: BudgetRow, onDismiss: () -> Unit, onSave: (Double, Double) -> Unit, onRemove: () -> Unit) {
    var limit by remember { mutableStateOf(row.budget?.monthlyLimit?.let { "%.2f".format(it) } ?: "") }
    var warning by remember { mutableStateOf((row.budget?.warningThresholdPercent ?: 80.0).toInt().toString()) }
    val limitValue = limit.toDoubleOrNull()?.takeIf { it > 0 }
    val warningValue = warning.toDoubleOrNull()?.takeIf { it in 1.0..100.0 }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(row.line) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(value = limit, onValueChange = { limit = it }, label = { Text("Monthly limit ($)") },
                    singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(value = warning, onValueChange = { warning = it }, label = { Text("Warn at (% of limit)") },
                    singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                row.spend?.let {
                    Text("This month: ${it.spent.money()} spent" + if (it.expected > 0) ", ${it.projected.money()} projected" else "",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = limitValue != null && warningValue != null, onClick = { onSave(limitValue!!, warningValue!!) }) {
                Text("Save")
            }
        },
        dismissButton = {
            Row {
                if (row.budget != null) TextButton(onClick = onRemove) { Text("Remove budget", color = MaterialTheme.colorScheme.error) }
                TextButton(onClick = onDismiss) { Text("Cancel") }
            }
        },
    )
}
