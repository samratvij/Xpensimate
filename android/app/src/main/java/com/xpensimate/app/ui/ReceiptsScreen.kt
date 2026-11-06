package com.xpensimate.app.ui

import android.app.Activity
import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.Receipt
import com.xpensimate.app.data.compressForUpload
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.HttpException
import java.time.format.DateTimeFormatter

class ReceiptsViewModel(private val api: Api) : ViewModel() {
    var list by mutableStateOf<Load<List<Receipt>>>(Load.Loading)
        private set
    var refreshing by mutableStateOf(false)
        private set
    var uploading by mutableStateOf(false)
        private set
    var uploadError by mutableStateOf<String?>(null)

    init { load() }

    fun load(pull: Boolean = false) {
        viewModelScope.launch {
            if (pull) refreshing = true
            list = runCatching { api.receipts() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
            refreshing = false
        }
    }

    fun upload(context: Context, uri: Uri, onDone: (String) -> Unit) {
        viewModelScope.launch {
            uploading = true
            uploadError = null
            runCatching {
                val jpeg = withContext(Dispatchers.IO) { compressForUpload(context, uri) }
                val part = MultipartBody.Part.createFormData(
                    "file", "receipt.jpg", jpeg.toRequestBody("image/jpeg".toMediaType())
                )
                api.uploadReceipt(part)
            }.onSuccess {
                load()
                onDone(it.id)
            }.onFailure { uploadError = it.uploadMessage() }
            uploading = false
        }
    }
}

private fun Throwable.uploadMessage(): String =
    if (this is HttpException) when (code()) {
        422 -> "That doesn't look like a receipt. Try again with the whole receipt in frame."
        415 -> "That image couldn't be read."
        502 -> "The receipt couldn't be read right now. Try again in a minute."
        503 -> "Receipt reading isn't set up on the server (no Claude API key or local model)."
        else -> friendly()
    } else friendly()

private val receiptDate = DateTimeFormatter.ofPattern("MMM d, yyyy")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReceiptsScreen(vm: ReceiptsViewModel, onOpen: (String) -> Unit) {
    val context = LocalContext.current
    val scanner = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        val page = GmsDocumentScanningResult.fromActivityResultIntent(result.data)?.pages?.firstOrNull()
        if (result.resultCode == Activity.RESULT_OK && page != null) vm.upload(context, page.imageUri, onOpen)
    }
    val startScan = {
        val options = GmsDocumentScannerOptions.Builder()
            .setGalleryImportAllowed(true)
            .setPageLimit(1)
            .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_JPEG)
            .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
            .build()
        GmsDocumentScanning.getClient(options).getStartScanIntent(context as Activity)
            .addOnSuccessListener { scanner.launch(IntentSenderRequest.Builder(it).build()) }
            .addOnFailureListener { vm.uploadError = "The scanner isn't available: ${it.message}" }
        Unit
    }

    Box(Modifier.fillMaxSize()) {
        when (val state = vm.list) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> PullToRefreshBox(isRefreshing = vm.refreshing, onRefresh = { vm.load(pull = true) }) {
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 96.dp)) {
                    if (state.value.isEmpty()) {
                        item { Text("No receipts yet. Scan one to split a purchase into its lines.", Modifier.padding(24.dp)) }
                    }
                    items(state.value, key = { it.id }) { receipt ->
                        ReceiptRow(receipt) { onOpen(receipt.id) }
                        HorizontalDivider()
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { if (!vm.uploading) startScan() },
            icon = { Icon(Icons.Filled.DocumentScanner, contentDescription = null) },
            text = { Text("Scan receipt") },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        )
    }

    if (vm.uploading) {
        AlertDialog(
            onDismissRequest = {},
            confirmButton = {},
            title = { Text("Reading receipt…") },
            text = {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    CircularProgressIndicator()
                    Text("Reading the lines. This can take up to a minute.")
                }
            },
        )
    }
    vm.uploadError?.let { message ->
        AlertDialog(
            onDismissRequest = { vm.uploadError = null },
            confirmButton = { TextButton(onClick = { vm.uploadError = null }) { Text("OK") } },
            title = { Text("Couldn't add the receipt") },
            text = { Text(message) },
        )
    }
}

@Composable
private fun ReceiptRow(receipt: Receipt, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(receipt.merchant, style = MaterialTheme.typography.bodyLarge)
            val status = when {
                receipt.matchedTransactionId != null -> "Matched"
                else -> "Waiting for the card charge"
            } + if (receipt.needsReview) " · check totals" else ""
            Text(
                "${parseInstant(receipt.timestamp).atZone(zone).format(receiptDate)} · $status",
                style = MaterialTheme.typography.bodySmall,
                color = if (receipt.needsReview) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(receipt.total.money(), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
    }
}
