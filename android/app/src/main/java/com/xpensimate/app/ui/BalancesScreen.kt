package com.xpensimate.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.AccountBalance
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.BalanceBatch
import com.xpensimate.app.data.BalanceBatchRow
import com.xpensimate.app.data.BalanceIn
import com.xpensimate.app.data.BalanceRead
import com.xpensimate.app.data.Balances
import com.xpensimate.app.data.EARNERS
import com.xpensimate.app.data.compressForUpload
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.math.BigDecimal
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import kotlin.math.abs

class BalancesViewModel(private val api: Api, start: YearMonth) : ViewModel() {
    var month by mutableStateOf(start)
        private set
    var balances by mutableStateOf<Load<Balances>>(Load.Loading)
        private set
    var busy by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    /** A screenshot's reading, waiting for the person to check it. */
    var reading by mutableStateOf<BalanceRead?>(null)
        private set
    private var me: String? = null

    fun selectMonth(value: YearMonth) { month = value; load() }

    fun load() {
        viewModelScope.launch {
            if (balances !is Load.Ok) balances = Load.Loading
            balances = runCatching { api.balances(month.toString()) }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun save(account: String, day: LocalDate, amount: BigDecimal, onDone: () -> Unit) = act(onDone) {
        api.setBalance(account, BalanceIn(day.toString(), amount.setScale(2).toPlainString())).let {
            if (!it.isSuccessful) error("Server error ${it.code()}")
        }
    }

    fun read(context: android.content.Context, uri: android.net.Uri) = act {
        if (me == null) me = runCatching { api.me().user }.getOrNull()
        val jpeg = withContext(Dispatchers.IO) { compressForUpload(context, uri) }
        val file = MultipartBody.Part.createFormData("file", "balances.jpg", jpeg.toRequestBody("image/jpeg".toMediaType()))
        val owner = me?.takeIf { it in EARNERS }?.let { MultipartBody.Part.createFormData("owner", it) }
        reading = api.readBalances(file, owner)
    }

    fun confirm(day: LocalDate, rows: List<BalanceBatchRow>) = act({ reading = null }) {
        api.saveBalances(BalanceBatch(day.toString(), rows)).let { if (!it.isSuccessful) error("Server error ${it.code()}") }
    }

    fun dismissReading() { reading = null }

    private fun act(onDone: () -> Unit = {}, block: suspend () -> Unit) {
        viewModelScope.launch {
            busy = true
            error = null
            runCatching { block() }.onSuccess { onDone(); load() }.onFailure { error = it.friendly() }
            busy = false
        }
    }
}

private val GROUP_TITLES = linkedMapOf(
    "savings" to "Savings", "investments" to "Investments", "chequing" to "Chequing",
    "credit_cards" to "Credit cards (owed)", "loans" to "Loans (owed)", "other" to "Other assets",
)
private val shortDate = DateTimeFormatter.ofPattern("MMM d, yyyy")

private fun Double.signedMoney(): String = (if (this < 0) "−" else "+") + abs(this).money()

/** The day a balance is for: the last day of a past month, or today in the current one. */
private fun YearMonth.balanceDay(): LocalDate = if (this == YearMonth.now()) LocalDate.now() else atEndOfMonth()

@Composable
fun BalancesScreen(vm: BalancesViewModel) {
    LaunchedEffect(Unit) { vm.load() }
    val context = LocalContext.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri -> if (uri != null) vm.read(context, uri) }
    var editing by remember { mutableStateOf<AccountBalance?>(null) }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.balances) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> {
                val worth = state.value
                LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("Net worth at the end of ${vm.month.title()}", style = MaterialTheme.typography.labelMedium)
                                Text((if (worth.netWorth < 0) "−" else "") + abs(worth.netWorth).money(),
                                    style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
                                Text("Assets ${worth.assets.money()} · owed ${worth.liabilities.money()}" +
                                    (worth.change?.let { " · ${it.signedMoney()} vs last month" } ?: ""),
                                    style = MaterialTheme.typography.bodySmall)
                                if (worth.contributions != 0.0 || worth.marketReturn != 0.0) {
                                    Text("Savings and investments: ${worth.contributions.money()} contributed, " +
                                        "${worth.marketReturn.signedMoney()} market return and interest.",
                                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                if (worth.missing > 0) Text("${worth.missing} accounts without a balance yet (left out).",
                                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                    item {
                        Button(
                            onClick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                            enabled = !vm.busy, modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (vm.busy) "Reading…" else "Read balances from a screenshot") }
                        vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    }
                    GROUP_TITLES.forEach { (group, title) ->
                        val list = worth.accounts.filter { it.group == group }
                        if (list.isEmpty()) return@forEach
                        item(key = "g$group") {
                            Row(Modifier.fillMaxWidth().padding(top = 8.dp)) {
                                Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.weight(1f))
                                Text(list.sumOf { it.balance ?: 0.0 }.money(), style = MaterialTheme.typography.titleSmall)
                            }
                        }
                        items(list, key = { it.accountId }) { BalanceRow(it) { editing = it } }
                    }
                    item {
                        Text("Tap an account to type its balance. Balances are what the bank app shows; for cards and " +
                            "loans, what's owed. Facts only, not investment advice.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                editing?.let { account ->
                    EditBalanceDialog(account, vm.month.balanceDay(), vm.busy, vm.error, onDismiss = { editing = null }) { day, amount ->
                        vm.save(account.accountId, day, amount) { editing = null }
                    }
                }
                vm.reading?.let { read -> ReviewReadingDialog(read, worth.accounts, vm.busy, vm.error, vm::dismissReading, vm::confirm) }
            }
        }
    }
}

@Composable
private fun BalanceRow(account: AccountBalance, onClick: () -> Unit) {
    val notes = buildList {
        when {
            account.balance == null -> add("No balance yet")
            account.source == "static" -> add("Fixed amount")
            else -> add((if (account.stale) "Carried from " else "As of ") + LocalDate.parse(account.asOf).format(shortDate))
        }
        if (account.contributions != null && account.marketReturn != null) {
            add("contributed ${account.contributions.money()}, " +
                "${if (account.group == "savings") "interest" else "market"} ${account.marketReturn.signedMoney()}")
        }
    }
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(account.accountName)
            Text("${account.owner} · ${notes.joinToString(" · ")}", style = MaterialTheme.typography.bodySmall,
                color = if (account.stale) ChartColors.warning else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(account.balance?.money() ?: "—", fontWeight = FontWeight.SemiBold)
    }
    HorizontalDivider()
}

private fun parseAmount(text: String): BigDecimal? =
    text.replace(",", "").removePrefix("$").trim().toBigDecimalOrNull()?.takeIf { it >= BigDecimal.ZERO && it.scale() <= 2 }

@Composable
private fun EditBalanceDialog(
    account: AccountBalance, defaultDay: LocalDate, busy: Boolean, error: String?, onDismiss: () -> Unit,
    onSave: (LocalDate, BigDecimal) -> Unit,
) {
    var amount by remember { mutableStateOf(account.balance?.takeIf { !account.stale }?.let { "%.2f".format(it) } ?: "") }
    var day by remember { mutableStateOf(defaultDay.toString()) }
    val value = parseAmount(amount)
    val date = runCatching { LocalDate.parse(day) }.getOrNull()?.takeIf { !it.isAfter(LocalDate.now()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(account.accountName) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (account.group in setOf("credit_cards", "loans")) "What's owed, as the bank shows it."
                    else "The balance the bank app shows.", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(amount, { amount = it }, label = { Text("Balance") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
                OutlinedTextField(day, { day = it }, label = { Text("On (YYYY-MM-DD)") }, singleLine = true, isError = date == null)
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && value != null && date != null, onClick = { onSave(date!!, value!!) }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun ReviewReadingDialog(
    read: BalanceRead, accounts: List<AccountBalance>, busy: Boolean, error: String?, onDismiss: () -> Unit,
    onConfirm: (LocalDate, List<BalanceBatchRow>) -> Unit,
) {
    val chosen = remember(read) { mutableStateListOf(*read.rows.map { it.accountId }.toTypedArray()) }
    var day by remember(read) { mutableStateOf(read.asOf) }
    val date = runCatching { LocalDate.parse(day) }.getOrNull()?.takeIf { !it.isAfter(LocalDate.now()) }
    val picked = chosen.filterNotNull()
    val clash = picked.size != picked.toSet().size
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Balances on the screenshot") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Check each one is matched to the right account (or skip it).", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(day, { day = it }, label = { Text("For the day (YYYY-MM-DD)") }, singleLine = true, isError = date == null)
                read.rows.forEachIndexed { i, row ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(10.dp)) {
                            Row {
                                Text(row.shownName + if (row.currency != "CAD") " (${row.currency})" else "", Modifier.weight(1f))
                                Text(row.balance.money() + if (row.owed) " owed" else "", fontWeight = FontWeight.SemiBold)
                            }
                            AccountPicker(chosen[i], accounts) { chosen[i] = it }
                        }
                    }
                }
                if (clash) Text("Two rows are matched to the same account.", color = MaterialTheme.colorScheme.error)
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            TextButton(
                enabled = !busy && date != null && picked.isNotEmpty() && !clash,
                onClick = {
                    onConfirm(date!!, read.rows.mapIndexedNotNull { i, row ->
                        chosen[i]?.let { BalanceBatchRow(it, "%.2f".format(row.balance)) }
                    })
                },
            ) { Text("Save balances") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun AccountPicker(selected: String?, accounts: List<AccountBalance>, onPick: (String?) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        Text(
            accounts.firstOrNull { it.accountId == selected }?.accountName?.let { "→ $it" } ?: "Skip (tap to pick an account)",
            color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.fillMaxWidth().clickable { open = true }.padding(vertical = 6.dp),
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Skip") }, onClick = { onPick(null); open = false })
            accounts.forEach { a ->
                DropdownMenuItem(text = { Text(a.accountName) }, onClick = { onPick(a.accountId); open = false })
            }
        }
    }
}

/** The dashboard's net worth card. */
@Composable
fun NetWorthCard(worth: Balances, onClick: () -> Unit) {
    val known = worth.accounts.count { it.balance != null && it.source != "static" }
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Net worth", style = MaterialTheme.typography.titleSmall)
                Text(
                    if (known == 0) "Add balances from a screenshot of your bank apps"
                    else listOfNotNull(
                        worth.change?.let { "${it.signedMoney()} vs last month" },
                        if (worth.contributions != 0.0 || worth.marketReturn != 0.0)
                            "contributed ${worth.contributions.money()}, market ${worth.marketReturn.signedMoney()}" else null,
                    ).joinToString(" · ").ifEmpty { "$known accounts with a balance" },
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text((if (worth.netWorth < 0) "−" else "") + abs(worth.netWorth).money(),
                style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
    }
}
