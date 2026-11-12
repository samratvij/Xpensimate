package com.xpensimate.app.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.xpensimate.app.BuildConfig
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.NotificationQueue
import com.xpensimate.app.data.Push
import com.xpensimate.app.data.ServerConfig
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    config: ServerConfig,
    api: Api,
    queue: NotificationQueue,
    onChangeServer: () -> Unit,
    onCategories: () -> Unit,
    onSavings: () -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var testResult by remember { mutableStateOf<String?>(null) }
    var captureOn by remember { mutableStateOf(false) }
    var queued by remember { mutableStateOf(0) }
    // Re-check when returning from Android's notification-access screen.
    LifecycleResumeEffect(Unit) {
        captureOn = context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)
        queued = queue.size()
        onPauseOrDispose {}
    }

    val permitted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        HealthSection(api, PhoneHealth(captureOn, queued, Push.isAvailable(context), permitted))

        HorizontalDivider()
        Text("Server", style = MaterialTheme.typography.titleMedium)
        Text(config.baseUrl)
        Text("API token ••••${config.token.takeLast(4)}", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = onChangeServer) { Text("Change server or token") }

        HorizontalDivider()
        Text("Categories", style = MaterialTheme.typography.titleMedium)
        Text("Add, rename or merge what-you-spent-on categories.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = onCategories) { Text("Manage categories") }

        HorizontalDivider()
        Text("Savings target and goals", style = MaterialTheme.typography.titleMedium)
        Text("How much to move into savings each month, and goals with a date. Once set, they show on the Budget tab.",
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = onSavings) { Text("Savings target and goals") }

        HorizontalDivider()
        Text("Budget alerts", style = MaterialTheme.typography.titleMedium)
        Text(
            when {
                !Push.isAvailable(context) -> "Push isn't set up in this build (google-services.json missing)."
                !permitted -> "Notifications are turned off for Xpensimate in Android settings."
                else -> "This phone receives budget alerts as notifications."
            },
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(
            enabled = Push.isAvailable(context),
            onClick = {
                scope.launch {
                    testResult = "Sending…"
                    Push.register(context, api)
                    testResult = runCatching { api.sendTestNotification() }.fold(
                        { r -> if (r.delivered > 0) "Sent to ${r.delivered} of ${r.devices} phone(s)." else
                            "Not delivered (${r.devices} phone(s) registered). Is the server's Firebase key in place?" },
                        { it.friendly() },
                    )
                }
            },
        ) { Text("Send test notification") }
        testResult?.let { Text(it) }

        HorizontalDivider()
        Text("Wealthsimple and Google Pay purchases", style = MaterialTheme.typography.titleMedium)
        Text(
            if (captureOn) "On: Wealthsimple and Google Pay purchase notifications become transactions (a purchase that also " +
                "sends a bank email is recorded once). Other apps' notifications are ignored."
            else "Off. Turn on notification access for Xpensimate so Wealthsimple and Google Pay purchases arrive as you spend.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (queued > 0) Text("$queued waiting to reach the server (they'll send when it's reachable).")
        OutlinedButton(onClick = { context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }) {
            Text(if (captureOn) "Notification access settings" else "Turn on notification access")
        }

        HorizontalDivider()
        Text("Version ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
