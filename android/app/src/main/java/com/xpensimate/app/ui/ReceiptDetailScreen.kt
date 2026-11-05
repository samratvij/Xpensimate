package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.ItemPatch
import com.xpensimate.app.data.MatchCandidate
import com.xpensimate.app.data.MatchRequest
import com.xpensimate.app.data.Receipt
import com.xpensimate.app.data.ReceiptItem
import com.xpensimate.app.data.Taxonomy
import kotlinx.coroutines.launch
import java.time.format.DateTimeFormatter

class ReceiptDetailViewModel(private val api: Api, private val id: String) : ViewModel() {
    var receipt by mutableStateOf<Load<Receipt>>(Load.Loading)
        private set
    var candidates by mutableStateOf<List<MatchCandidate>>(emptyList())
        private set
    var taxonomy by mutableStateOf<Taxonomy?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set

    init { load() }

    fun load() {
        viewModelScope.launch {
            receipt = runCatching { api.receipt(id) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            refreshCandidates()
            if (taxonomy == null) taxonomy = runCatching { api.taxonomy() }.getOrNull()
        }
    }

    private suspend fun refreshCandidates() {
        val current = (receipt as? Load.Ok)?.value ?: return
        candidates = if (current.matchedTransactionId == null) runCatching { api.candidates(id) }.getOrDefault(emptyList())
        else emptyList()
    }

    private fun act(block: suspend () -> Receipt?) {
        viewModelScope.launch {
            busy = true
            error = null
            runCatching { block() }
                .onSuccess { updated -> if (updated != null) { receipt = Load.Ok(updated); refreshCandidates() } }
                .onFailure { error = it.friendly() }
            busy = false
        }
    }

    fun updateItem(item: ReceiptItem, category: String? = null, purpose: String? = null, note: String? = null) =
        act { api.updateReceiptItem(id, item.id, ItemPatch(category, purpose, note)) }

    fun match(candidate: MatchCandidate) = act { api.matchReceipt(id, MatchRequest(candidate.transaction.id)) }
    fun unmatch() = act { api.unmatchReceipt(id) }

    fun delete(onDeleted: () -> Unit) {
        viewModelScope.launch {
            busy = true
            runCatching { api.deleteReceipt(id) }.onSuccess { onDeleted() }.onFailure { error = it.friendly() }
            busy = false
        }
    }
}

private val shortDate = DateTimeFormatter.ofPattern("MMM d, h:mm a")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReceiptDetailScreen(vm: ReceiptDetailViewModel, onOpenTransaction: (String) -> Unit, onDeleted: () -> Unit) {
    var editing by remember { mutableStateOf<ReceiptItem?>(null) }
    var confirmDelete by remember { mutableStateOf(false) }

    when (val state = vm.receipt) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> {
            val receipt = state.value
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(receipt.merchant, style = MaterialTheme.typography.headlineSmall)
                Text(receipt.total.money(), style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.SemiBold)
                Text(
                    "${parseInstant(receipt.timestamp).atZone(zone).format(shortDate)} · tax ${receipt.tax.money()}",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (receipt.needsReview) {
                    Text(
                        "The lines don't add up to the total, or the date wasn't readable. Check them before trusting this receipt.",
                        color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium,
                    )
                }
                vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

                MatchCard(receipt, vm.candidates, vm.busy, vm::match, vm::unmatch, onOpenTransaction)

                Text("Lines", style = MaterialTheme.typography.titleMedium)
                Text("Tap a line to change what it is, who it was for, or add a note.", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                receipt.items.forEach { item ->
                    Row(
                        Modifier.fillMaxWidth().clickable { editing = item }.padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(item.skuName)
                            Text(
                                item.budgetLine + if (item.categoryIsManual || item.purposeIsManual) " · manual" else "",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            NoteLine(item.note)
                        }
                        Text(item.price.money())
                    }
                    HorizontalDivider()
                }

                OutlinedButton(
                    onClick = { confirmDelete = true }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                ) { Text("Delete receipt") }
            }

            editing?.let { item ->
                val taxonomy = vm.taxonomy
                if (taxonomy != null) {
                    // Re-read the item after each save so the chips show the new choice.
                    val current = receipt.items.firstOrNull { it.id == item.id } ?: item
                    ModalBottomSheet(onDismissRequest = { editing = null }) {
                        ItemEditor(current, taxonomy, vm.busy, onNote = { vm.updateItem(current, note = it) }) { category, purpose ->
                            vm.updateItem(current, category, purpose)
                        }
                    }
                }
            }
            if (confirmDelete) {
                AlertDialog(
                    onDismissRequest = { confirmDelete = false },
                    title = { Text("Delete this receipt?") },
                    text = { Text("Its lines are removed from the matched card charge, which goes back to a single amount.") },
                    confirmButton = { TextButton(onClick = { confirmDelete = false; vm.delete(onDeleted) }) { Text("Delete") } },
                    dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } },
                )
            }
        }
    }
}

@Composable
private fun MatchCard(
    receipt: Receipt,
    candidates: List<MatchCandidate>,
    busy: Boolean,
    onMatch: (MatchCandidate) -> Unit,
    onUnmatch: () -> Unit,
    onOpenTransaction: (String) -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val matched = receipt.matchedTransactionId
            if (matched != null) {
                Text("Matched to a card charge", style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { onOpenTransaction(matched) }) { Text("Open charge") }
                    TextButton(onClick = onUnmatch, enabled = !busy) { Text("Unmatch") }
                }
            } else if (candidates.isEmpty()) {
                Text("No card charge matched yet", style = MaterialTheme.typography.titleSmall)
                Text("It will match automatically when the bank alert for this purchase arrives.",
                    style = MaterialTheme.typography.bodySmall)
            } else {
                Text("Which charge is this?", style = MaterialTheme.typography.titleSmall)
                candidates.forEach { c ->
                    Row(
                        Modifier.fillMaxWidth().clickable(enabled = !busy) { onMatch(c) }.padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(c.transaction.merchant)
                            Text(
                                parseInstant(c.transaction.timestamp).atZone(zone).format(shortDate) +
                                    if (c.tip) " · includes tip" else "",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(c.transaction.amount.money())
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ItemEditor(
    item: ReceiptItem,
    taxonomy: Taxonomy,
    busy: Boolean,
    onNote: (String) -> Unit,
    onChange: (String?, String?) -> Unit,
) {
    Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(item.skuName, style = MaterialTheme.typography.titleMedium)
        item.rawText?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        Text("What", style = MaterialTheme.typography.titleSmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            taxonomy.categories.forEach { c ->
                FilterChip(selected = item.category == c, enabled = !busy,
                    onClick = { if (item.category != c) onChange(c, null) }, label = { Text(c) })
            }
        }
        Text("Who", style = MaterialTheme.typography.titleSmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            taxonomy.purposes.forEach { p ->
                FilterChip(selected = item.purpose == p, enabled = !busy,
                    onClick = { if (item.purpose != p) onChange(null, p) }, label = { Text(p) })
            }
        }
        Text("Note", style = MaterialTheme.typography.titleSmall)
        NoteEditor(item.note, busy, onNote, placeholder = "What this was, or who it was for")
        Text(" ")
    }
}
