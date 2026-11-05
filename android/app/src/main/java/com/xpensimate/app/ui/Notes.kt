package com.xpensimate.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow

/** Same limit as the server (schemas.NOTE_MAX). */
const val NOTE_MAX = 1000
const val NOTE_HINT = "Where exactly, what for, who it was for… Helps you remember and plan later."

/**
 * A free-text note with its own Save button, shown once the text differs from what's saved.
 * onSave gets the trimmed text; "" removes the note.
 */
@Composable
fun NoteEditor(
    saved: String?,
    busy: Boolean,
    onSave: (String) -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = NOTE_HINT,
) {
    var text by remember(saved) { mutableStateOf(saved.orEmpty()) }
    val dirty = text.trim() != saved.orEmpty().trim()
    Column(modifier) {
        OutlinedTextField(
            value = text,
            onValueChange = { if (it.length <= NOTE_MAX) text = it },
            placeholder = { Text(placeholder) },
            minLines = 2,
            maxLines = 6,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            modifier = Modifier.fillMaxWidth(),
        )
        if (dirty) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { text = saved.orEmpty() }, enabled = !busy) { Text("Cancel") }
                TextButton(onClick = { onSave(text.trim()) }, enabled = !busy) { Text("Save note") }
            }
        }
    }
}

/** A note shown under a row's subtitle in lists (one line). */
@Composable
fun NoteLine(note: String?, maxLines: Int = 1) {
    if (note.isNullOrBlank()) return
    Text(
        "📝 $note",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.75f),
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}
