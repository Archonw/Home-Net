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

    /** Eine Adresse und ein Passwort für alle Module; Monitor und Pi-Wartung liegen dann darunter. */
    var hubUrl: String
        get() = prefs.getString("hub_url", "") ?: ""
        set(value) = prefs.edit().putString("hub_url", value.trim().trimEnd('/')).apply()

    var hubUser: String
        get() = prefs.getString("hub_user", "") ?: ""
        set(value) = prefs.edit().putString("hub_user", value).apply()

    var hubExternalUrl: String
        get() = prefs.getString("hub_external_url", "") ?: ""
        set(value) = prefs.edit().putString("hub_external_url", value.trim().trimEnd('/')).apply()

    /** Eigener Schlüssel dieses Geräts, gilt auch von unterwegs mit eingerichtetem Passkey. */
    var hubToken: String
        get() = prefs.getString("hub_token", "") ?: ""
        set(value) = prefs.edit().putString("hub_token", value).apply()

    val isHubConfigured: Boolean get() = hubUrl.isNotBlank()

    /** Ob dieses Handy im Hintergrund Meldungen empfängt. */
    var pushEnabled: Boolean
        get() = prefs.getBoolean("push_enabled", true)
        set(value) = prefs.edit().putBoolean("push_enabled", value).apply()

    /** Letzte angezeigte Meldung; nach einer Unterbrechung geht es dort weiter. */
    var lastEventId: Long
        get() = prefs.getLong("last_event_id", 0)
        set(value) = prefs.edit().putLong("last_event_id", value).apply()

    /** Womit sich die App anmeldet: Geräteschlüssel, falls vorhanden, sonst das Passwort. */
    val hubSecret: String get() = hubToken.ifBlank { password }

    fun useHub(url: String, external: String, hubPassword: String, token: String) {
        hubToken = token
        if (url.trim().trimEnd('/') != hubUrl) lastEventId = 0
        hubUrl = url
        hubExternalUrl = external
        val base = hubUrl
        val outside = hubExternalUrl
        serverUrl = "$base/m/netzwerkmonitor"
        externalUrl = if (outside.isBlank()) "" else "$outside/m/netzwerkmonitor"
        password = hubPassword
        piUrl = "$base/m/pi-wartung"
        piExternalUrl = if (outside.isBlank()) "" else "$outside/m/pi-wartung"
        piPassword = hubPassword
    }

    var piUrl: String
        get() = prefs.getString("pi_url", "") ?: ""
        set(value) = prefs.edit().putString("pi_url", value.trim()).apply()

    var piExternalUrl: String
        get() = prefs.getString("pi_external_url", "") ?: ""
        set(value) = prefs.edit().putString("pi_external_url", value.trim()).apply()

    var piPassword: String
        get() = prefs.getString("pi_password", "") ?: ""
        set(value) = prefs.edit().putString("pi_password", value).apply()

    val isPiConfigured: Boolean get() = piUrl.isNotBlank()

    fun piApi() = PiApi(piUrl, if (isHubConfigured) hubSecret else piPassword, piExternalUrl)

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

    /** App-Version, für die „Später“ gewählt wurde; dafür wird nicht erneut gefragt. */
    var skippedAppVersion: Long
        get() = prefs.getLong("skipped_app_version", 0)
        set(value) = prefs.edit().putLong("skipped_app_version", value).apply()

    private inline fun <reified E : Enum<E>> enumOr(name: String?, default: E): E =
        enumValues<E>().firstOrNull { it.name == name } ?: default

    val isConfigured: Boolean get() = serverUrl.isNotBlank()

    fun api() = Api(serverUrl, if (isHubConfigured) hubSecret else password, externalUrl)
}
