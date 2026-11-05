package com.xpensimate.app

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.xpensimate.app.data.PhoneNotification
import com.xpensimate.app.data.QueueFlushWorker
import java.time.Instant

/**
 * Forwards Wealthsimple and Google Wallet (Google Pay) notifications to the server, which parses them into
 * transactions. Every other app's notifications are ignored without being read. (The class keeps its original
 * name: renaming the service would revoke the notification access the user granted.)
 */
class WealthsimpleListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in CAPTURED_PACKAGES) return
        val notification = sbn.notification
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty()
        if (title.isBlank() && text.isBlank()) return

        val app = application as XpensimateApp
        app.notificationQueue.add(
            PhoneNotification(sbn.packageName, title, text, Instant.ofEpochMilli(sbn.postTime).toString(), sbn.key)
        )
        // Sent as soon as there's a network; retried in the background while the server can't be reached.
        QueueFlushWorker.schedule(this)
    }

    companion object {
        val CAPTURED_PACKAGES = setOf("com.wealthsimple.trade", "com.google.android.apps.walletnfcrel")
    }
}
