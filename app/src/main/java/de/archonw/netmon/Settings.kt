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

    /** Optionale Adresse für unterwegs, z. B. https://netmon.example.de (über den Reverse Proxy). */
    var externalUrl: String
        get() = prefs.getString("external_url", "") ?: ""
        set(value) = prefs.edit().putString("external_url", value.trim()).apply()

    var password: String
        get() = prefs.getString("password", "") ?: ""
        set(value) = prefs.edit().putString("password", value).apply()

    var sortOrder: SortOrder
        get() = enumOr(prefs.getString("sort", null), SortOrder.NAME_ASC)
        set(value) = prefs.edit().putString("sort", value.name).apply()

    var quickFilter: QuickFilter
        get() = enumOr(prefs.getString("quick_filter", null), QuickFilter.ALL)
        set(value) = prefs.edit().putString("quick_filter", value.name).apply()

    var appStyle: AppStyle
        get() = enumOr(prefs.getString("style", null), AppStyle.SOFT)
        set(value) = prefs.edit().putString("style", value.name).apply()

    /** Jedes Design merkt sich seine eigene Akzentfarbe. */
    fun accent(style: AppStyle): Accent =
        enumOr(prefs.getString("accent_${style.name}", null), style.accents.first())
            .takeIf { it in style.accents } ?: style.accents.first()

    fun setAccent(style: AppStyle, accent: Accent) =
        prefs.edit().putString("accent_${style.name}", accent.name).apply()

    var themeMode: ThemeMode
        get() = enumOr(prefs.getString("theme", null), ThemeMode.SYSTEM)
        set(value) = prefs.edit().putString("theme", value.name).apply()

    /** Entsperren per Fingerabdruck statt mit dem Passwort. */
    var useBiometrics: Boolean
        get() = prefs.getBoolean("use_biometrics", false)
        set(value) = prefs.edit().putBoolean("use_biometrics", value).apply()

    /** Ältere Versionen kannten nur den Fingerabdruck; bestehende Installationen behalten ihn. */
    fun migrateLockMode() {
        if (!prefs.contains("use_biometrics")) useBiometrics = isConfigured
    }

    private inline fun <reified E : Enum<E>> enumOr(name: String?, default: E): E =
        enumValues<E>().firstOrNull { it.name == name } ?: default

    val isConfigured: Boolean get() = serverUrl.isNotBlank()

    fun api() = Api(serverUrl, password, externalUrl)
}
