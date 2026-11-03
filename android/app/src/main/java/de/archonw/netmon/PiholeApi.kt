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
