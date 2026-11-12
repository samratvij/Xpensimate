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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
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
import com.xpensimate.app.data.Account
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.Goal
import com.xpensimate.app.data.GoalIn
import com.xpensimate.app.data.SavingsPlan
import com.xpensimate.app.data.SavingsTarget
import com.xpensimate.app.data.SavingsTargetIn
import com.xpensimate.app.data.SavingsTotals
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.time.LocalDate
import java.time.format.DateTimeFormatter

class SavingsViewModel(private val api: Api) : ViewModel() {
    var plan by mutableStateOf<Load<SavingsPlan>>(Load.Loading)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    /** Every account, to add single ones to a goal (a loan, or one account of another kind). */
    var accounts by mutableStateOf<List<Account>>(emptyList())
        private set

    fun load() {
        viewModelScope.launch {
            if (accounts.isEmpty()) runCatching { api.accounts() }.onSuccess { accounts = it }
            if (plan !is Load.Ok) plan = Load.Loading
            plan = runCatching { api.savingsPlan() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun setTarget(body: SavingsTargetIn, onDone: () -> Unit) = act(onDone) { api.setSavingsTarget(body) }

    fun saveGoal(id: String?, body: GoalIn, onDone: () -> Unit) = act(onDone) {
        if (id == null) api.createGoal(body) else api.updateGoal(id, body)
    }

    fun deleteGoal(id: String, onDone: () -> Unit) = act(onDone) { api.deleteGoal(id) }

    private fun act(onDone: () -> Unit, block: suspend () -> Unit) {
        viewModelScope.launch {
            busy = true
            error = null
            runCatching { block() }.onSuccess { onDone(); load() }.onFailure { error = it.friendly() }
            busy = false
        }
    }
}

private val goalDate = DateTimeFormatter.ofPattern("MMM d, yyyy")
private val STATUS = mapOf("met" to "Target met ✓", "on_track" to "On track", "behind" to "Behind target")
// Kinds of account a goal can count as a whole (app/core/accounts.py); single accounts of any kind can be added too.
private val GOAL_KINDS = listOf("savings" to "Savings", "investment" to "Investment", "retirement" to "Retirement",
    "chequing" to "Chequing")

private fun Goal.leftText(): String = when {
    remaining <= 0 -> "Reached ✓"
    requiredMonthly != null -> "${requiredMonthly.money()}/month to reach it by ${LocalDate.parse(targetDate).format(goalDate)}"
    else -> "${remaining.money()} short; the date has passed"
}

@Composable
private fun GoalProgress(goal: Goal, named: Boolean = true) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row {
            Text(if (named) goal.name else "", fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Text("${goal.current.money()} of ${goal.targetAmount.money()}", style = MaterialTheme.typography.bodySmall)
        }
        LinearProgressIndicator(progress = { (goal.percent / 100).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
        Text("${goal.percent.toInt()}% · ${goal.leftText()}", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The dashboard's savings card: this month's target progress and each goal. */
@Composable
fun SavingsCard(savings: SavingsTotals, goals: List<Goal>, onClick: () -> Unit) {
    val target = savings.target ?: return
    val behind = savings.targetStatus == "behind"
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Savings this month", style = MaterialTheme.typography.titleSmall)
                    Text("${STATUS[savings.targetStatus] ?: ""} · income less spending ${savings.kept.money()}",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (behind) ChartColors.warning else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text("${savings.moved.money()} / ${target.money()}", fontWeight = FontWeight.SemiBold)
            }
            LinearProgressIndicator(
                progress = { (savings.moved / target).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth(),
                color = if (behind) ChartColors.warning else MaterialTheme.colorScheme.primary,
            )
            goals.forEach { GoalProgress(it) }
        }
    }
}

@Composable
fun SavingsScreen(vm: SavingsViewModel) {
    LaunchedEffect(Unit) { vm.load() }
    var editingTarget by remember { mutableStateOf(false) }
    // null: closed; Pair(null, _): new goal.
    var editingGoal by remember { mutableStateOf<Pair<Goal?, Unit>?>(null) }
    when (val state = vm.plan) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> {
            val plan = state.value
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("Monthly savings target", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                                TextButton(onClick = { editingTarget = true }) { Text("Edit") }
                            }
                            val p = plan.progress
                            val t = plan.target
                            if (p != null && t != null && t.monthlyAmount > 0) {
                                Text("${p.saved.money()} of ${p.target.money()} this month",
                                    style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                                LinearProgressIndicator(progress = { (p.saved / p.target).toFloat().coerceIn(0f, 1f) },
                                    modifier = Modifier.fillMaxWidth(),
                                    color = if (p.status == "behind") ChartColors.warning else MaterialTheme.colorScheme.primary)
                                Text(STATUS[p.status].orEmpty() + if (p.remaining > 0) " · ${p.remaining.money()} to go" else "",
                                    style = MaterialTheme.typography.bodySmall)
                                Text("Counts money moved into savings and investment accounts. Income less spending this " +
                                    "month: ${p.kept.money()}. From day ${t.alertDay}, under ${t.warningPercent.toInt()}% of " +
                                    "the target sends an alert.", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            } else {
                                Text("No target set.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
                items(plan.goals, key = { it.id }) { goal ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(goal.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                                TextButton(onClick = { editingGoal = goal to Unit }) { Text("Edit") }
                            }
                            GoalProgress(goal, named = false)
                            Text("${goal.monthsLeft} month-ends left · ${goal.remaining.money()} to go · " +
                                "${goal.movedThisMonth.money()} moved in this month" +
                                (goal.oldestBalance?.let { " · some balances from ${LocalDate.parse(it).format(goalDate)}" } ?: ""),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("Counts: " + goal.accounts.joinToString(", ") { it.accountName },
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                item {
                    OutlinedButton(onClick = { editingGoal = null to Unit }, modifier = Modifier.fillMaxWidth()) { Text("Add a goal") }
                    Text("Goals count the accounts' latest balances (Balances screen). The monthly amount is what's left " +
                        "divided by the month-ends left: a fact, not advice.", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
                }
            }
            if (editingTarget) TargetDialog(plan.target, vm.busy, vm.error, onDismiss = { editingTarget = false }) {
                vm.setTarget(it) { editingTarget = false }
            }
            editingGoal?.let { (goal, _) ->
                GoalDialog(goal, vm.accounts, vm.busy, vm.error, onDismiss = { editingGoal = null },
                    onDelete = goal?.let { g -> { vm.deleteGoal(g.id) { editingGoal = null } } }) { body ->
                    vm.saveGoal(goal?.id, body) { editingGoal = null }
                }
            }
        }
    }
}

private fun amountOf(text: String): BigDecimal? =
    text.filter { it.isDigit() || it == '.' }.toBigDecimalOrNull()?.takeIf { it >= BigDecimal.ZERO && it.scale() <= 2 }

@Composable
private fun TargetDialog(target: SavingsTarget?, busy: Boolean, error: String?, onDismiss: () -> Unit, onSave: (SavingsTargetIn) -> Unit) {
    var amount by remember { mutableStateOf(target?.let { "%.2f".format(it.monthlyAmount) } ?: "") }
    var day by remember { mutableStateOf((target?.alertDay ?: 25).toString()) }
    var warn by remember { mutableStateOf((target?.warningPercent ?: 80.0).toInt().toString()) }
    val value = amountOf(amount)
    val dayValue = day.toIntOrNull()?.takeIf { it in 1..28 }
    val warnValue = warn.toDoubleOrNull()?.takeIf { it > 0 && it <= 100 }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Monthly savings target") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(amount, { amount = it }, label = { Text("Amount a month (0 turns it off)") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(day, { day = it }, label = { Text("Alert from day (1–28)") }, singleLine = true,
                    isError = dayValue == null, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                OutlinedTextField(warn, { warn = it }, label = { Text("Alert below (% of target)") }, singleLine = true,
                    isError = warnValue == null, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && value != null && dayValue != null && warnValue != null, onClick = {
                onSave(SavingsTargetIn(value!!.setScale(2).toPlainString(), dayValue!!, warnValue!!))
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GoalDialog(
    goal: Goal?, accounts: List<Account>, busy: Boolean, error: String?, onDismiss: () -> Unit, onDelete: (() -> Unit)?,
    onSave: (GoalIn) -> Unit,
) {
    var name by remember { mutableStateOf(goal?.name ?: "") }
    var amount by remember { mutableStateOf(goal?.let { "%.2f".format(it.targetAmount) } ?: "") }
    var date by remember { mutableStateOf(goal?.targetDate ?: "") }
    var kinds by remember { mutableStateOf(goal?.accountKinds?.toSet() ?: setOf("savings")) }
    var ids by remember { mutableStateOf(goal?.accountIds?.toSet() ?: emptySet()) }
    val value = amountOf(amount)?.takeIf { it > BigDecimal.ZERO }
    val day = runCatching { LocalDate.parse(date) }.getOrNull()
    val hasAccounts = kinds.isNotEmpty() || ids.isNotEmpty()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (goal == null) "New goal" else "Edit goal") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true)
                OutlinedTextField(amount, { amount = it }, label = { Text("Amount") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(date, { date = it }, label = { Text("By (YYYY-MM-DD)") }, singleLine = true, isError = day == null)
                Text("Counts every account of these kinds", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    GOAL_KINDS.forEach { (kind, label) ->
                        FilterChip(selected = kind in kinds, onClick = { kinds = if (kind in kinds) kinds - kind else kinds + kind },
                            label = { Text(label) })
                    }
                }
                // Single accounts on top of the kinds (a loan, or one account of another kind); cards never count.
                val others = accounts.filter { it.kind !in kinds && it.kind != "credit_card" }
                if (others.isNotEmpty()) {
                    Text("…and these accounts", style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        others.sortedBy { it.accountName }.forEach { a ->
                            FilterChip(selected = a.id in ids, onClick = { ids = if (a.id in ids) ids - a.id else ids + a.id },
                                label = { Text(a.accountName) })
                        }
                    }
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                if (onDelete != null) TextButton(onClick = onDelete, enabled = !busy) {
                    Text("Delete goal", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && name.isNotBlank() && value != null && day != null && hasAccounts, onClick = {
                // Accounts already covered by a picked kind needn't be listed again.
                val single = ids.filter { id -> accounts.firstOrNull { it.id == id }?.kind !in kinds }
                onSave(GoalIn(name.trim(), value!!.setScale(2).toPlainString(), day.toString(), kinds.toList(), single))
            }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
