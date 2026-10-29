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
        )
    }
}

class ApiException(message: String) : Exception(message)

/** Spricht mit der REST-API des Netzwerkmonitors (Basic Auth, Benutzername beliebig). */
class Api(private val baseUrl: String, private val password: String) {

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

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    private suspend fun request(method: String, path: String): String = withContext(Dispatchers.IO) {
        val url = URL(baseUrl.trimEnd('/') + "/" + path)
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8000
            conn.readTimeout = 15000
            val auth = Base64.encodeToString("app:$password".toByteArray(), Base64.NO_WRAP)
            conn.setRequestProperty("Authorization", "Basic $auth")
            conn.setRequestProperty("Accept", "application/json")
            if (method == "POST") {
                conn.doOutput = true
                conn.outputStream.use { }
            }
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            when {
                code in 200..299 -> body
                code == 401 -> throw ApiException("Passwort falsch")
                code == 404 -> throw ApiException("Gerät nicht gefunden")
                else -> throw ApiException(
                    runCatching { JSONObject(body).getString("detail") }.getOrDefault("Fehler $code")
                )
            }
        } catch (e: ApiException) {
            throw e
        } catch (e: Exception) {
            throw ApiException("Monitor nicht erreichbar (${e.javaClass.simpleName}). Bist du im Heimnetz?")
        } finally {
            conn.disconnect()
        }
    }
}
