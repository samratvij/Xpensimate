package com.xpensimate.app

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.xpensimate.app.data.PhoneNotification
import com.xpensimate.app.data.QueueFlushWorker
import java.time.Instant

/**
 * Forwards Wealthsimple and Google Wallet (Google Pay) notifications, and Emirates NBD purchase SMS, to the
 * server, which parses them into transactions. The SMS has no sender name to match, so only a text in the
 * purchase wording is forwarded; every other message and app is ignored without being kept. (The class keeps
 * its original name: renaming the service would revoke the notification access the user granted.)
 */
class WealthsimpleListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val sms = sbn.packageName in SMS_PACKAGES
        if (sbn.packageName !in CAPTURED_PACKAGES && !sms) return
        val notification = sbn.notification
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras.getCharSequence(Notification.EXTRA_TEXT))
            ?.toString().orEmpty()
        if (title.isBlank() && text.isBlank()) return
        if (sms && !ENBD_PURCHASE.containsMatchIn(text)) return

        val app = application as XpensimateApp
        app.notificationQueue.add(
            PhoneNotification(sbn.packageName, title, text, Instant.ofEpochMilli(sbn.postTime).toString(), sbn.key)
        )
        // Sent as soon as there's a network; retried in the background while the server can't be reached.
        QueueFlushWorker.schedule(this)
    }

    companion object {
        val CAPTURED_PACKAGES = setOf("com.wealthsimple.trade", "com.google.android.apps.walletnfcrel")
        val SMS_PACKAGES = setOf(
            "com.google.android.apps.messaging", "com.android.mms",
            "com.samsung.android.messaging", "com.android.messaging",
        )
        private val ENBD_PURCHASE = Regex("""(?is)Purchase of AED\s*[\d,]+\.\d{2}\s+with\s+(?:Debit|Credit)\s+Card\s+ending\s+\d{4}\s+at\s+.+?\.\s+Avl\b""")
    }
}
