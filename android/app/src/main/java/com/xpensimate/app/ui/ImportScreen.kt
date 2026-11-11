package com.xpensimate.app.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Account
import kotlin.math.abs
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.StatementCommit
import com.xpensimate.app.data.StatementImport
import com.xpensimate.app.data.StatementLine
import com.xpensimate.app.data.TxnBrief
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.time.YearMonth

class ImportViewModel(private val api: Api) : ViewModel() {
    /** null = the whole file. */
    var month by mutableStateOf<YearMonth?>(YearMonth.now().minusMonths(1))
    /** null = detect it from the file or screenshot. */
    var account by mutableStateOf<String?>(null)
    var accounts by mutableStateOf<List<Account>>(emptyList())
        private set
    var preview by mutableStateOf<StatementImport?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set
    /** Row index -> "add" | "skip" | "match:<id>" for unclear rows; "income" | "add" | "transfer" | "skip" for deposits. */
    val decisions = mutableStateMapOf<String, String>()
    /** Ids of replaced charges to keep (a separate purchase after all). */
    val keep = mutableStateListOf<String>()

    fun loadAccounts() {
        if (accounts.isNotEmpty()) return
        viewModelScope.launch { runCatching { api.accounts() }.onSuccess { accounts = it } }
    }

    fun load(context: Context, uri: Uri) {
        viewModelScope.launch {
            busy = true
            error = null
            decisions.clear()
            keep.clear()
            runCatching {
                val (name, bytes) = withContext(Dispatchers.IO) { readFile(context, uri) }
                val type = context.contentResolver.getType(uri) ?: "application/octet-stream"
                val file = MultipartBody.Part.createFormData("file", name, bytes.toRequestBody(type.toMediaType()))
                api.previewStatement(
                    file,
                    month?.let { MultipartBody.Part.createFormData("month", it.toString()) },
                    account?.let { MultipartBody.Part.createFormData("account_id", it) },
                )
            }.onSuccess { p ->
                preview = p
                // What the app suggests is chosen unless changed.
                p.rows.forEach { row -> row.suggested?.let { decisions[row.index.toString()] = it } }
            }.onFailure { error = it.friendly() }
            busy = false
        }
    }

    fun commit() {
        val current = preview ?: return
        viewModelScope.launch {
            busy = true
            error = null
            runCatching {
                var state = api.commitStatement(current.id, StatementCommit(decisions.toMap(), keep.toList()))
                while (state.status == "importing") {
                    preview = current.copy(status = state.status, processed = state.processed)
                    delay(1000)
                    state = api.statement(current.id)
                }
                state
            }.onSuccess { preview = it }.onFailure { error = it.friendly() }
            busy = false
        }
    }

    fun reset() { preview = null; error = null; decisions.clear(); keep.clear() }
}

private fun readFile(context: Context, uri: Uri): Pair<String, ByteArray> {
    val name = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
        if (it.moveToFirst()) it.getString(0) else null
    } ?: "statement.csv"
    val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: error("Couldn't open the file")
    return name to bytes
}

private val ACTION_TITLES = mapOf(
    "add" to "Missing from the app → will be added",
    "payment" to "Card payments → not an expense",
    "match" to "Already in the app",
    "imported" to "Already imported",
    "outside" to "Outside the chosen month",
    "unclear" to "Needs your decision",
    "income" to "Pay → recorded as income",
    "deposit" to "Money in: your call",
    "transfer" to "Money out: where to?",
)

@Composable
fun ImportScreen(vm: ImportViewModel) {
    val context = LocalContext.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) vm.load(context, uri)
    }
    val screenshotPicker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) vm.load(context, uri)
    }
    val preview = vm.preview
    LaunchedEffect(Unit) { vm.loadAccounts() }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (preview == null) {
            item {
                Text("Import a bank statement", style = MaterialTheme.typography.titleLarge)
                Text("Adds only what the app is missing and keeps card payments out of your spending. " +
                    "Canadian Tire, PC Financial, Tangerine and Wealthsimple CSV exports, RBC PDF statements, " +
                    "or a screenshot of your bank app's transactions.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            item {
                Text("Which month?", style = MaterialTheme.typography.titleSmall)
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    val last = YearMonth.now().minusMonths(1)
                    listOf(last.minusMonths(1), last, YearMonth.now()).forEach { m ->
                        FilterChip(selected = vm.month == m, onClick = { vm.month = m }, label = { Text(m.title()) })
                    }
                    FilterChip(selected = vm.month == null, onClick = { vm.month = null }, label = { Text("Whole file") })
                }
                Text("Screenshots ignore the month: they show only a few days anyway.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            item {
                Text("Which account?", style = MaterialTheme.typography.titleSmall)
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = vm.account == null, onClick = { vm.account = null }, label = { Text("Detect") })
                    vm.accounts.forEach { a ->
                        FilterChip(selected = vm.account == a.id, onClick = { vm.account = a.id }, label = { Text(a.accountName) })
                    }
                }
            }
            item {
                Button(onClick = { picker.launch(arrayOf("*/*")) }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) {
                    Text(if (vm.busy) "Reading…" else "Choose statement file")
                }
                OutlinedButton(
                    onClick = { screenshotPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                    enabled = !vm.busy, modifier = Modifier.fillMaxWidth(),
                ) { Text("Choose screenshot") }
            }
            vm.error?.let { item { Text(it, color = MaterialTheme.colorScheme.error) } }
        } else {
            previewItems(preview, vm)
        }
    }
}

