package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.Insight

private const val SHOWN = 3

/** What to do about this month, most pressing first. Hidden when the server has nothing to say. */
@Composable
fun InsightsSection(
    insights: List<Insight>,
    onOpen: (Insight) -> Unit,
    onTrack: (Insight) -> Unit,
    onRemoveHold: (Insight) -> Unit,
    onDismiss: (Insight) -> Unit,
) {
    if (insights.isEmpty()) return
    var expanded by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Worth a look", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(start = 4.dp))
        (if (expanded) insights else insights.take(SHOWN)).forEach { insight ->
            InsightCard(insight, { onOpen(insight) }, { onTrack(insight) }, { onRemoveHold(insight) }, { onDismiss(insight) })
        }
        if (insights.size > SHOWN) TextButton(onClick = { expanded = !expanded }) {
            Text(if (expanded) "Show fewer" else "Show ${insights.size - SHOWN} more")
        }
    }
}

@Composable
private fun InsightCard(
    insight: Insight,
    onOpen: () -> Unit,
    onTrack: () -> Unit,
    onRemoveHold: () -> Unit,
    onDismiss: () -> Unit,
) {
    val (icon, tint) = when (insight.severity) {
        "alert" -> Icons.Filled.Error to ChartColors.critical
        "warn" -> Icons.Filled.Warning to ChartColors.warning
        else -> Icons.Filled.Lightbulb to MaterialTheme.colorScheme.primary
    }
    val tracking = insight.action == "track_recurring"
    Card(onClick = if (tracking) onTrack else onOpen, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 16.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Icon(icon, contentDescription = insight.severity, tint = tint, modifier = Modifier.padding(top = 2.dp).size(20.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(insight.title, style = MaterialTheme.typography.titleSmall)
                Text(insight.body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (tracking) TextButton(onClick = onTrack, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
                    Text("Track as a bill")
                }
                if (insight.action == "remove_hold") TextButton(onClick = onRemoveHold,
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
                    Text("Not a charge")
                }
            }
            IconButton(onClick = onDismiss, modifier = Modifier.size(36.dp)) {
                Icon(Icons.Filled.Close, contentDescription = "Dismiss", modifier = Modifier.size(18.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
