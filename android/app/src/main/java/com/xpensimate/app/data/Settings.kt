package com.xpensimate.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

data class ServerConfig(val baseUrl: String, val token: String)

const val DEFAULT_SERVER_URL = ""

private val Context.dataStore by preferencesDataStore(name = "settings")
private val BASE_URL = stringPreferencesKey("base_url")
private val TOKEN = stringPreferencesKey("api_token")

/** Server address and API token, kept in the app's private storage (backups are disabled in the manifest). */
class Settings(private val context: Context) {
    val config: Flow<ServerConfig?> = context.dataStore.data.map { prefs ->
        val url = prefs[BASE_URL]
        val token = prefs[TOKEN]
        if (url.isNullOrBlank() || token.isNullOrBlank()) null else ServerConfig(url, token)
    }

    suspend fun save(config: ServerConfig) {
        context.dataStore.edit {
            it[BASE_URL] = config.baseUrl.trim()
            it[TOKEN] = config.token.trim()
        }
    }

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
