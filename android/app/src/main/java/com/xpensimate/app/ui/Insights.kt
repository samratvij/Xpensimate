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
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.Insight

/**
 * What to do about this month, folded into one row (the top item and a count) until tapped.
 * Hidden when the server has nothing to say.
 */
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
    val worst = insights.minByOrNull { SEVERITY_ORDER.indexOf(it.severity).let { i -> if (i < 0) SEVERITY_ORDER.size else i } }!!
    val (icon, tint) = severityIcon(worst.severity)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Card(onClick = { expanded = !expanded }, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(20.dp))
                Text(
                    if (expanded) "Worth a look" else worst.title,
                    style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (!expanded && insights.size > 1) Text("+${insights.size - 1}", style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary)
                Icon(if (expanded) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
                    contentDescription = if (expanded) "Hide" else "Show all", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (expanded) insights.forEach { insight ->
            InsightCard(insight, { onOpen(insight) }, { onTrack(insight) }, { onRemoveHold(insight) }, { onDismiss(insight) })
        }
    }
}

private val SEVERITY_ORDER = listOf("alert", "warn")

@Composable
private fun severityIcon(severity: String) = when (severity) {
    "alert" -> Icons.Filled.Error to ChartColors.critical
    "warn" -> Icons.Filled.Warning to ChartColors.warning
    else -> Icons.Filled.Lightbulb to MaterialTheme.colorScheme.primary
}

@Composable
private fun InsightCard(
    insight: Insight,
    onOpen: () -> Unit,
    onTrack: () -> Unit,
    onRemoveHold: () -> Unit,
    onDismiss: () -> Unit,
) {
    val (icon, tint) = severityIcon(insight.severity)
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
