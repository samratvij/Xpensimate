package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.BudgetAlert
import kotlinx.coroutines.launch
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

class AlertsViewModel(private val api: Api) : ViewModel() {
    var alerts by mutableStateOf<Load<List<BudgetAlert>>>(Load.Loading)
        private set

    init { load() }

    fun load() {
        viewModelScope.launch {
            alerts = runCatching { api.alerts() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }
}

private val alertTime = DateTimeFormatter.ofPattern("MMM d, h:mm a")

@Composable
fun AlertsScreen(vm: AlertsViewModel, onOpenTransaction: (String) -> Unit) {
    when (val state = vm.alerts) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> LazyColumn(Modifier.fillMaxSize()) {
            if (state.value.isEmpty()) {
                item { Text("No budget alerts yet. Set budgets per budget line to get warned as you near them.", Modifier.padding(24.dp)) }
            }
            items(state.value, key = { it.id }) { alert ->
                AlertRow(alert, onClick = alert.transactionId?.let { id -> { onOpenTransaction(id) } })
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun AlertRow(alert: BudgetAlert, onClick: (() -> Unit)?) {
    val exceeded = alert.level == "EXCEEDED"
    val saving = alert.level == "BEHIND" || alert.level == "SHORT"
    val color = if (exceeded) MaterialTheme.colorScheme.error else Color(0xFFE08A00)
    Row(
        Modifier.fillMaxWidth().then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Icon(if (exceeded) Icons.Filled.Error else Icons.Filled.Warning, contentDescription = null, tint = color)
        Column(Modifier.weight(1f)) {
            val percent = (alert.spent / alert.monthlyLimit * 100).roundToInt()
            Text(when {
                alert.level == "BEHIND" -> "Behind on savings: $percent% of the target"
                alert.level == "SHORT" -> "${java.time.YearMonth.parse(alert.month).title()} savings finished short"
                exceeded -> "Over budget: ${alert.budgetLine}"
                else -> "${alert.budgetLine} at $percent%"
            },
                style = MaterialTheme.typography.bodyLarge)
            Text(
                "${alert.spent.money()}${if (saving) " moved" else ""} of ${alert.monthlyLimit.money()} · " +
                    parseInstant(alert.createdAt).atZone(zone).format(alertTime),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
