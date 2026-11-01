package de.archonw.netmon

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class HubModule(val id: String, val name: String, val online: Boolean?)

/** Fragt den gemeinsamen Zugang, welche Module installiert sind. */
suspend fun hubModules(home: String, external: String, password: String): List<HubModule> {
    val body = PiApi(home, password, external).raw("api/modules")
    val arr = JSONObject(body).getJSONArray("modules")
    return (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        HubModule(o.getString("id"), o.optString("name"), if (o.has("online")) o.optBoolean("online") else null)
    }
}

data class PiRow(
    val id: String,
    val name: String,
    val category: String,
    val running: Boolean,
    val status: String,
    val okTime: Long,
    val images: Int,
    val bytes: Long,
    val schedule: String,
    val online: Boolean?,
    val card: String,
    val temp: String,
) {
    companion object {
        fun from(o: JSONObject) = PiRow(
            id = o.getString("id"),
            name = o.getString("name"),
            category = o.optString("category"),
            running = o.optBoolean("running"),
            status = o.optString("status"),
            okTime = o.optLong("ok_time"),
            images = o.optInt("images"),
            bytes = o.optLong("bytes"),
            schedule = o.optString("schedule"),
            online = if (o.isNull("online")) null else o.optBoolean("online"),
            card = o.optString("card"),
            temp = o.optString("temp"),
        )
    }
}

data class PiAttention(val serious: Boolean, val pi: String, val text: String)
data class PiError(val stamp: String, val pi: String, val message: String)

data class PiOverview(
    val pis: List<PiRow>,
    val images: Int,
    val imagesBytes: Long,
    val mirrorBytes: Long,
    val availBytes: Long,
    val totalBytes: Long,
    val running: Int,
    val attention: List<PiAttention>,
    val errors: List<PiError>,
) {
    val problems: Int get() = attention.count { it.serious }
}

data class PiImage(val file: String, val size: Long, val time: Long, val os: String)
data class PiProgress(val step: String, val percent: Int, val detail: String)

data class PiDetail(
    val row: PiRow,
    val error: String,
    val warnings: String,
    val notes: String,
    val address: String,
    val device: Map<String, String>,
    val imagesList: List<PiImage>,
    val progress: PiProgress?,
)

/** Spricht mit dem Pi-Wartung-Dienst auf dem Unraid (Basic Auth, Benutzername beliebig). */
class PiApi(homeUrl: String, private val password: String, externalUrl: String = "") {
    private val urls = listOf(homeUrl, externalUrl).map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    suspend fun raw(path: String): String = request("GET", path)

    suspend fun overview(): PiOverview {
        val o = JSONObject(request("GET", "api/overview"))
        return PiOverview(
            pis = o.getJSONArray("pis").objects().map(PiRow::from),
            images = o.optInt("images"),
            imagesBytes = o.optLong("images_bytes"),
            mirrorBytes = o.optLong("mirror_bytes"),
            availBytes = o.optLong("avail_bytes"),
            totalBytes = o.optLong("total_bytes"),
            running = o.optInt("running"),
            attention = o.getJSONArray("attention").objects().map {
                PiAttention(it.optBoolean("serious"), it.optString("pi"), it.optString("text"))
            },
            errors = o.getJSONArray("errors").objects().map {
                PiError(it.optString("stamp"), it.optString("pi"), it.optString("message"))
            },
        )
    }

    suspend fun detail(id: String): PiDetail {
        val o = JSONObject(request("GET", "api/pis/$id"))
        val device = o.optJSONObject("device")
        return PiDetail(
            row = PiRow.from(o.put("images", o.getJSONArray("images_list").length())),
            error = o.optString("error"),
            warnings = o.optString("warnings"),
            notes = o.optString("notes"),
            address = "${o.optString("user")}@${o.optString("host")}:${o.optInt("port")}",
            device = device?.keys()?.asSequence()?.associateWith { device.optString(it) }.orEmpty(),
            imagesList = o.getJSONArray("images_list").objects().map {
                PiImage(it.getString("file"), it.optLong("size"), it.optLong("time"), it.optString("os"))
            },
            progress = o.optJSONObject("progress")?.let {
                PiProgress(it.optString("step"), it.optInt("percent"), it.optString("detail"))
            },
        )
    }

    suspend fun backup(id: String) { request("POST", "api/pis/$id/backup") }

    suspend fun cancel(id: String) { request("POST", "api/pis/$id/cancel") }

    /** Fragt alle Pis neu ab; das Ergebnis liegt beim nächsten Abruf der Übersicht vor. */
    suspend fun check() { request("POST", "api/check") }

    private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }

    private suspend fun request(method: String, path: String): String = withContext(Dispatchers.IO) {
        if (urls.isEmpty()) throw ApiException("Keine Adresse eingetragen")
        val ordered = urls.sortedBy { if (it == lastWorking) 0 else 1 }
        var lastError: Exception? = null
        for (base in ordered) {
            try {
                val body = call(base, method, path, connectTimeout = if (ordered.size > 1) 3000 else 8000)
                lastWorking = base
                return@withContext body
            } catch (e: ApiException) {
                throw e
            } catch (e: Exception) {
                lastError = e
            }
        }
        val hint = if (urls.size > 1) "" else " Bist du im Heimnetz?"
        throw ApiException("Dienst nicht erreichbar (${lastError?.javaClass?.simpleName}).$hint")
    }

    private fun call(base: String, method: String, path: String, connectTimeout: Int): String {
        val conn = URL(base.trimEnd('/') + "/" + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = connectTimeout
            conn.readTimeout = 130000
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
            return when {
                code in 200..299 -> body
                code == 401 -> throw ApiException("Passwort falsch")
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
