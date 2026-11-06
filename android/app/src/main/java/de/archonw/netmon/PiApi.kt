package de.archonw.netmon

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class HubModule(
    val id: String,
    val name: String,
    val online: Boolean?,
    val description: String = "",
    /** Weg zur Weboberfläche des Moduls, z. B. /m/beispiel, wenn es eine hat. */
    val webPath: String = "",
    /** Weg, unter dem das Modul seine Kachel beschreibt ({"text": …, "alert": …}), sonst leer. */
    val tile: String = "",
)

/** Kachel, die ein Modul selbst beschreibt. */
data class ModuleTile(val text: String, val alert: Boolean)

/** Fragt den gemeinsamen Zugang, welche Module installiert sind. */
suspend fun hubModules(home: String, external: String, password: String): List<HubModule> {
    val body = PiApi(home, password, external).raw("api/modules")
    val arr = JSONObject(body).getJSONArray("modules")
    return (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        HubModule(
            o.getString("id"), o.optString("name"), if (o.has("online")) o.optBoolean("online") else null,
            description = o.optString("description"),
            webPath = if (o.optBoolean("web")) o.optString("path") else "",
            tile = o.optString("tile"),
        )
    }
}

/** Die Kachel eines Moduls, das die App nicht selbst kennt. */
suspend fun moduleTile(home: String, external: String, password: String, path: String): ModuleTile {
    val o = JSONObject(PiApi(home, password, external).raw(path.trimStart('/')))
    return ModuleTile(o.optString("text"), o.optBoolean("alert"))
}

/** Koppelt dieses Gerät mit dem Code von der Startseite; liefert seinen Geräteschlüssel. */
suspend fun pairDevice(
    home: String, external: String, username: String, password: String, code: String, name: String,
): String =
    withContext(Dispatchers.IO) {
        val payload = JSONObject().put("username", username.trim()).put("password", password)
            .put("code", code.trim()).put("name", name).toString()
        var lastError: Exception? = null
        for (base in listOf(home, external).map { it.trim() }.filter { it.isNotEmpty() }.distinct()) {
            try {
                val conn = URL(base.trimEnd('/') + "/auth/pair").openConnection() as HttpURLConnection
                try {
                    conn.requestMethod = "POST"
                    conn.connectTimeout = 5000
                    conn.readTimeout = 15000
                    conn.doOutput = true
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.outputStream.use { it.write(payload.toByteArray()) }
                    val status = conn.responseCode
                    val body = (if (status in 200..299) conn.inputStream else conn.errorStream)
                        ?.bufferedReader()?.use { it.readText() }.orEmpty()
                    if (status in 200..299) return@withContext JSONObject(body).getString("token")
                    throw ApiException(runCatching { JSONObject(body).getString("detail") }.getOrDefault("Fehler $status"))
                } finally {
                    conn.disconnect()
                }
            } catch (e: ApiException) {
                throw e
            } catch (e: Exception) {
                lastError = e
            }
        }
        throw ApiException("Server nicht erreichbar (${lastError?.javaClass?.simpleName})")
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
    /** Wartende Updates, -1 solange nie geprüft. */
    val updates: Int = -1,
    /** "upgrading", "checking" oder leer. */
    val updateState: String = "",
    /** "all", "single" oder "careful" (gesperrt, nur nach Eintippen des Namens). */
    val updateMode: String = "single",
    /** Zählt in der Kachel „X Pis haben Updates“ mit. */
    val updateTile: Boolean = true,
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
            updates = o.optInt("updates", -1),
            updateState = o.optString("update_state"),
            updateMode = o.optString("update_mode", "single"),
            updateTile = o.optBoolean("update_tile", true),
        )
    }
}

data class PiUpdateTarget(val id: String, val name: String, val updates: Int)

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
    val updatePis: Int = 0,
    /** Was „Alle aktualisieren“ gerade nehmen würde; der Server prüft es beim Start noch einmal. */
    val updateAll: List<PiUpdateTarget> = emptyList(),
) {
    val problems: Int get() = attention.count { it.serious }
}

data class PiImage(val file: String, val size: Long, val time: Long, val os: String)
data class PiProgress(val step: String, val percent: Int, val detail: String)

data class PiPackage(val name: String, val old: String, val new: String)

data class PiSoftware(
    val state: String,
    val checked: Long,
    val count: Int,
    val installed: Int,
    val lastApt: Long,
    val reboot: Boolean,
    val error: String,
    val lastStatus: String,
    val lastTime: Long,
    val lastError: String,
    val packages: List<PiPackage>,
    val log: List<String>,
)

/** Letzter Neustart über Pi-Wartung: [state] ist "running", "ok", "failed" oder leer. */
data class PiRestart(val state: String, val phase: String, val started: Long, val ended: Long, val error: String)

