package com.xpensimate.app.data

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.xpensimate.app.XpensimateApp
import kotlinx.coroutines.flow.first
import java.util.concurrent.TimeUnit

/**
 * Sends the [NotificationQueue] once the phone has a network, and keeps retrying (10, 20, 30... minutes, up to
 * 5 hours apart) while the server can't be reached, e.g. with Tailscale off. Survives reboots and the app being
 * closed.
 */
class QueueFlushWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as XpensimateApp
        val config = app.settings.config.first() ?: return Result.success() // not set up yet: nothing to send to
        app.notificationQueue.flush(app.api(config))
        return if (app.notificationQueue.size() == 0) Result.success() else Result.retry()
    }

    companion object {
        private const val NAME = "send-captured-notifications"

        fun schedule(context: Context) {
            val request = OneTimeWorkRequestBuilder<QueueFlushWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 10, TimeUnit.MINUTES)
                .build()
            // REPLACE: a new capture should be sent now, not wait out the previous attempt's back-off.
            WorkManager.getInstance(context).enqueueUniqueWork(NAME, ExistingWorkPolicy.REPLACE, request)
        }
    }
}
