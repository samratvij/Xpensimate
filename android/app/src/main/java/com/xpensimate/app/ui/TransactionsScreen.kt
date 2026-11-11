package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.HorizontalDivider
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.Account
import com.xpensimate.app.data.Transaction
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter

class TransactionsViewModel(private val api: Api) : ViewModel() {
    var month by mutableStateOf(YearMonth.now())
        private set
    var list by mutableStateOf<Load<List<Transaction>>>(Load.Loading)
        private set
    var refreshing by mutableStateOf(false)
        private set
    var accounts by mutableStateOf<List<Account>>(emptyList())
        private set

    // Filters and order; kept across month changes so you can flip through months with the same view.
    var category by mutableStateOf<String?>(null)
    var budgetLine by mutableStateOf<String?>(null)
    var who by mutableStateOf<String?>(null)
    var account by mutableStateOf<String?>(null)
    var sort by mutableStateOf(Sort.Newest)

    val filtering: Boolean get() = category != null || budgetLine != null || who != null || account != null

    fun clearFilters() { category = null; budgetLine = null; who = null; account = null }

    init {
        load()
        viewModelScope.launch { runCatching { api.accounts() }.onSuccess { accounts = it } }
    }

    fun selectMonth(value: YearMonth) { month = value; list = Load.Loading; load() }

    fun load(pull: Boolean = false) {
        viewModelScope.launch {
            if (pull) refreshing = true else if (list !is Load.Ok) list = Load.Loading
            list = runCatching { api.transactions(start = month.startUtc(), end = month.endUtc(), limit = 500) }
                .fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            refreshing = false
        }
    }
}

