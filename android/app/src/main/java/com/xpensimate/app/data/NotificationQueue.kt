package com.xpensimate.app.data

import android.content.Context
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import retrofit2.HttpException
import java.io.File

/**
 * Wealthsimple and Google Pay notifications waiting to reach the server (e.g. while Tailscale is off). A small
 * JSON-lines file in app-private storage. [QueueFlushWorker] sends it whenever the phone has a network, and the
 * app also sends it on start.
 *
 * Safe to add while a send is running: a send only removes the entries it delivered, so anything captured
 * meanwhile stays queued. Only one send runs at a time. (The server ignores a notification it has already seen,
 * so an entry sent twice after a crash is harmless.)
 */
class NotificationQueue(context: Context) {
    private val file = File(context.filesDir, "pending_notifications.jsonl")
    private val sending = Mutex()

    @Synchronized
    fun add(item: PhoneNotification) = file.appendText(json.encodeToString(item) + "\n")

    @Synchronized
    fun size(): Int = read().size

    @Synchronized
    private fun read(): List<PhoneNotification> =
        if (!file.exists()) emptyList()
        else file.readLines().filter { it.isNotBlank() }.mapNotNull { runCatching { json.decodeFromString<PhoneNotification>(it) }.getOrNull() }

    /** Drop the given entries, keeping everything else, including anything added since they were read. */
    @Synchronized
    private fun remove(done: Set<PhoneNotification>) {
        val left = read().filterNot { it in done }
        if (left.isEmpty()) file.delete() else file.writeText(left.joinToString("") { json.encodeToString(it) + "\n" })
    }

    /** Send everything queued; anything that still fails stays queued. Returns how many were sent. */
    suspend fun flush(api: Api): Int = sending.withLock {
        val done = mutableSetOf<PhoneNotification>()
        var sent = 0
        for (item in read()) {
            val result = runCatching { api.sendNotification(item) }
            val error = result.exceptionOrNull()
            when {
                error == null -> { done += item; sent++ }
                // The server read it and said no (not a network or auth problem): retrying won't change that.
                error is HttpException && error.code() in 400..499 && error.code() !in RETRYABLE -> done += item
            }
        }
        if (done.isNotEmpty()) remove(done)
        sent
    }

    private companion object {
        val RETRYABLE = setOf(401, 403, 408, 429)
    }
}