private fun LazyListScope.previewItems(p: StatementImport, vm: ImportViewModel) {
    item {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(p.filename, style = MaterialTheme.typography.titleMedium)
                Text("${p.accountId} · ${p.periodStart ?: "?"} to ${p.periodEnd ?: "?"}",
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                p.counts.forEach { (action, n) -> Text("${ACTION_TITLES[action] ?: action}: $n") }

            }
        }
    }
    when (p.status) {
        "importing" -> item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinearProgressIndicator(progress = { p.processed.toFloat() / p.rows.size.coerceAtLeast(1) }, modifier = Modifier.fillMaxWidth())
                Text("Importing ${p.processed} of ${p.rows.size} rows…")
            }
        }
        "done", "failed" -> item {
            val r = p.result.orEmpty().mapValues { it.value.jsonPrimitive.intOrNull }
            Text(
                if (p.status == "done") "Done. Added ${r["added"] ?: 0}" +
                    (r["income"]?.let { ", income $it" } ?: "") + (r["transfer"]?.let { ", transfers $it" } ?: "") + ", confirmed ${r["matched"] ?: 0}, " +
                    "card payments ${r["payment"] ?: 0}, skipped ${r["skipped"] ?: 0}" +
                    (r["replaced"]?.let { ", replaced charges removed $it." } ?: ".")
                else "The import stopped part way. Importing the file again picks up the rest.",
                style = MaterialTheme.typography.titleSmall,
                color = if (p.status == "done") MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            )
            OutlinedButton(onClick = vm::reset, modifier = Modifier.padding(top = 8.dp)) { Text("Import another") }
        }
        else -> {
            val unclear = p.rows.filter { it.action == "unclear" }
            if (unclear.isNotEmpty()) {
                item { SectionTitle("Needs your decision (${unclear.size})") }
                items(unclear, key = { "u${it.index}" }) { row -> UnclearRow(row, vm.decisions[row.index.toString()]) { vm.decisions[row.index.toString()] = it } }
            }
            val deposits = p.rows.filter { it.action == "deposit" }
            if (deposits.isNotEmpty()) {
                item {
                    SectionTitle("Money in: what is it? (${deposits.size})")
                    Text("Deposits that aren't pay. Income counts as money earned; money back counts against spending " +
                        "(someone paying you back); a transfer is your own money moving and isn't counted.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                items(deposits, key = { "d${it.index}" }) { row ->
                    MoveRow(row, vm.accounts.filter { it.id != p.accountId }, incoming = true,
                        chosen = vm.decisions[row.index.toString()]) { vm.decisions[row.index.toString()] = it }
                }
            }
            val transfers = p.rows.filter { it.action == "transfer" }
            if (transfers.isNotEmpty()) {
                item {
                    SectionTitle("Money out: where did it go? (${transfers.size})")
                    Text("Transfers and e-transfers sent. To a savings or investment account counts as saving; between " +
                        "people in the household isn't counted; paying someone for something is spending.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                items(transfers, key = { "t${it.index}" }) { row ->
                    MoveRow(row, vm.accounts.filter { it.id != p.accountId }, incoming = false,
                        chosen = vm.decisions[row.index.toString()]) { vm.decisions[row.index.toString()] = it }
                }
            }
            section("income", p, null)
            section("add", p, "Categorized with your rules, your history, then the AI model.")
            section("payment", p, null)
            val (replaced, stray) = p.missing.partition { it.replacedBy != null }
            if (replaced.isNotEmpty()) {
                item {
                    SectionTitle("Replaced by the final charge → not an expense (${replaced.size})")
                    Text("The order changed, so the bank dropped the first charge and charged the final amount. " +
                        "Tick any that were a separate purchase.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                items(replaced, key = { "r${it.id}" }) { t ->
                    ReplacedRow(t, kept = t.id in vm.keep) { keep -> if (keep) vm.keep.add(t.id) else vm.keep.remove(t.id) }
                }
            }
            if (stray.isNotEmpty()) {
                item { SectionTitle("In the app but not on this statement (${stray.size})") }
                items(stray, key = { "m${it.id}" }) { BriefRow(it) }
            }
            item {
                val left = (unclear + deposits + transfers).count { vm.decisions[it.index.toString()] == null }
                val anything = p.rows.any { it.action in setOf("add", "payment", "match", "unclear", "income", "deposit", "transfer") }
                vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Button(onClick = vm::commit, enabled = !vm.busy && left == 0 && anything) { Text("Import") }
                    OutlinedButton(onClick = vm::reset, enabled = !vm.busy) { Text("Cancel") }
                }
                if (left > 0) Text("Decide $left more row(s) first.", style = MaterialTheme.typography.bodySmall)
                if (!anything) Text("Nothing to import.", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

private fun LazyListScope.section(action: String, p: StatementImport, note: String?) {
    val rows = p.rows.filter { it.action == action }
    if (rows.isEmpty()) return
    item {
        SectionTitle("${ACTION_TITLES[action]} (${rows.size})")
        note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
    items(rows, key = { "$action${it.index}" }) { LineRow(it) }
}

@Composable
private fun SectionTitle(text: String) =
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(top = 8.dp))

@Composable
private fun LineRow(row: StatementLine) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.weight(1f)) {
            Text(row.merchant + when (row.kind) { "refund" -> " (refund)"; "interest" -> " (interest)"; "deposit" -> " (deposit)"; "transfer" -> " (transfer)"; else -> "" } +
                if (row.pending) " (pending)" else "")
            Text(listOfNotNull(row.date, row.bankCategory, row.pays?.let { "also excludes ${it.merchant} ${it.date}" }).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(row.amount.toDouble().money())
    }
    HorizontalDivider()
}

@Composable
private fun BriefRow(t: TxnBrief) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.weight(1f)) {
            Text(t.merchant)
            Text("${t.date} · ${t.source}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(t.amount.toDouble().money())
    }
    HorizontalDivider()
}

@Composable
private fun ReplacedRow(t: TxnBrief, kept: Boolean, onKeep: (Boolean) -> Unit) {
    val final = t.replacedBy ?: return
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(t.merchant)
            Text("${t.date} · replaced by ${final.amount.toDouble().money()} on ${final.date}",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(t.amount.toDouble().money())
        Checkbox(checked = kept, onCheckedChange = onKeep)
        Text("Keep", style = MaterialTheme.typography.bodySmall)
    }
    HorizontalDivider()
}

/** A deposit (money in) or a transfer out: what it was, including which own account it came from or went to. */
@Composable
private fun MoveRow(row: StatementLine, accounts: List<Account>, incoming: Boolean, chosen: String?, onChoose: (String) -> Unit) {
    var picking by remember { mutableStateOf(false) }
    val amount = abs(row.amount.toDouble()).money()
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text("${row.date} · ${row.merchant} · ${if (incoming) "+" else ""}$amount", fontWeight = FontWeight.SemiBold)
            val moved = chosen?.takeIf { it.startsWith("move:") }?.removePrefix("move:")
            Box {
                Row(Modifier.fillMaxWidth().clickable { picking = true }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = moved != null, onClick = { picking = true })
                    Text(moved?.let { id -> "${if (incoming) "From" else "To"} ${accounts.firstOrNull { it.id == id }?.accountName ?: id}" }
                        ?: "${if (incoming) "From" else "To"} another of our accounts…")
                }
                DropdownMenu(expanded = picking, onDismissRequest = { picking = false }) {
                    accounts.sortedBy { MOVE_ORDER.indexOf(it.kind).let { i -> if (i < 0) 99 else i } }.forEach { a ->
                        DropdownMenuItem(
                            text = { Text("${a.accountName} · ${a.kind.replace('_', ' ')}") },
                            onClick = { onChoose("move:${a.id}"); picking = false },
                        )
                    }
                }
            }
            val options = if (incoming) listOf(
                "income" to "Income",
                "add" to "Money back (counts against spending)",
                "transfer" to "From someone in the household / own money (not counted)",
                "skip" to "Skip",
            ) else listOf(
                "transfer" to "To someone in the household / own money (not counted)",
                "add" to "Spending: paid someone for something",
                "skip" to "Skip",
            )
            options.forEach { (value, label) ->
                Row(Modifier.fillMaxWidth().clickable { onChoose(value) }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = chosen == value, onClick = { onChoose(value) })
                    Text(label)
                }
            }
        }
    }
}

private val MOVE_ORDER = listOf("savings", "tfsa", "fhsa", "rrsp", "non_registered", "chequing", "credit_card", "loan", "asset")

@Composable
private fun UnclearRow(row: StatementLine, chosen: String?, onChoose: (String) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text("${row.date} · ${row.merchant} · ${row.amount.toDouble().money()}", fontWeight = FontWeight.SemiBold)
            val options = row.candidates.map { "match:${it.id}" to "Is ${it.merchant} on ${it.date} (${it.source})" } +
                listOf("add" to "Add as a new charge", "skip" to "Skip")
            options.forEach { (value, label) ->
                Row(Modifier.fillMaxWidth().clickable { onChoose(value) }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = chosen == value, onClick = { onChoose(value) })
                    Text(label)
                }
            }
        }
    }
}
