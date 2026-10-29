package de.archonw.netmon

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys

/** Server-Adresse und Passwort, verschlüsselt im Android-Keystore abgelegt. */
class Settings(context: Context) {
    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "netmon_settings",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(value) = prefs.edit().putString("server_url", value.trim()).apply()

    var password: String
        get() = prefs.getString("password", "") ?: ""
        set(value) = prefs.edit().putString("password", value).apply()

    var sortOrder: SortOrder
        get() = enumOr(prefs.getString("sort", null), SortOrder.NAME_ASC)
        set(value) = prefs.edit().putString("sort", value.name).apply()

    var onlineFilter: OnlineFilter
        get() = enumOr(prefs.getString("filter_online", null), OnlineFilter.ALL)
        set(value) = prefs.edit().putString("filter_online", value.name).apply()

    var statusFilter: StatusFilter
        get() = enumOr(prefs.getString("filter_status", null), StatusFilter.ALL)
        set(value) = prefs.edit().putString("filter_status", value.name).apply()

    var themeMode: ThemeMode
        get() = enumOr(prefs.getString("theme", null), ThemeMode.SYSTEM)
        set(value) = prefs.edit().putString("theme", value.name).apply()

    private inline fun <reified E : Enum<E>> enumOr(name: String?, default: E): E =
        enumValues<E>().firstOrNull { it.name == name } ?: default

    val isConfigured: Boolean get() = serverUrl.isNotBlank()

    fun api() = Api(serverUrl, password)
}
