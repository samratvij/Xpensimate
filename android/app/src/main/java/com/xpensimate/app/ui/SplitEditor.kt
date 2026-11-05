package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.xpensimate.app.data.SplitLine
import com.xpensimate.app.data.Taxonomy
import com.xpensimate.app.data.Transaction
import java.math.BigDecimal
import kotlin.math.roundToLong

private data class DraftLine(
    val key: Int, val name: String, val amount: String, val category: String, val purpose: String, val note: String = "",
)

/** "12.5", "$12.50" or "1,200" as cents; null if not a whole number of cents. */
internal fun String.toCents(): Long? {
    val value = trim().removePrefix("$").replace(",", "").toBigDecimalOrNull() ?: return null
    if (value.stripTrailingZeros().scale() > 2) return null
    return value.movePointRight(2).toLong()
}

internal fun Long.centsText(): String = BigDecimal.valueOf(this, 2).toPlainString()

private fun Double.toCents(): Long = (this * 100).roundToLong()

/**
 * Edit a manual split: each line gets its own what and who, and the lines must add up to the charge.
 * Starts from the existing split, or from the whole charge as one line plus an empty one.
 */
@Composable
fun SplitEditor(
    txn: Transaction,
    taxonomy: Taxonomy,
    busy: Boolean,
    error: String?,
    onSave: (List<SplitLine>) -> Unit,
    onRemove: () -> Unit,
) {
    val total = txn.amount.toCents()
    var nextKey by remember { mutableStateOf(0) }
    fun line(name: String, cents: Long?, category: String, purpose: String, note: String = "") =
        DraftLine(nextKey++, name, cents?.centsText().orEmpty(), category, purpose, note)

    val lines = remember {
        mutableStateListOf<DraftLine>().apply {
            if (txn.items.isNotEmpty()) {
                txn.items.forEach { add(line(it.skuName, it.price.toCents(), it.category, it.purpose, it.note.orEmpty())) }
            } else {
                add(line("", total, txn.category, txn.purpose))
                add(line("", null, txn.category, txn.purpose))
            }
        }
    }

    val parsed = lines.map { it.amount.toCents() }
    val assigned = parsed.sumOf { it ?: 0L }
    val left = total - assigned
    val problem = when {
        lines.size < 2 -> "Add at least two lines"
        lines.any { it.name.isBlank() } -> "Give every line a name"
        parsed.any { it == null || it == 0L } -> "Give every line an amount"
        else -> null // an unbalanced total is shown by the "left to assign" line
    }
    val canSave = !busy && problem == null && left == 0L

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Split ${txn.amount.money()}", style = MaterialTheme.typography.titleLarge)
        Text("Give each item its own what and who. The lines must add up to the charge.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

        lines.forEachIndexed { index, draft ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = draft.name, onValueChange = { lines[index] = draft.copy(name = it) },
                            label = { Text("Item") }, singleLine = true, modifier = Modifier.weight(1f),
                        )
                        OutlinedTextField(
                            value = draft.amount, onValueChange = { lines[index] = draft.copy(amount = it) },
                            label = { Text("Amount") }, singleLine = true, modifier = Modifier.weight(0.6f),
                            isError = draft.amount.isNotBlank() && draft.amount.toCents() == null,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        )
                        IconButton(onClick = { lines.removeAt(index) }, enabled = lines.size > 1) {
                            Icon(Icons.Filled.Close, contentDescription = "Remove line")
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Picker("What", draft.category, taxonomy.categories, Modifier.weight(1f)) {
                            lines[index] = draft.copy(category = it)
                        }
                        Picker("Who", draft.purpose, taxonomy.purposes, Modifier.weight(1f)) {
                            lines[index] = draft.copy(purpose = it)
                        }
                    }
                    OutlinedTextField(
                        value = draft.note,
                        onValueChange = { if (it.length <= NOTE_MAX) lines[index] = draft.copy(note = it) },
                        label = { Text("Note (optional)") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = {
                val last = lines.lastOrNull()
                lines.add(line("", left.takeIf { it > 0 }, last?.category ?: txn.category, last?.purpose ?: txn.purpose))
            }) {
                Icon(Icons.Filled.Add, contentDescription = null)
                Text("Add line")
            }
            Text(
                when {
                    left == 0L -> "All assigned"
                    left > 0 -> "${BigDecimal.valueOf(left, 2).toDouble().money()} left to assign"
                    else -> "${BigDecimal.valueOf(-left, 2).toDouble().money()} too much"
                },
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = if (left == 0L) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            )
        }
        problem?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

        Button(
            onClick = {
                onSave(lines.map {
                    SplitLine(it.name.trim(), it.amount.toCents()!!.centsText(), it.category, it.purpose,
                        it.note.trim().ifBlank { null })
                })
            },
            enabled = canSave,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Save split") }
        if (txn.items.isNotEmpty()) {
            TextButton(onClick = onRemove, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                Text("Remove split", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Picker(label: String, value: String, options: List<String>, modifier: Modifier, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = open, onExpandedChange = { open = it }, modifier = modifier) {
        OutlinedTextField(
            value = value, onValueChange = {}, readOnly = true, singleLine = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = open) },
            modifier = Modifier.menuAnchor(MenuAnchorType.PrimaryNotEditable).fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { option ->
                DropdownMenuItem(text = { Text(option) }, onClick = { onPick(option); open = false })
            }
        }
    }
}
