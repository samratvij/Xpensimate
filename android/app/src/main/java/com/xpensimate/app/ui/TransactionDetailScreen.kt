package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Switch
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.Text
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
import com.xpensimate.app.data.EARNERS
import com.xpensimate.app.data.INCOME_KINDS
import com.xpensimate.app.data.SplitLine
import com.xpensimate.app.data.SplitRequest
import com.xpensimate.app.data.Taxonomy
import com.xpensimate.app.data.Transaction
import com.xpensimate.app.data.TransactionPatch
import kotlinx.coroutines.launch
import java.time.format.DateTimeFormatter

class TransactionDetailViewModel(private val api: Api, private val id: String) : ViewModel() {
    var txn by mutableStateOf<Load<Transaction>>(Load.Loading)
        private set
    var taxonomy by mutableStateOf<Taxonomy?>(null)
        private set
    var remember by mutableStateOf(false)
    var saving by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    init { load() }

    fun load() {
        viewModelScope.launch {
            txn = runCatching { api.transaction(id) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            if (taxonomy == null) taxonomy = runCatching { api.taxonomy() }.getOrNull()
        }
    }

    /** Selecting a chip saves immediately (PRD 7.2); "remember" also turns it into a rule for this merchant. */
    fun update(category: String? = null, purpose: String? = null) =
        save(TransactionPatch(category = category, purpose = purpose, remember = remember))

    /** Free-text note on the whole charge; "" removes it. */
    fun saveNote(text: String) = save(TransactionPatch(note = text))

    /** Not an expense (e.g. a credit card bill payment): kept, but left out of totals and budgets. */
    fun setExcluded(excluded: Boolean) = save(TransactionPatch(excluded = excluded))

    /** Replace the lines (an Amazon order with items for different people); an empty list removes the split. */
    fun split(lines: List<SplitLine>, onDone: () -> Unit) =
        act(onDone) { api.splitTransaction(id, SplitRequest(lines)) }

    private fun save(patch: TransactionPatch) = act { api.updateTransaction(id, patch) }

    private fun act(onDone: () -> Unit = {}, block: suspend () -> Transaction) {
        viewModelScope.launch {
            saving = true
            error = null
            runCatching { block() }
                .onSuccess { txn = Load.Ok(it); onDone() }
                .onFailure { error = it.friendly() }
            saving = false
        }
    }
}

private val detailTime = DateTimeFormatter.ofPattern("EEE, MMM d yyyy · h:mm a")

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun TransactionDetailScreen(vm: TransactionDetailViewModel, onOpenReceipt: (String) -> Unit) {
    var splitting by remember { mutableStateOf(false) }
    when (val state = vm.txn) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> {
            val txn = state.value
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(txn.merchant, style = MaterialTheme.typography.headlineSmall)
                Text(if (txn.isIncome) "+${txn.amount.money()}" else txn.amount.money(),
                    style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.SemiBold,
                    color = if (txn.isIncome) ChartColors.good else MaterialTheme.colorScheme.onSurface)
                Text(
                    parseInstant(txn.timestamp).atZone(zone).format(detailTime) +
                        (txn.accountId?.let { " · $it" } ?: ""),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (txn.merchantRaw != txn.merchant) {
                    Text(txn.merchantRaw, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                txn.editedBy?.let {
                    Text("Last changed by $it", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (txn.isIncome) {
                    AssistChip(onClick = {}, label = { Text("Income · not spending") })
                } else if (txn.isTransfer) {
                    AssistChip(onClick = {}, label = { Text("Moved: ${txn.moveText()} · not spending") })
                } else if (txn.excluded) {
                    Text("Not counted as an expense", color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.titleSmall)
                } else {
                    AssistChip(onClick = {}, label = { Text("Budget line: ${txn.budgetLine}") })
                }

                vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                Text("Note", style = MaterialTheme.typography.titleMedium)
                NoteEditor(txn.note, vm.saving, vm::saveNote)
                val taxonomy = vm.taxonomy
                if (txn.isIncome) {
                    // Income: its kind and who earned it. No rules, no "not an expense", no split.
                    SectionTitle("Kind", if (txn.categoryIsManual) "Manual" else "Auto")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        INCOME_KINDS.forEach { c ->
                            FilterChip(selected = txn.category == c, enabled = !vm.saving,
                                onClick = { if (txn.category != c) vm.update(category = c) }, label = { Text(c) })
                        }
                    }
                    SectionTitle("Who earned it", if (txn.purposeIsManual) "Manual" else "Auto")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        EARNERS.forEach { p ->
                            FilterChip(selected = txn.purpose == p, enabled = !vm.saving,
                                onClick = { if (txn.purpose != p) vm.update(purpose = p) }, label = { Text(p) })
                        }
                    }
                } else if (taxonomy != null) {
                    SectionTitle("What", if (txn.categoryIsManual) "Manual" else "Auto · ${txn.categorizedBy}")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        taxonomy.categories.forEach { c ->
                            FilterChip(selected = txn.category == c, enabled = !vm.saving,
                                onClick = { if (txn.category != c) vm.update(category = c) }, label = { Text(c) })
                        }
                    }
                    SectionTitle("Who", if (txn.purposeIsManual) "Manual" else "Auto")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        taxonomy.purposes.forEach { p ->
                            FilterChip(selected = txn.purpose == p, enabled = !vm.saving,
                                onClick = { if (txn.purpose != p) vm.update(purpose = p) }, label = { Text(p) })
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Remember for ${txn.merchant}")
                            Text("Future charges from this merchant get the same choice",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Switch(checked = vm.remember, onCheckedChange = { vm.remember = it })
                    }
                }

                if (!txn.isIncome) OutlinedButton(
                    onClick = { vm.setExcluded(!txn.excluded) },
                    enabled = !vm.saving,
                    colors = if (txn.excluded) ButtonDefaults.outlinedButtonColors()
                    else ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(when {
                        txn.isTransfer -> "Not a transfer: count as spending"
                        txn.excluded -> "Count as an expense again"
                        else -> "Not an expense (transfer, card payment…)"
                    })
                }

                if (txn.isIncome) return@Column
                HorizontalDivider()
                val receiptId = txn.receiptId
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        when {
                            receiptId != null -> "Receipt lines"
                            txn.items.isNotEmpty() -> "Split into ${txn.items.size} items"
                            else -> "Items"
                        },
                        style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f),
                    )
                    when {
                        receiptId != null -> TextButton(onClick = { onOpenReceipt(receiptId) }) { Text("Edit on receipt") }
                        vm.taxonomy != null -> TextButton(onClick = { splitting = true }, enabled = !vm.saving) {
                            Text(if (txn.items.isEmpty()) "Split" else "Edit split")
                        }
                    }
                }
                if (txn.items.isEmpty() && receiptId == null) {
                    Text("Bought several things for different people or budgets? Split the charge so each counts on its own.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (txn.items.isNotEmpty()) {
                    txn.items.forEach { item ->
                        Row(Modifier.fillMaxWidth()) {
                            Column(Modifier.weight(1f)) {
                                Text(item.skuName)
                                Text(item.budgetLine, style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                NoteLine(item.note, maxLines = 3)
                            }
                            Text(item.price.money())
                        }
                    }
                }
            }

            val taxonomy = vm.taxonomy
            if (splitting && taxonomy != null) {
                ModalBottomSheet(
                    onDismissRequest = { splitting = false },
                    sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
                ) {
                    SplitEditor(
                        txn, taxonomy, vm.saving, vm.error,
                        onSave = { lines -> vm.split(lines) { splitting = false } },
                        onRemove = { vm.split(emptyList()) { splitting = false } },
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(title: String, badge: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(badge, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
