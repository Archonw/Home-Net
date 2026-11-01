package de.archonw.netmon

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class Device(
    val mac: String,
    val name: String,
    val hostname: String,
    val ip: String,
    val iface: String,
    val status: String,
    val online: Boolean,
    val randomMac: Boolean,
    val firstSeen: String,
    val lastSeen: String,
    /** Bekannte Geräte, von denen dieser Eintrag eine Repeater-Kopie sein könnte. */
    val related: List<String> = emptyList(),
    /** Meldet sich, wenn das Gerät länger offline ist. */
    val watched: Boolean = false,
) {
    val title: String get() = name.ifBlank { hostname.ifBlank { "Unbekanntes Gerät" } }

    companion object {
        fun from(o: JSONObject) = Device(
            mac = o.getString("mac"),
            name = o.optString("name"),
            hostname = o.optString("hostname"),
            ip = o.optString("ip"),
            iface = o.optString("interface"),
            status = o.optString("status", "unknown"),
            online = o.optBoolean("online"),
            randomMac = o.optBoolean("random_mac"),
            firstSeen = o.optString("first_seen"),
            lastSeen = o.optString("last_seen"),
            watched = o.optBoolean("watched"),
            related = o.optJSONArray("related")?.let { arr ->
                (0 until arr.length()).map { i ->
                    arr.getJSONObject(i).let { "${it.optString("title")} (${it.optString("ip")})" }
                }
            }.orEmpty(),
        )
    }
}

class ApiException(message: String) : Exception(message)

/** Ein gekoppeltes Gerät meldet sich mit seinem Schlüssel an, sonst gilt das Passwort. */
fun authHeader(secret: String): String =
    if (secret.startsWith("hnd_")) "Bearer $secret"
    else "Basic " + Base64.encodeToString("app:$secret".toByteArray(), Base64.NO_WRAP)

/** Auf dem Monitor bereitgestellte App-Version; [url] ist die volle Download-Adresse. */
data class AppRelease(val version: String, val versionCode: Long, val url: String)

/**
 * Spricht mit der REST-API des Netzwerkmonitors (Basic Auth, Benutzername beliebig).
 *
 * Probiert erst die Heimnetz-Adresse und, wenn die nicht erreichbar ist, die Adresse für unterwegs.
 */
class Api(
    private val homeUrl: String,
    private val password: String,
    private val externalUrl: String = "",
) {
    private val urls = listOf(homeUrl, externalUrl).map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    suspend fun devices(): List<Device> {
        val arr = JSONArray(request("GET", "api/devices"))
        return (0 until arr.length()).map { Device.from(arr.getJSONObject(it)) }
    }

    suspend fun device(mac: String): Device =
        Device.from(JSONObject(request("GET", "api/devices/${enc(mac)}")))

    suspend fun allow(mac: String): Device =
        Device.from(JSONObject(request("POST", "api/devices/${enc(mac)}/allow")))

    suspend fun block(mac: String): Device =
        Device.from(JSONObject(request("POST", "api/devices/${enc(mac)}/block")))

    suspend fun setWatched(mac: String, watched: Boolean): Device =
        Device.from(JSONObject(request("POST", "api/devices/${enc(mac)}/${if (watched) "watch" else "unwatch"}")))

    /** Entfernt alle offline Geräte außer gesperrten; liefert die Anzahl. */
    suspend fun removeOffline(): Int =
        JSONObject(request("POST", "api/devices/remove-offline")).getInt("removed")

    /** Neueste App-Version auf dem Monitor, null wenn dort keine liegt. */
    suspend fun latestApp(): AppRelease? {
        val o = try {
            JSONObject(request("GET", "app/latest.json"))
        } catch (e: ApiException) {
            return null
        }
        val base = (lastWorking ?: urls.first()).trimEnd('/')
        return AppRelease(o.getString("version"), o.getLong("version_code"), base + o.getString("url"))
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    private suspend fun request(method: String, path: String): String = withContext(Dispatchers.IO) {
        // Zuletzt erfolgreiche Adresse zuerst, damit unterwegs nicht jedes Mal das Heimnetz abwartet
        val ordered = urls.sortedBy { if (it == lastWorking) 0 else 1 }
        var lastError: Exception? = null
        for (base in ordered) {
            try {
                val body = request(base, method, path, connectTimeout = if (ordered.size > 1) 3000 else 8000)
                lastWorking = base
                return@withContext body
            } catch (e: ApiException) {
                throw e
            } catch (e: Exception) {
                lastError = e
            }
        }
        val hint = if (urls.size > 1) "" else " Bist du im Heimnetz?"
        throw ApiException("Monitor nicht erreichbar (${lastError?.javaClass?.simpleName}).$hint")
    }

    private fun request(base: String, method: String, path: String, connectTimeout: Int): String {
        val url = URL(base.trimEnd('/') + "/" + path)
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = connectTimeout
            conn.readTimeout = 15000
            conn.setRequestProperty("Authorization", authHeader(password))
            conn.setRequestProperty("Accept", "application/json")
            if (method == "POST") {
                conn.doOutput = true
                conn.outputStream.use { }
            }
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            return when {
                code in 200..299 -> body
                code == 401 -> throw ApiException("Passwort falsch")
                code == 404 -> throw ApiException("Gerät nicht gefunden")
                code == 429 -> throw ApiException("Zu viele falsche Passwörter, bitte in 15 Minuten erneut versuchen")
                else -> throw ApiException(
                    runCatching { JSONObject(body).getString("detail") }.getOrDefault("Fehler $code")
                )
            }
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        @Volatile
        private var lastWorking: String? = null
    }
}
