package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

data class PhInstance(
    val name: String,
    val url: String,
    val online: Boolean,
    val error: String,
    /** enabled, disabled, failed oder unknown */
    val blocking: String,
    /** Sekunden bis zum Wiedereinschalten, null: ohne Ende oder nicht pausiert */
    val timer: Int?,
    val version: String,
    /** z. B. "FTL v6.9" für jeden Teil mit neuerer Version */
    val updates: List<String> = emptyList(),
)

data class PhOverview(
    val configured: Boolean,
    val manager: Boolean,
    /** enabled, disabled, mixed oder unknown (keins erreichbar) */
    val blocking: String,
    val instances: List<PhInstance>,
)

data class PhCount(val label: String, val count: Long)

data class PhSlot(val time: Long, val total: Long, val blocked: Long)

data class PhStats(
    val total: Long,
    val blocked: Long,
    val percent: Double,
    val clientsActive: Int,
    val gravity: Long,
    val gravityUpdate: Long,
    val topBlocked: List<PhCount>,
    val topPermitted: List<PhCount>,
    val topClients: List<PhCount>,
    val history: List<PhSlot>,
    val errors: List<String>,
    val answered: Int,
    val instances: Int,
)

data class PhPauseResult(val failed: List<String>)

/** Update eines Pi-hole über SSH. [state]: waiting, running, ok oder failed. */
data class PhJob(val state: String, val finished: Long, val log: List<String>) {
    val busy get() = state == "waiting" || state == "running"
}

data class PhDetail(
    val name: String,
    val url: String,
    val blocking: String,
    val timer: Int?,
    val versions: List<Pair<String, String>>,
    val updates: List<String>,
    val total: Long,
    val blocked: Long,
    val cached: Long,
    val forwarded: Long,
    val uniqueDomains: Long,
    val percent: Double,
    val clients: Int,
    val gravity: Long,
    val gravityUpdate: Long,
    val lists: Int,
    val ownAllowed: Int,
    val ownDenied: Int,
    val model: String,
    val hostname: String,
    val os: String,
    val uptime: Long,
    val ftlUptime: Long,
    val cpus: Int,
    val load: List<Double>,
    val ramTotal: Long,
    val ramUsed: Long,
    /** null: Gerät meldet keine Temperatur */
    val temp: Double?,
    val messages: List<String>,
    val manager: Boolean = false,
    /** null: seit dem Start des Dienstes nicht aktualisiert */
    val updateJob: PhJob? = null,
)

/** Pi-hole-Modul, nur über den gemeinsamen Zugang erreichbar. Pausieren gilt immer für alle Pi-holes. */
class PiholeApi(private val http: PiApi) {

    suspend fun overview(): PhOverview {
        val o = JSONObject(http.raw("api/overview"))
        return PhOverview(
            configured = o.optBoolean("configured"),
            manager = o.optBoolean("manager"),
            blocking = o.optString("blocking", "unknown"),
            instances = o.optJSONArray("instances").objects().map {
                PhInstance(
                    name = it.optString("name"),
                    url = it.optString("url"),
                    online = it.optBoolean("online"),
                    error = it.optString("error"),
                    blocking = it.optString("blocking", "unknown"),
                    timer = if (it.isNull("timer")) null else it.optDouble("timer").toInt(),
                    version = it.optString("version").ifBlank { it.optString("ftl").let { f -> if (f.isBlank()) "" else "FTL $f" } },
                    updates = it.optJSONArray("updates").objects().map { u -> "${u.optString("part")} ${u.optString("available")}" },
                )
            },
        )
    }

