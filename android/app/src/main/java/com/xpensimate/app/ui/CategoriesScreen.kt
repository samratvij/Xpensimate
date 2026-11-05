package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.CategoryInfo
import com.xpensimate.app.data.CategoryNameBody
import kotlinx.coroutines.launch

class CategoriesViewModel(private val api: Api) : ViewModel() {
    var categories by mutableStateOf<Load<List<CategoryInfo>>>(Load.Loading)
        private set
    var error by mutableStateOf<String?>(null)
        private set
    var purposes by mutableStateOf<List<String>>(emptyList())
        private set

    init {
        load()
        viewModelScope.launch { runCatching { api.taxonomy() }.onSuccess { purposes = it.purposes } }
    }

    fun load() {
        viewModelScope.launch {
            categories = runCatching { api.categories() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun add(name: String) = act { api.addCategory(CategoryNameBody(name)) }
    fun rename(from: String, to: String) = act { api.renameCategory(from, CategoryNameBody(to)) }
    fun merge(from: String, into: String) = act { api.mergeCategory(from, into) }

    private fun act(block: suspend () -> Any) {
        viewModelScope.launch {
            error = null
            runCatching { block() }.onSuccess { load() }.onFailure { error = it.categoryError() }
        }
    }
}

private fun Throwable.categoryError(): String =
    if (this is retrofit2.HttpException && code() == 409) "There's already a category with that name."
    else if (this is retrofit2.HttpException && code() == 422) "That name can't be used (it may be a person's budget line or contain '/')."
    else friendly()

private sealed interface Editing {
    data object Add : Editing
    data class Existing(val category: CategoryInfo) : Editing
}

@Composable
fun CategoriesScreen(vm: CategoriesViewModel) {
    var editing by remember { mutableStateOf<Editing?>(null) }
    Box(Modifier.fillMaxSize()) {
        when (val state = vm.categories) {
            Load.Loading -> LoadingView()
            is Load.Failed -> ErrorView(state.message) { vm.load() }
            is Load.Ok -> LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 96.dp)) {
                item {
                    Column(Modifier.padding(16.dp)) {
                        Text("What money is spent on. Who it was for" +
                            (if (vm.purposes.isEmpty()) "" else " (${vm.purposes.joinToString()})") + " stays fixed.",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 8.dp)) }
                    }
                }
                items(state.value, key = { it.name }) { c ->
                    Row(
                        Modifier.fillMaxWidth().clickable { editing = Editing.Existing(c) }.padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        CategoryBadge(c.name)
                        Column(Modifier.weight(1f)) {
                            Text(c.name)
                            Text(
                                "${c.transactions} transaction${if (c.transactions == 1) "" else "s"}" +
                                    (c.monthlyLimit?.let { " · budget ${it.money()}" } ?: ""),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    HorizontalDivider()
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { editing = Editing.Add },
            icon = { Icon(Icons.Filled.Add, contentDescription = null) },
            text = { Text("Add category") },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        )
    }

    val all = (vm.categories as? Load.Ok)?.value.orEmpty()
    when (val e = editing) {
        Editing.Add -> NameDialog("New category", "", onDismiss = { editing = null }) { vm.add(it); editing = null }
        is Editing.Existing -> EditDialog(e.category, all.map { it.name }.filter { it != e.category.name },
            onDismiss = { editing = null },
            onRename = { vm.rename(e.category.name, it); editing = null },
            onMerge = { vm.merge(e.category.name, it); editing = null })
        null -> {}
    }
}

@Composable
private fun NameDialog(title: String, initial: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var name by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Name") }, singleLine = true) },
        confirmButton = { TextButton(enabled = name.isNotBlank() && name.trim() != initial, onClick = { onSave(name.trim()) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun EditDialog(
    category: CategoryInfo,
    others: List<String>,
    onDismiss: () -> Unit,
    onRename: (String) -> Unit,
    onMerge: (String) -> Unit,
) {
    var mode by remember { mutableStateOf("rename") }
    var name by remember { mutableStateOf(category.name) }
    var target by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(category.name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = mode == "rename", onClick = { mode = "rename" }); Text("Rename")
                    RadioButton(selected = mode == "merge", onClick = { mode = "merge" }); Text("Merge into another")
                }
                if (mode == "rename") {
                    OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("New name") }, singleLine = true)
                    Text("Its ${category.transactions} transactions, rules, budget and bills move to the new name.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    Text("Removes ${category.name}; its ${category.transactions} transactions, rules and bills move to:",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Column(Modifier.heightIn(max = 280.dp).verticalScroll(rememberScrollState())) {
                        others.forEach { o ->
                            Row(Modifier.fillMaxWidth().clickable { target = o }, verticalAlignment = Alignment.CenterVertically) {
                                RadioButton(selected = target == o, onClick = { target = o }); Text(o)
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            if (mode == "rename") {
                TextButton(enabled = name.isNotBlank() && name.trim() != category.name, onClick = { onRename(name.trim()) }) { Text("Rename") }
            } else {
                TextButton(enabled = target != null, onClick = { onMerge(target!!) }) { Text("Merge") }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
