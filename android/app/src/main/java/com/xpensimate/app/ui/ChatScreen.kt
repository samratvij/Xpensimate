package com.xpensimate.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.ChatAsk
import com.xpensimate.app.data.ChatMemory
import com.xpensimate.app.data.ChatMemoryIn
import com.xpensimate.app.data.ChatMessage
import com.xpensimate.app.data.ChatThread
import com.xpensimate.app.data.ChatUsage
import com.xpensimate.app.data.json
import kotlinx.coroutines.launch
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import java.net.URI
import java.time.format.DateTimeFormatter

/** Example questions, with amounts in the household's currency. */
private fun examples() = listOf(
    "How much did we spend eating out in the last 3 months?",
    "Can we afford a ${3000.0.wholeMoney()} trip in March?",
    "We're planning to buy a car next summer, about ${25000.0.wholeMoney()}. Keep that in mind.",
    "What should I cut back on this month?",
)

/** The server's own explanation (e.g. the monthly limit), when it gave one. */
private fun Throwable.chatError(): String {
    if (this is HttpException) {
        val detail = runCatching {
            json.parseToJsonElement(response()?.errorBody()?.string().orEmpty()).jsonObject["detail"]?.jsonPrimitive?.content
        }.getOrNull()
        if (detail != null) return detail
    }
    return friendly()
}

class ChatViewModel(private val api: Api) : ViewModel() {
    var threadId by mutableStateOf<String?>(null)
        private set
    var title by mutableStateOf<String?>(null)
        private set
    val messages = mutableStateListOf<ChatMessage>()
    var canContinue by mutableStateOf(true)
        private set
    var input by mutableStateOf("")
    var sending by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
        private set
    var usage by mutableStateOf<ChatUsage?>(null)
        private set
    /** The history dialog's contents while it's open. */
    var threads by mutableStateOf<Load<List<ChatThread>>?>(null)
        private set

    init {
        viewModelScope.launch { usage = runCatching { api.chatUsage() }.getOrNull() }
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || sending) return
        input = ""
        error = null
        sending = true
        val question = ChatMessage(id = "local-${System.nanoTime()}", kind = "question", text = text)
        messages.add(question)
        viewModelScope.launch {
            runCatching { api.ask(ChatAsk(text, threadId)) }
                .onSuccess { reply ->
                    if (threadId == null) title = text
                    threadId = reply.threadId
                    messages.add(ChatMessage(reply.messageId, "answer", reply.text, reply.actions, reply.sources))
                    usage = usage?.let { it.copy(left = reply.questionsLeft, used = it.used + 1) }
                }
                .onFailure {
                    messages.remove(question)
                    input = text
                    error = it.chatError()
                }
            sending = false
        }
    }

    fun newChat() {
        if (sending) return
        threadId = null
        title = null
        messages.clear()
        canContinue = true
        error = null
    }

    fun showHistory() {
        threads = Load.Loading
        viewModelScope.launch {
            threads = runCatching { api.chatThreads() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun hideHistory() { threads = null }

    fun open(id: String) {
        threads = null
        if (sending) return
        viewModelScope.launch {
            runCatching { api.chatThread(id) }
                .onSuccess { thread ->
                    threadId = thread.id
                    title = thread.title
                    messages.clear()
                    messages.addAll(thread.messages)
                    canContinue = thread.canContinue
                    error = null
                }
                .onFailure { error = it.friendly() }
        }
    }

    fun delete(id: String) {
        viewModelScope.launch {
            runCatching { api.deleteChatThread(id) }.onSuccess {
                if (id == threadId) newChat()
                showHistory()
            }
        }
    }
}

/** The assistant's plain-text answer with its **bold** spans. */
private fun answerText(text: String): AnnotatedString = buildAnnotatedString {
    var rest = text
    val bold = Regex("""\*\*([^*\n]+)\*\*""")
    while (true) {
        val match = bold.find(rest) ?: break
        append(rest.substring(0, match.range.first))
        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(match.groupValues[1]) }
        rest = rest.substring(match.range.last + 1)
    }
    append(rest)
}

@Composable
fun ChatScreen(vm: ChatViewModel, onOpenMemory: () -> Unit) {
    val list = rememberLazyListState()
    LaunchedEffect(vm.messages.size, vm.sending) {
        val count = vm.messages.size + (if (vm.sending) 1 else 0)
        if (count > 0) list.animateScrollToItem(count - 1)
    }
    Column(Modifier.fillMaxSize().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = vm::showHistory) { Text("History") }
            Spacer(Modifier.weight(1f))
            vm.usage?.left?.let {
                Text("$it left this month", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (vm.threadId != null) TextButton(onClick = vm::newChat) { Text("New chat") }
            IconButton(onClick = onOpenMemory) { Icon(Icons.Filled.Psychology, contentDescription = "Assistant memory") }
        }
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(),
            state = list,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (vm.messages.isEmpty() && !vm.sending) item { Intro(vm) }
            items(vm.messages, key = { it.id + it.kind }) { Bubble(it) }
            if (vm.sending) item {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text("Thinking… (looking at your data can take a little while)", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            vm.error?.let { item { Text(it, color = MaterialTheme.colorScheme.error) } }
        }
        val available = vm.usage?.available != false && vm.canContinue
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = vm.input,
                onValueChange = { vm.input = it.take(4000) },
                modifier = Modifier.weight(1f),
                enabled = available,
                placeholder = {
                    Text(when {
                        vm.usage?.available == false -> "Chat needs an AI model set up on the server"
                        !vm.canContinue -> "This chat is full: start a new one"
                        else -> "Ask or tell me something…"
                    })
                },
                maxLines = 5,
                shape = RoundedCornerShape(24.dp),
            )
            IconButton(onClick = vm::send, enabled = available && !vm.sending && vm.input.isNotBlank()) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
            }
        }
    }
    vm.threads?.let { HistoryDialog(it, vm) }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Intro(vm: ChatViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Ask about your money", style = MaterialTheme.typography.titleMedium)
        Text(
            "Questions about spending, budgets and bills, plans you want help with, or changes (“set the grocery " +
                "budget to ${800.0.wholeMoney()}”). It uses your transactions, receipts, bills and balances, what " +
                "you've told it to remember" + (if (vm.usage?.webSearch == true) ", and the web" else "") +
                ". Your chats are private to you.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            examples().forEach { example -> SuggestionChip(onClick = { vm.input = example }, label = { Text(example) }) }
        }
    }
}

