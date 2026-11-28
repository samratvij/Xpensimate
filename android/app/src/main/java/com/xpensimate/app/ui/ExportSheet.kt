package com.xpensimate.app.ui

import android.content.ActivityNotFoundException
import android.content.ContentResolver
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.Account
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** What to export: both days included. format is "pdf" or "csv"; accountId null means every account. */
data class ExportRequest(val start: LocalDate, val end: LocalDate, val format: String, val accountId: String?) {
    val fileName: String get() = "xpensimate_${start}_to_$end.$format"
    val mime: String get() = if (format == "pdf") "application/pdf" else "text/csv"
}

private val exportDay = DateTimeFormatter.ofPattern("MMM d, yyyy")

/**
 * Export every transaction for a month or any date range, like a bank statement: a PDF to read or share, or a CSV
 * for a spreadsheet. The person picks where to save it (Downloads, Drive, …), then it opens.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ExportSheet(
    initialMonth: YearMonth,
    accounts: List<Account>,
    exporting: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onExport: (ExportRequest, Uri, ContentResolver, () -> Unit) -> Unit,
) {
    val context = LocalContext.current
    val today = LocalDate.now()
    var byMonth by remember { mutableStateOf(true) }
    var month by remember { mutableStateOf(initialMonth) }
    var from by remember { mutableStateOf(initialMonth.atDay(1)) }
    var to by remember { mutableStateOf(minOf(initialMonth.atEndOfMonth(), today)) }
    var format by remember { mutableStateOf("pdf") }
    var accountId by remember { mutableStateOf<String?>(null) }
    var pickingAccount by remember { mutableStateOf(false) }
    var pickingDay by remember { mutableStateOf<String?>(null) }  // "from" | "to"

    val request = if (byMonth) ExportRequest(month.atDay(1), month.atEndOfMonth(), format, accountId)
        else ExportRequest(from, to, format, accountId)
    val valid = !request.end.isBefore(request.start)

    fun saved(uri: Uri) {
        Toast.makeText(context, "Saved ${request.fileName}", Toast.LENGTH_SHORT).show()
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, request.mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        try { context.startActivity(view) } catch (_: ActivityNotFoundException) { /* saved; nothing installed opens it */ }
    }
    // The save dialog's file type is fixed per launcher, so one each.
    val savePdf = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/pdf")) { uri ->
        uri?.let { onExport(request, it, context.contentResolver) { saved(it) } }
    }
    val saveCsv = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        uri?.let { onExport(request, it, context.contentResolver) { saved(it) } }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Export a statement", style = MaterialTheme.typography.titleLarge)
            Text("Every transaction in the period, oldest first, with debits, credits, categories and notes.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Segments(listOf("Month", "Date range"), if (byMonth) 0 else 1) { byMonth = it == 0 }
            if (byMonth) {
                MonthBar(month, { if (!it.isAfter(YearMonth.now())) month = it })
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { pickingDay = "from" }, modifier = Modifier.weight(1f)) {
                        Text("From ${from.format(exportDay)}", maxLines = 1)
                    }
                    OutlinedButton(onClick = { pickingDay = "to" }, modifier = Modifier.weight(1f)) {
                        Text("To ${to.format(exportDay)}", maxLines = 1)
                    }
                }
            }
            Box {
                OutlinedButton(onClick = { pickingAccount = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(accounts.firstOrNull { it.id == accountId }?.accountName ?: "All accounts", maxLines = 1,
                        modifier = Modifier.weight(1f, fill = false))
                    Icon(Icons.Default.ArrowDropDown, contentDescription = null)
                }
                DropdownMenu(expanded = pickingAccount, onDismissRequest = { pickingAccount = false }) {
                    DropdownMenuItem(text = { Text("All accounts") }, onClick = { accountId = null; pickingAccount = false })
                    accounts.forEach { a ->
                        DropdownMenuItem(text = { Text(a.accountName) }, onClick = { accountId = a.id; pickingAccount = false })
                    }
                }
            }
            Segments(listOf("PDF statement", "CSV (spreadsheet)"), if (format == "pdf") 0 else 1) {
                format = if (it == 0) "pdf" else "csv"
            }
            if (!valid) Text("The end date is before the start date.", color = MaterialTheme.colorScheme.error)
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Button(
                enabled = valid && !exporting,
                onClick = { (if (format == "pdf") savePdf else saveCsv).launch(request.fileName) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (exporting) "Preparing…" else "Save…") }
            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) { Text("Cancel") }
        }
    }

    pickingDay?.let { which ->
        val initial = if (which == "from") from else to
        // The date picker works in UTC midnights.
        val state = rememberDatePickerState(
            initialSelectedDateMillis = initial.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
            selectableDates = object : SelectableDates {
                override fun isSelectableDate(utcTimeMillis: Long) =
                    !Instant.ofEpochMilli(utcTimeMillis).atZone(ZoneOffset.UTC).toLocalDate().isAfter(today)
                override fun isSelectableYear(year: Int) = year <= today.year
            },
        )
        DatePickerDialog(
            onDismissRequest = { pickingDay = null },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let {
                        val day = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate()
                        if (which == "from") from = day else to = day
                    }
                    pickingDay = null
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { pickingDay = null }) { Text("Cancel") } },
        ) { DatePicker(state) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Segments(labels: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        labels.forEachIndexed { i, label ->
            SegmentedButton(selected = i == selected, onClick = { onSelect(i) },
                shape = SegmentedButtonDefaults.itemShape(i, labels.size)) { Text(label, maxLines = 1) }
        }
    }
}
