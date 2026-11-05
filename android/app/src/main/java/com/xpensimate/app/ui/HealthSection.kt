package com.xpensimate.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.HealthCheck
import com.xpensimate.app.data.HealthReport
import retrofit2.HttpException
import kotlin.system.measureTimeMillis

/** What this phone knows about itself, checked locally (no server needed). */
data class PhoneHealth(val captureOn: Boolean, val queued: Int, val pushAvailable: Boolean, val pushPermitted: Boolean)

private sealed interface ServerHealth {
    data object Checking : ServerHealth
    data class Reached(val millis: Long, val checks: List<HealthCheck>) : ServerHealth
    data class Unreachable(val check: HealthCheck) : ServerHealth
}

/** Health at a glance: is the server reachable, and is each part of the pipeline (Gmail, Claude, push, ...) working? */
@Composable
fun HealthSection(api: Api, phone: PhoneHealth) {
    var refresh by remember { mutableIntStateOf(0) }
    var expanded by rememberSaveable { mutableStateOf(false) }
    var server by remember { mutableStateOf<ServerHealth>(ServerHealth.Checking) }
    LaunchedEffect(refresh) {
        server = ServerHealth.Checking
        server = try {
            lateinit var report: HealthReport
            val millis = measureTimeMillis { report = api.health() }
            ServerHealth.Reached(millis, report.checks)
        } catch (e: HttpException) {
            ServerHealth.Unreachable(
                if (e.code() == 404) HealthCheck("Server", "warning", "Reachable, but too old to report health. Update the backend.")
                else HealthCheck("Server", "error", e.friendly())
            )
        } catch (e: Exception) {
            ServerHealth.Unreachable(HealthCheck("Server", "error", e.friendly()))
        }
    }

    val checks = buildList {
        when (val s = server) {
            ServerHealth.Checking -> add(HealthCheck("Server", "checking", "Checking…"))
            is ServerHealth.Reached -> {
                add(HealthCheck("Server", "ok", "Connected; answered in ${s.millis} ms."))
                addAll(s.checks)
            }
            is ServerHealth.Unreachable -> add(s.check)
        }
        add(
            HealthCheck(
                "Purchase capture on this phone",
                if (phone.captureOn) "ok" else "off",
                if (phone.captureOn) "Notification access is on." else "Notification access is off.",
            )
        )
        if (phone.queued > 0) {
            add(HealthCheck("Waiting to send", "warning", "${phone.queued} purchase(s) queued until the server is reachable."))
        }
        add(
            when {
                !phone.pushAvailable -> HealthCheck("Alerts on this phone", "off", "Push isn't in this build.")
                !phone.pushPermitted -> HealthCheck("Alerts on this phone", "warning", "Notifications are blocked in Android settings.")
                else -> HealthCheck("Alerts on this phone", "ok", "Allowed.")
            }
        )
    }
    val worst = when {
        checks.any { it.status == "checking" } -> null
        checks.any { it.status == "error" } -> "error"
        checks.any { it.status == "warning" } -> "warning"
        else -> "ok"
    }

    val problems = checks.filter { it.status == "error" || it.status == "warning" }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.clickable { expanded = !expanded },
        ) {
            Box(Modifier.size(10.dp).background(worst?.let { statusColor(it) } ?: MaterialTheme.colorScheme.outline, CircleShape))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text("Health", style = MaterialTheme.typography.titleMedium)
                Text(
                    when (worst) {
                        null -> "Checking…"
                        "error" -> "Something needs attention"
                        "warning" -> "Working, with warnings"
                        else -> "All good"
                    },
                    color = worst?.let { statusColor(it) } ?: MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.labelLarge,
                )
                // Collapsed, still say what's wrong so it isn't hidden.
                if (!expanded && problems.isNotEmpty()) {
                    Text(problems.joinToString(" · ") { it.name }, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (expanded) {
                TextButton(onClick = { refresh++ }, enabled = server != ServerHealth.Checking) { Text("Refresh") }
            }
            Icon(
                if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                contentDescription = if (expanded) "Hide health details" else "Show health details",
            )
        }
        AnimatedVisibility(expanded) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                checks.forEach { HealthRow(it) }
            }
        }
    }
}

@Composable
private fun HealthRow(check: HealthCheck) {
    Row(verticalAlignment = Alignment.Top) {
        Box(Modifier.padding(top = 6.dp).size(10.dp).background(statusColor(check.status), CircleShape))
        Spacer(Modifier.width(10.dp))
        Column {
            Text(check.name, style = MaterialTheme.typography.bodyMedium)
            Text(check.detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun statusColor(status: String): Color = when (status) {
    "ok" -> ChartColors.good
    "warning" -> ChartColors.warning
    "error" -> ChartColors.critical
    else -> MaterialTheme.colorScheme.outline
}
