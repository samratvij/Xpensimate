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

    init { load() }

    fun selectMonth(value: YearMonth) { month = value; list = Load.Loading; load() }

    fun load(pull: Boolean = false) {
        viewModelScope.launch {
            if (pull) refreshing = true else if (list !is Load.Ok) list = Load.Loading
            list = runCatching { api.transactions(start = month.startUtc(), end = month.endUtc()) }
                .fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            refreshing = false
        }
    }
}

val dayHeader: DateTimeFormatter = DateTimeFormatter.ofPattern("EEEE, MMM d")

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
            is Load.Ok -> PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                val byDay = state.value.groupBy { LocalDate.ofInstant(parseInstant(it.timestamp), zone) }
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                    if (byDay.isEmpty()) item { Text("No transactions this month.", Modifier.padding(24.dp)) }
                    byDay.forEach { (day, txns) ->
                        item(key = day.toString()) {
                            val total = txns.filter { !it.excluded }.sumOf { it.amount }
                            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp)) {
                                Text(day.format(dayHeader), style = MaterialTheme.typography.labelLarge,
                                    color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
                                Text(total.money(), style = MaterialTheme.typography.labelLarge,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        items(txns, key = { it.id }) { txn ->
                            TransactionRow(txn) { onOpen(txn.id) }
                                        }
                    }
                }
            }
        }
    }
}

@Composable
fun TransactionRow(txn: Transaction, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp)
            .alpha(if (txn.excluded) 0.5f else 1f),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CategoryBadge(if (txn.excluded) "" else txn.category)
        Column(Modifier.weight(1f)) {
            Text(txn.merchant, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                when {
                    txn.excluded -> "Not an expense"
                    txn.isItemized -> "${txn.budgetLine} · itemized"
                    else -> txn.budgetLine
                },
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
            if (txn.amount < 0) "+${(-txn.amount).money()}" else txn.amount.money(),
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
            textDecoration = if (txn.excluded) TextDecoration.LineThrough else null,
            color = if (txn.amount < 0) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
        )
    }
}
