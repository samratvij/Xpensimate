package com.xpensimate.app

import android.app.Application
import com.xpensimate.app.data.Api
import com.xpensimate.app.data.NotificationQueue
import com.xpensimate.app.data.Push
import com.xpensimate.app.data.ServerConfig
import com.xpensimate.app.data.Settings
import com.xpensimate.app.data.createApi

class XpensimateApp : Application() {
    lateinit var settings: Settings
        private set
    lateinit var notificationQueue: NotificationQueue
        private set

    private var cached: Pair<ServerConfig, Api>? = null

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        notificationQueue = NotificationQueue(this)
        Push.createChannel(this)
    }

    /** One Retrofit client per server config; rebuilt when the address or token changes. */
    @Synchronized
    fun api(config: ServerConfig): Api {
        cached?.let { (c, api) -> if (c == config) return api }
        return createApi(config).also { cached = config to it }
    }
}