    suspend fun stats(): PhStats {
        val o = JSONObject(http.raw("api/stats"))
        fun counts(key: String, label: (JSONObject) -> String) =
            o.optJSONArray(key).objects().map { PhCount(label(it), it.optLong("count")) }
        return PhStats(
            total = o.optLong("total"),
            blocked = o.optLong("blocked"),
            percent = o.optDouble("percent", 0.0),
            clientsActive = o.optInt("clients_active"),
            gravity = o.optLong("gravity"),
            gravityUpdate = o.optLong("gravity_update"),
            topBlocked = counts("top_blocked") { it.optString("domain") },
            topPermitted = counts("top_permitted") { it.optString("domain") },
            topClients = counts("top_clients") { it.optString("name").ifBlank { it.optString("ip") } },
            history = o.optJSONArray("history").objects().map { PhSlot(it.optLong("time"), it.optLong("total"), it.optLong("blocked")) },
            errors = o.optJSONArray("errors").objects().map { it.optString("error") },
            answered = o.optInt("answered"),
            instances = o.optInt("instances"),
        )
    }

    suspend fun detail(index: Int): PhDetail {
        val o = JSONObject(http.raw("api/instances/$index"))
        val q = o.optJSONObject("queries") ?: JSONObject()
        val d = o.optJSONObject("device") ?: JSONObject()
        val load = d.optJSONArray("load")
        return PhDetail(
            name = o.optString("name"),
            url = o.optString("url"),
            blocking = o.optString("blocking", "unknown"),
            timer = if (o.isNull("timer")) null else o.optDouble("timer").toInt(),
            versions = o.optJSONArray("versions").objects().map { it.optString("part") to it.optString("installed") },
            updates = o.optJSONArray("updates").objects().map { "${it.optString("part")} ${it.optString("available")}" },
            total = q.optLong("total"),
            blocked = q.optLong("blocked"),
            cached = q.optLong("cached"),
            forwarded = q.optLong("forwarded"),
            uniqueDomains = q.optLong("unique_domains"),
            percent = o.optDouble("percent", 0.0),
            clients = o.optInt("clients"),
            gravity = o.optLong("gravity"),
            gravityUpdate = o.optLong("gravity_update"),
            lists = o.optInt("lists"),
            ownAllowed = o.optInt("own_allowed"),
            ownDenied = o.optInt("own_denied"),
            model = d.optString("model"),
            hostname = d.optString("hostname"),
            os = d.optString("os"),
            uptime = d.optLong("uptime"),
            ftlUptime = d.optLong("ftl_uptime"),
            cpus = d.optInt("cpus"),
            load = if (load == null) emptyList() else (0 until load.length()).map { load.optDouble(it) },
            ramTotal = d.optLong("ram_total"),
            ramUsed = d.optLong("ram_used"),
            temp = if (d.isNull("temp") || !d.has("temp")) null else d.optDouble("temp"),
            messages = o.optJSONArray("messages").let { a -> if (a == null) emptyList() else (0 until a.length()).map { a.getString(it) } },
            manager = o.optBoolean("manager"),
            updateJob = o.optJSONObject("update_job")?.let { job(it) },
        )
    }

    /** Startet das Update eines Pi-hole, [index] null: alle nacheinander. Nur Eigentümer und Admins. */
    suspend fun update(index: Int? = null) {
        http.postJson(if (index == null) "api/update" else "api/instances/$index/update", "{}")
    }

    /** Stand der Updates in der Reihenfolge der Pi-holes, null: keins gelaufen. */
    suspend fun updateJobs(): List<PhJob?> =
        JSONObject(http.raw("api/update")).optJSONArray("jobs").objects().map { if (it.optString("state").isBlank()) null else job(it) }

    private fun job(o: JSONObject) = PhJob(
        state = o.optString("state"),
        finished = o.optLong("finished"),
        log = o.optJSONArray("log").let { a -> if (a == null) emptyList() else (0 until a.length()).map { a.getString(it) } },
    )

    /** [seconds] null: ohne Ende (nur Eigentümer und Admins). */
    suspend fun pause(seconds: Int?): PhPauseResult {
        val body = JSONObject()
        if (seconds != null) body.put("seconds", seconds)
        return result(http.postJson("api/pause", body.toString()))
    }

    suspend fun resume(): PhPauseResult = result(http.postJson("api/resume", "{}"))

    private fun result(raw: String) = PhPauseResult(
        JSONObject(raw).optJSONArray("instances").objects().filter { !it.optBoolean("ok") }.map { it.optString("error") },
    )

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
}
