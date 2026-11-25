package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SelectableDates
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.Account
import com.xpensimate.app.data.ManualTransactionIn
import com.xpensimate.app.data.Taxonomy
import java.math.BigDecimal
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

private val pickedDay = DateTimeFormatter.ofPattern("EEE, MMM d yyyy")

/**
 * Enter a purchase by hand: cash, or anything no alert, email or statement will report. Any day up to today.
 * "Auto" for what or who leaves it to the server's categorizer, as for any other charge.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddTransactionSheet(
    initialDay: LocalDate,
    accounts: List<Account>,
    taxonomy: Taxonomy?,
    saving: Boolean,
    error: String?,
    onDismiss: () -> Unit,
    onAdd: (ManualTransactionIn) -> Unit,
) {
    var amount by remember { mutableStateOf("") }
    var merchant by remember { mutableStateOf("") }
    var day by remember { mutableStateOf(initialDay) }
    var accountId by remember { mutableStateOf<String?>(null) }
    var category by remember { mutableStateOf<String?>(null) }
    var purpose by remember { mutableStateOf<String?>(null) }
    var note by remember { mutableStateOf("") }
    var pickingDay by remember { mutableStateOf(false) }
    var pickingAccount by remember { mutableStateOf(false) }
    val cents = amount.replace(",", "").removePrefix("$").toBigDecimalOrNull()?.takeIf { it > BigDecimal.ZERO && it.scale() <= 2 }
    val paidWith = accounts.filter { it.kind == "credit_card" || it.kind == "chequing" }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Add a transaction", style = MaterialTheme.typography.titleLarge)
            Text("For cash, or anything that won't show up on a statement or alert.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(amount, { amount = it }, label = { Text("Amount") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(merchant, { merchant = it }, label = { Text("Where / what") }, singleLine = true,
                placeholder = { Text("Farmers market, haircut, …") }, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { pickingDay = true }, modifier = Modifier.weight(1f)) { Text(day.format(pickedDay)) }
                Box(Modifier.weight(1f)) {
                    OutlinedButton(onClick = { pickingAccount = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(paidWith.firstOrNull { it.id == accountId }?.accountName ?: "Cash / other", maxLines = 1,
                            modifier = Modifier.weight(1f, fill = false))
                        Icon(Icons.Default.ArrowDropDown, contentDescription = null)
                    }
                    DropdownMenu(expanded = pickingAccount, onDismissRequest = { pickingAccount = false }) {
                        DropdownMenuItem(text = { Text("Cash / other") }, onClick = { accountId = null; pickingAccount = false })
                        paidWith.forEach { a ->
                            DropdownMenuItem(text = { Text(a.accountName) }, onClick = { accountId = a.id; pickingAccount = false })
                        }
                    }
                }
            }
            if (taxonomy != null) {
                Text("What", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = category == null, onClick = { category = null }, label = { Text("Auto") })
                    taxonomy.categories.forEach { c ->
                        FilterChip(selected = category == c, onClick = { category = c }, label = { Text(c) })
                    }
                }
                Text("Who", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = purpose == null, onClick = { purpose = null }, label = { Text("Auto") })
                    taxonomy.purposes.forEach { p ->
                        FilterChip(selected = purpose == p, onClick = { purpose = p }, label = { Text(p) })
                    }
                }
            }
            OutlinedTextField(note, { note = it }, label = { Text("Note (optional)") }, minLines = 2,
                modifier = Modifier.fillMaxWidth())
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Button(
                enabled = !saving && cents != null && merchant.isNotBlank(),
                onClick = {
                    onAdd(ManualTransactionIn(
                        amount = cents!!.setScale(2).toPlainString(), spentOn = day.toString(), merchant = merchant.trim(),
                        accountId = accountId, category = category, purpose = purpose, note = note.trim().ifEmpty { null },
                    ))
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (saving) "Adding…" else "Add") }
            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) { Text("Cancel") }
        }
    }

    if (pickingDay) {
        val today = LocalDate.now()
        // The date picker works in UTC midnights.
        val state = rememberDatePickerState(
            initialSelectedDateMillis = day.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
            selectableDates = object : SelectableDates {
                override fun isSelectableDate(utcTimeMillis: Long) =
                    !Instant.ofEpochMilli(utcTimeMillis).atZone(ZoneOffset.UTC).toLocalDate().isAfter(today)
                override fun isSelectableYear(year: Int) = year <= today.year
            },
        )
        DatePickerDialog(
            onDismissRequest = { pickingDay = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { day = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate() }
                    pickingDay = false
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { pickingDay = false }) { Text("Cancel") } },
        ) { DatePicker(state) }
    }
}
