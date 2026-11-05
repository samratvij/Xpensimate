package com.xpensimate.app.data

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Budget alerts arrive as Firebase pushes on this channel (the server names it in every message). */
object Push {
    const val CHANNEL_ID = "budget_alerts"
    const val EXTRA_OPEN_ALERTS = "open_alerts"
    private const val TAG = "Push"

    /** False when the app was built without google-services.json: push is simply off. */
    fun isAvailable(context: Context): Boolean = FirebaseApp.getApps(context).isNotEmpty()

    fun createChannel(context: Context) {
        val channel = NotificationChannel(CHANNEL_ID, "Budget alerts", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "When a budget line nears or passes its monthly limit"
        }
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    /** Tell the server where to push. Safe to call on every start: the server upserts by token. */
    suspend fun register(context: Context, api: Api, token: String? = null) {
        if (!isAvailable(context)) return
        runCatching {
            val current = token ?: fcmToken()
            api.registerDevice(DeviceRegistration(current, "${Build.MANUFACTURER} ${Build.MODEL}"))
        }.onFailure { Log.w(TAG, "Device registration failed", it) }
    }

    private suspend fun fcmToken(): String = suspendCancellableCoroutine { cont ->
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { cont.resume(it) }
            .addOnFailureListener { cont.resumeWithException(it) }
    }
}