val dayHeader: DateTimeFormatter = DateTimeFormatter.ofPattern("EEEE, MMM d")
private val shortDay: DateTimeFormatter = DateTimeFormatter.ofPattern("MMM d")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TransactionsScreen(vm: TransactionsViewModel, onOpen: (String) -> Unit) {
    // Reload when coming back from an edit screen (budgets, a transaction), keeping what's shown meanwhile.
    LaunchedEffect(Unit) { vm.load(pull = false) }
    Column(Modifier.fillMaxSize()) {
        MonthBar(vm.month, vm::selectMonth, Modifier.padding(horizontal = 8.dp))
        when (val state = vm.list) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> {
                val all = state.value
                FilterBar(vm, all)
                val shown = all.mapNotNull { txn -> vm.matchedAmount(txn)?.let { txn to it } }
                if (vm.filtering) {
                    val spent = shown.filter { !it.first.excluded }.sumOf { it.second }
                    Text("${shown.size} of ${all.size} · ${spent.money()}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
                }
                PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                        if (all.isEmpty()) item { Text("No transactions this month.", Modifier.padding(24.dp)) }
                        else if (shown.isEmpty()) item { Text("Nothing matches these filters.", Modifier.padding(24.dp)) }
                        when (vm.sort) {
                            Sort.Newest, Sort.Oldest -> {
                                val byDay = shown.groupBy { LocalDate.ofInstant(parseInstant(it.first.timestamp), zone) }
                                    .let { if (vm.sort == Sort.Oldest) it.toSortedMap() else it.toSortedMap(compareByDescending { d -> d }) }
                                byDay.forEach { (day, rows) ->
                                    item(key = day.toString()) {
                                        val total = rows.filter { !it.first.excluded }.sumOf { it.second }
                                        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp)) {
                                            Text(day.format(dayHeader), style = MaterialTheme.typography.labelLarge,
                                                color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
                                            Text(total.money(), style = MaterialTheme.typography.labelLarge,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                    }
                                    val ordered = if (vm.sort == Sort.Oldest) rows.reversed() else rows
                                    items(ordered, key = { it.first.id }) { (txn, _) ->
                                        TransactionRow(txn) { onOpen(txn.id) }
                                    }
                                }
                            }
                            Sort.Highest, Sort.Lowest -> {
                                val ordered = if (vm.sort == Sort.Highest) shown.sortedByDescending { it.second }
                                    else shown.sortedBy { it.second }
                                items(ordered, key = { it.first.id }) { (txn, _) ->
                                    TransactionRow(txn, showDate = true) { onOpen(txn.id) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

enum class Sort(val label: String) {
    Newest("Newest first"), Oldest("Oldest first"), Highest("Amount: high to low"), Lowest("Amount: low to high")
}

/**
 * How much of [txn] the current filters cover, or null if it doesn't match. An itemized charge
 * matches when any of its lines does (a Costco run with one Groceries line shows under Groceries),
 * and then only those lines count toward the totals and the amount order.
 */
fun TransactionsViewModel.matchedAmount(txn: Transaction): Double? {
    if (account != null && txn.accountId != account) return null
    if (category == null && budgetLine == null && who == null) return txn.amount
    fun fits(cat: String, line: String, purpose: String) =
        (category == null || cat == category) && (budgetLine == null || line == budgetLine) && (who == null || purpose == who)
    if (txn.isItemized && txn.items.isNotEmpty()) {
        val lines = txn.items.filter { fits(it.category, it.budgetLine, it.purpose) }
        return if (lines.isEmpty()) null else lines.sumOf { it.price }
    }
    return if (fits(txn.category, txn.budgetLine, txn.purpose)) txn.amount else null
}

@Composable
private fun FilterBar(vm: TransactionsViewModel, txns: List<Transaction>) {
    // Offer only values that occur this month (lines of itemized charges included), plus whatever is
    // already picked so it can be cleared after a month change.
    fun values(own: (Transaction) -> String, item: (com.xpensimate.app.data.TransactionItem) -> String, picked: String?) =
        (txns.flatMap { t -> if (t.isItemized && t.items.isNotEmpty()) t.items.map(item) else listOf(own(t)) } + listOfNotNull(picked))
            .filter { it.isNotBlank() }.distinct().sorted()
    val categories = values({ it.category }, { it.category }, vm.category)
    val lines = values({ it.budgetLine }, { it.budgetLine }, vm.budgetLine)
    val people = values({ it.purpose }, { it.purpose }, vm.who)
    val accountIds = txns.mapNotNull { it.accountId }.toSet() + listOfNotNull(vm.account)
    val accounts = vm.accounts.filter { it.id in accountIds }.associate { it.id to it.accountName }

    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PickerChip("Sort", vm.sort.takeIf { it != Sort.Newest }?.label, Sort.entries.map { it.name to it.label },
            allLabel = null) { vm.sort = it?.let(Sort::valueOf) ?: Sort.Newest }
        PickerChip("Who", vm.who, people.map { it to it }) { vm.who = it }
        PickerChip("Budget line", vm.budgetLine, lines.map { it to it }) { vm.budgetLine = it }
        PickerChip("Category", vm.category, categories.map { it to it }) { vm.category = it }
        if (accounts.size > 1 || vm.account != null) {
            PickerChip("Account", vm.account?.let { accounts[it] ?: "Account" }, accounts.toList()) { vm.account = it }
        }
        if (vm.filtering) {
            FilterChip(selected = false, onClick = vm::clearFilters, label = { Text("Clear") },
                leadingIcon = { Icon(Icons.Default.Close, contentDescription = null) })
        }
    }
}

/** A chip that opens a menu; shows the picked value (or [title]) and is highlighted while something is picked. */
@Composable
private fun PickerChip(
    title: String,
    selectedLabel: String?,
    options: List<Pair<String, String>>,
    allLabel: String? = "All",
    onPick: (String?) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box {
        FilterChip(
            selected = selectedLabel != null,
            onClick = { open = true },
            label = { Text(selectedLabel ?: title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            trailingIcon = { Icon(Icons.Default.ArrowDropDown, contentDescription = null) },
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            if (allLabel != null) {
                DropdownMenuItem(text = { Text(allLabel) }, onClick = { onPick(null); open = false })
                HorizontalDivider()
            }
            options.forEach { (value, label) ->
                DropdownMenuItem(
                    text = { Text(label, fontWeight = if (label == selectedLabel) FontWeight.SemiBold else null) },
                    onClick = { onPick(value); open = false },
                )
            }
        }
    }
}

/** "PC Money Account → PC Savings"; an unknown side reads as own money or someone in the household. */
fun Transaction.moveText(): String =
    "${AccountNames.of(accountId) ?: "Own money"} → ${AccountNames.of(counterpartAccountId) ?: "Someone in the household"}"

@Composable
fun TransactionRow(txn: Transaction, showDate: Boolean = false, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp)
            .alpha(if (txn.excluded && !txn.isIncome) 0.5f else 1f),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CategoryBadge(if (txn.excluded && !txn.isIncome) "" else txn.category)
        Column(Modifier.weight(1f)) {
            Text(txn.merchant, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val detail = when {
                txn.isIncome -> "${txn.category} · ${txn.purpose}"
                txn.isTransfer -> "Moved: ${txn.moveText()}"
                txn.excluded -> "Not an expense"
                txn.isItemized -> "${txn.budgetLine} · itemized"
                else -> txn.budgetLine
            }
            Text(
                if (showDate) "${LocalDate.ofInstant(parseInstant(txn.timestamp), zone).format(shortDay)} · $detail" else detail,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            NoteLine(txn.note)
        }
        if (txn.needsCheck) {
            SuggestionChip(
                onClick = onClick,
                label = { Text("Check") },
                colors = SuggestionChipDefaults.suggestionChipColors(labelColor = MaterialTheme.colorScheme.error),
            )
        }
        Text(
            if (txn.isIncome || txn.amount < 0) "+${kotlin.math.abs(txn.amount).money()}" else txn.amount.money(),
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
            textDecoration = if (txn.excluded && !txn.isIncome) TextDecoration.LineThrough else null,
            color = when {
                txn.isIncome -> ChartColors.good
                txn.amount < 0 -> MaterialTheme.colorScheme.primary
                else -> MaterialTheme.colorScheme.onSurface
            },
        )
    }
}