data class PiDetail(
    val row: PiRow,
    val error: String,
    val warnings: String,
    val notes: String,
    val address: String,
    val device: Map<String, String>,
    val imagesList: List<PiImage>,
    val progress: PiProgress?,
    val software: PiSoftware?,
    val restart: PiRestart? = null,
)

/** Spricht mit dem Pi-Wartung-Dienst auf dem Unraid (Basic Auth, Benutzername beliebig). */
class PiApi(homeUrl: String, private val password: String, externalUrl: String = "") {
    private val urls = listOf(homeUrl, externalUrl).map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    suspend fun raw(path: String): String = request("GET", path)

    suspend fun postJson(path: String, json: String): String = request("POST", path, json)

    suspend fun putJson(path: String, json: String): String = request("PUT", path, json)

    suspend fun delete(path: String): String = request("DELETE", path)

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
            updatePis = o.optInt("update_pis"),
            updateAll = o.optJSONArray("update_all")?.objects()?.map {
                PiUpdateTarget(it.getString("id"), it.optString("name"), it.optInt("updates"))
            }.orEmpty(),
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
            software = o.optJSONObject("software")?.let { s ->
                PiSoftware(
                    state = s.optString("state"), checked = s.optLong("checked"), count = s.optInt("count", -1),
                    installed = s.optInt("installed"), lastApt = s.optLong("last_apt"), reboot = s.optBoolean("reboot"),
                    error = s.optString("error"), lastStatus = s.optString("last_status"), lastTime = s.optLong("last_time"),
                    lastError = s.optString("last_error"),
                    packages = s.optJSONArray("packages")?.objects()?.map {
                        PiPackage(it.optString("name"), it.optString("old"), it.optString("new"))
                    }.orEmpty(),
                    log = s.optJSONArray("log")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty(),
                )
            },
            restart = o.optJSONObject("restart")?.let {
                PiRestart(it.optString("state"), it.optString("phase"), it.optLong("started"), it.optLong("ended"), it.optString("error"))
            },
        )
    }

    /** Ein gesperrter Pi braucht seinen Namen als [confirm]. */
    suspend fun upgrade(id: String, confirm: String = "") {
        request("POST", "api/pis/$id/upgrade", JSONObject().put("confirm", confirm).toString())
    }

    /** Startet die Updates der genannten Pis; liefert die Namen der gestarteten und Fehlermeldungen. */
    suspend fun upgradeAll(ids: List<String>): Pair<List<String>, List<String>> {
        val o = JSONObject(request("POST", "api/updates/all", JSONObject().put("ids", JSONArray(ids)).toString()))
        val started = o.getJSONArray("started").let { a -> (0 until a.length()).map { a.getString(it) } }
        val failed = o.getJSONArray("failed").objects().map { "${it.optString("name")}: ${it.optString("message")}" }
        return started to failed
    }

    suspend fun checkUpdates(id: String) { request("POST", "api/pis/$id/check-updates") }

    suspend fun setUpdateTile(id: String, on: Boolean) {
        request("PUT", "api/pis/$id", JSONObject().put("update_tile", on).toString())
    }

    suspend fun setUpdateMode(id: String, mode: String) {
        request("PUT", "api/pis/$id", JSONObject().put("updates", mode).toString())
    }

    suspend fun backup(id: String) { request("POST", "api/pis/$id/backup") }

    /** Startet den Pi neu; während einer Sicherung oder eines Updates lehnt der Server ab. */
    suspend fun reboot(id: String) { request("POST", "api/pis/$id/reboot") }

    suspend fun cancel(id: String) { request("POST", "api/pis/$id/cancel") }

    /** Fragt alle Pis neu ab; das Ergebnis liegt beim nächsten Abruf der Übersicht vor. */
    suspend fun check() { request("POST", "api/check") }

    private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }

    private suspend fun request(method: String, path: String, json: String? = null): String = withContext(Dispatchers.IO) {
        if (urls.isEmpty()) throw ApiException("Keine Adresse eingetragen")
        val ordered = urls.sortedBy { if (it == lastWorking) 0 else 1 }
        var lastError: Exception? = null
        for (base in ordered) {
            try {
                val body = call(base, method, path, json, connectTimeout = if (ordered.size > 1) 3000 else 8000)
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

    private fun call(base: String, method: String, path: String, json: String?, connectTimeout: Int): String {
        val conn = URL(base.trimEnd('/') + "/" + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = connectTimeout
            conn.readTimeout = 130000
            conn.setRequestProperty("Authorization", authHeader(password))
            conn.setRequestProperty("Accept", "application/json")
            if (method == "POST" || method == "PUT") {
                conn.doOutput = true
                if (json != null) conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { out -> json?.let { out.write(it.toByteArray()) } }
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

        /** Die Adresse, die zuletzt geantwortet hat (Heimnetz oder unterwegs). */
        val reached: String? get() = lastWorking
    }
}