@Composable
private fun Bubble(message: ChatMessage) {
    val mine = message.kind == "question"
    val uri = LocalUriHandler.current
    Box(Modifier.fillMaxWidth(), contentAlignment = if (mine) Alignment.CenterEnd else Alignment.CenterStart) {
        Surface(
            color = if (mine) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
            contentColor = if (mine) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant,
            shape = RoundedCornerShape(18.dp),
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (mine) Text(message.text.orEmpty()) else Text(answerText(message.text.orEmpty()))
                message.actions.orEmpty().forEach { Text("✅ ${it.summary}", style = MaterialTheme.typography.bodySmall) }
                message.sources.orEmpty().forEach { source ->
                    val label = source.title ?: runCatching { URI(source.url).host }.getOrNull() ?: source.url
                    Text(
                        "🔗 $label",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.clickable { runCatching { uri.openUri(source.url) } },
                    )
                }
            }
        }
    }
}

private val threadTime = DateTimeFormatter.ofPattern("MMM d, h:mm a")

@Composable
private fun HistoryDialog(threads: Load<List<ChatThread>>, vm: ChatViewModel) {
    AlertDialog(
        onDismissRequest = vm::hideHistory,
        title = { Text("Your chats") },
        text = {
            when (threads) {
                Load.Loading -> CircularProgressIndicator()
                is Load.Failed -> Text(threads.message)
                is Load.Ok -> if (threads.value.isEmpty()) Text("No chats yet.") else LazyColumn {
                    items(threads.value, key = { it.id }) { thread ->
                        Row(Modifier.fillMaxWidth().clickable { vm.open(thread.id) }.padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(thread.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(parseInstant(thread.updatedAt).atZone(zone).format(threadTime),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            IconButton(onClick = { vm.delete(thread.id) }) {
                                Icon(Icons.Filled.Delete, contentDescription = "Delete chat")
                            }
                        }
                        HorizontalDivider()
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = vm::hideHistory) { Text("Close") } },
    )
}

class ChatMemoryViewModel(private val api: Api) : ViewModel() {
    var memories by mutableStateOf<Load<List<ChatMemory>>>(Load.Loading)
        private set
    var draft by mutableStateOf("")

    init { load() }

    fun load() {
        viewModelScope.launch {
            memories = runCatching { api.chatMemory() }.fold({ Load.Ok(it) }, { Load.Failed(it.friendly()) })
        }
    }

    fun add() {
        val fact = draft.trim()
        if (fact.length < 3) return
        viewModelScope.launch {
            runCatching { api.addChatMemory(ChatMemoryIn(fact)) }.onSuccess { draft = ""; load() }
        }
    }

    fun delete(id: String) {
        viewModelScope.launch { runCatching { api.deleteChatMemory(id) }.onSuccess { load() } }
    }
}

private val memoryDate = DateTimeFormatter.ofPattern("MMM d, yyyy")

@Composable
fun ChatMemoryScreen(vm: ChatMemoryViewModel) {
    when (val state = vm.memories) {
        Load.Loading -> LoadingView()
        is Load.Failed -> ErrorView(state.message) { vm.load() }
        is Load.Ok -> LazyColumn(Modifier.fillMaxSize().imePadding()) {
            item {
                Text(
                    "Plans and facts the assistant keeps in mind when you chat. Only you see these. Tell it in chat, " +
                        "or add one here.",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            items(state.value, key = { it.id }) { memory ->
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(memory.fact)
                        Text(parseInstant(memory.createdAt).atZone(zone).format(memoryDate),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { vm.delete(memory.id) }) { Icon(Icons.Filled.Delete, contentDescription = "Forget") }
                }
                HorizontalDivider()
            }
            item {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = vm.draft,
                        onValueChange = { vm.draft = it.take(500) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Add something to remember") },
                        placeholder = { Text("e.g. Saving ${5000.0.wholeMoney()} for a car by June") },
                    )
                    Button(onClick = vm::add, enabled = vm.draft.trim().length >= 3, modifier = Modifier.fillMaxWidth()) {
                        Text("Add")
                    }
                }
            }
        }
    }
}
