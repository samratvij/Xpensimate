package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.DEFAULT_SERVER_URL
import com.xpensimate.app.data.ServerConfig
import com.xpensimate.app.data.createApi
import kotlinx.coroutines.launch

/** First run (or after "Change server"): enter the server address and API token, verified before saving. */
@Composable
fun SetupScreen(initial: ServerConfig?, onSave: suspend (ServerConfig) -> Unit) {
    var url by remember { mutableStateOf(initial?.baseUrl ?: DEFAULT_SERVER_URL) }
    var token by remember { mutableStateOf(initial?.token ?: "") }
    var showToken by remember { mutableStateOf(false) }
    var checking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Column(
        Modifier.fillMaxSize().safeDrawingPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Connect to your server", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Use your Xpensimate server's address (http://<server>.<tailnet>.ts.net:8000 on a Tailscale " +
                "network, or https://<your-domain> on a VPS) and the API_TOKEN from the server's backend/.env file.",
            style = MaterialTheme.typography.bodyMedium,
        )
        OutlinedTextField(
            value = url, onValueChange = { url = it }, label = { Text("Server address") }, singleLine = true,
            placeholder = { Text("http://myserver.tail1234.ts.net:8000") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = token, onValueChange = { token = it }, label = { Text("API token") }, singleLine = true,
            visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            trailingIcon = { TextButton(onClick = { showToken = !showToken }) { Text(if (showToken) "Hide" else "Show") } },
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(
            enabled = !checking && url.isNotBlank() && token.isNotBlank(),
            onClick = {
                scope.launch {
                    checking = true
                    error = null
                    val config = ServerConfig(url.trim(), token.trim())
                    runCatching { createApi(config).accounts() }
                        .onSuccess { onSave(config) }
                        .onFailure { error = it.friendly() }
                    checking = false
                }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (checking) "Checking…" else "Connect") }
    }
}
