package de.archonw.netmon

import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject

data class UnraidDisk(
    val name: String,
    val kind: String,
    val model: String,
    val statusText: String,
    val ok: Boolean,
    val temp: Int?,
    val spundown: Boolean,
    val ssd: Boolean,
    val errors: Int,
    val size: Long,
    val free: Long,
    val mounted: Boolean,
)

data class UnraidCheck(
    val running: Boolean,
    val paused: Boolean,
    val percent: Double?,
    val last: Long,
    val lastErrors: Int,
    val lastExit: Int,
)

data class UnraidNote(val time: Long, val subject: String, val description: String, val importance: String)

data class UnraidTemps(val hot: Int, val max: Int, val hotSsd: Int, val maxSsd: Int)

data class UnraidOverview(
    val available: Boolean,
    val name: String,
    val version: String,
    val writable: Boolean,
    val manager: Boolean,
    val uptime: Long,
    val load: Double,
    val cpus: Int,
    val memTotal: Long,
    val memAvailable: Long,
    val arrayState: String,
    val started: Boolean,
    val missing: Int,
    val mover: Boolean,
    val check: UnraidCheck,
    val disks: List<UnraidDisk>,
    val notes: List<UnraidNote>,
    val temps: UnraidTemps,
    val apiKey: Boolean,
    val userScripts: Boolean,
) {
    fun hot(d: UnraidDisk) = d.temp != null && !d.spundown && d.temp >= (if (d.ssd) temps.hotSsd else temps.hot)
    fun critical(d: UnraidDisk) = d.temp != null && !d.spundown && d.temp >= (if (d.ssd) temps.maxSsd else temps.max)
    val problems: Int get() = disks.count { !it.ok || it.errors > 0 || critical(it) } + missing
    val warnings: Int get() = disks.count { hot(it) && !critical(it) }
}

data class UnraidShare(
    val name: String,
    val comment: String,
    val storage: String,
    val export: String,
    val security: String,
    val access: Map<String, String>,
) {
    /** Recht eines Benutzers, wie Unraid es anwendet. */
    fun rightOf(user: String): String = when (security) {
        "public" -> "read-write"
        "secure" -> access[user] ?: "read-only"
        else -> access[user] ?: "no-access"
    }
}

data class UnraidShares(val shares: List<UnraidShare>, val users: List<String>, val pools: List<String>, val writable: Boolean)

data class UnraidUser(val name: String, val desc: String, val rights: Map<String, String>)

data class UnraidContainer(val id: String, val name: String, val image: String, val state: String, val status: String, val protected: Boolean)

data class UnraidVm(val id: String, val name: String, val state: String)

data class UnraidScript(
    val id: String,
    val name: String,
    val description: String,
    val frequency: String,
    val custom: String,
    val running: Boolean,
    val last: Long,
    val foregroundOnly: Boolean,
    val arrayStarted: Boolean,
)

/** [starterRunning]: ohne den Home-Net Starter in User Scripts lassen sich die Skripte nur ansehen. */
data class UnraidScriptList(val installed: Boolean, val starterRunning: Boolean, val scripts: List<UnraidScript>)

/** Unraid-Verwaltung, nur über den gemeinsamen Zugang erreichbar. */
class UnraidApi(private val http: PiApi) {

    suspend fun overview(): UnraidOverview {
        val o = JSONObject(http.raw("api/overview"))
        val array = o.optJSONObject("array") ?: JSONObject()
        val check = array.optJSONObject("check") ?: JSONObject()
        val mem = o.optJSONObject("memory") ?: JSONObject()
        val temps = o.optJSONObject("temps") ?: JSONObject()
        return UnraidOverview(
            available = o.optBoolean("available"),
            name = o.optString("name"),
            version = o.optString("version"),
            writable = o.optBoolean("writable"),
            manager = o.optBoolean("manager"),
            uptime = o.optLong("uptime"),
            load = o.optJSONArray("load")?.optDouble(0) ?: 0.0,
            cpus = o.optInt("cpus"),
            memTotal = mem.optLong("total"),
            memAvailable = mem.optLong("available"),
            arrayState = array.optString("state"),
            started = array.optBoolean("started"),
            missing = array.optInt("missing"),
            mover = array.optBoolean("mover"),
            check = UnraidCheck(
                running = check.optBoolean("running"),
                paused = check.optBoolean("paused"),
                percent = if (check.isNull("percent")) null else check.optDouble("percent"),
                last = check.optLong("last"),
                lastErrors = check.optInt("last_errors"),
                lastExit = check.optInt("last_exit"),
            ),
            disks = o.optJSONArray("disks").objects().map {
                UnraidDisk(
                    name = it.optString("name"),
                    kind = it.optString("kind"),
                    model = it.optString("model"),
                    statusText = it.optString("status_text"),
                    ok = it.optBoolean("ok"),
                    temp = if (it.isNull("temp")) null else it.optInt("temp"),
                    spundown = it.optBoolean("spundown"),
                    ssd = it.optBoolean("ssd"),
                    errors = it.optInt("errors"),
                    size = it.optLong("size"),
                    free = it.optLong("free"),
                    mounted = it.optBoolean("mounted"),
                )
            },
            notes = o.optJSONArray("notifications").objects().map {
                UnraidNote(it.optLong("time"), it.optString("subject").ifBlank { it.optString("event") },
                    it.optString("description"), it.optString("importance"))
            },
            temps = UnraidTemps(temps.optInt("hot", 45), temps.optInt("max", 55), temps.optInt("hotssd", 60), temps.optInt("maxssd", 70)),
            apiKey = o.optBoolean("api"),
            userScripts = o.optBoolean("userscripts"),
        )
    }

    suspend fun arrayCommand(command: String) { http.postJson("api/array/$command", "{}") }

    suspend fun shares(): UnraidShares {
        val o = JSONObject(http.raw("api/shares"))
        return UnraidShares(
            shares = o.optJSONArray("shares").objects().map {
                val smb = it.optJSONObject("smb") ?: JSONObject()
                val storage = it.optJSONObject("storage") ?: JSONObject()
                UnraidShare(
                    name = it.optString("name"),
                    comment = it.optString("comment"),
                    storage = storageText(storage),
                    export = smb.optString("export", "-"),
                    security = smb.optString("security", "public"),
                    access = smb.optJSONObject("access").toMap(),
                )
            },
            users = o.optJSONArray("users").strings(),
            pools = o.optJSONArray("pools").strings(),
            writable = o.optBoolean("writable"),
        )
    }

    suspend fun addShare(name: String, comment: String, primary: String, secondary: String, security: String, access: Map<String, String>) {
        val body = JSONObject()
            .put("name", name.trim())
            .put("comment", comment.trim())
            .put("storage", JSONObject().put("primary", primary).put("secondary", secondary).put("mover", "to_secondary"))
            .put("smb", JSONObject().put("export", "e").put("security", security))
        if (security != "public") body.put("access", JSONObject(access as Map<*, *>))
        http.postJson("api/shares", body.toString())
    }

    suspend fun shareSecurity(name: String, export: String, security: String) {
        http.putJson("api/shares/${enc(name)}/security", JSONObject().put("export", export).put("security", security).toString())
    }

    suspend fun shareAccess(name: String, security: String, access: Map<String, String>) {
        http.putJson("api/shares/${enc(name)}/access",
            JSONObject().put("security", security).put("access", JSONObject(access as Map<*, *>)).toString())
    }

    suspend fun deleteShare(name: String) { http.delete("api/shares/${enc(name)}") }

    suspend fun users(): Pair<List<UnraidUser>, Boolean> {
        val o = JSONObject(http.raw("api/users"))
        val users = o.optJSONArray("users").objects().map {
            UnraidUser(it.optString("name"), it.optString("desc"), it.optJSONObject("rights").toMap())
        }
        return users to o.optBoolean("writable")
    }

    suspend fun addUser(name: String, desc: String, password: String) {
        http.postJson("api/users", JSONObject().put("name", name.trim()).put("desc", desc.trim()).put("password", password).toString())
    }

    suspend fun setPassword(name: String, password: String) {
        http.putJson("api/users/${enc(name)}/password", JSONObject().put("password", password).toString())
    }

    suspend fun deleteUser(name: String) { http.delete("api/users/${enc(name)}") }

    suspend fun containers(): List<UnraidContainer> =
        JSONObject(http.raw("api/docker")).optJSONArray("containers").objects().map {
            UnraidContainer(it.optString("id"), it.optString("name"), it.optString("image"), it.optString("state"),
                it.optString("status"), it.optBoolean("protected"))
        }

    suspend fun container(id: String, command: String) {
        http.postJson("api/docker", JSONObject().put("id", id).put("command", command).toString())
    }

    suspend fun vms(): List<UnraidVm> =
        JSONObject(http.raw("api/vms")).optJSONArray("vms").objects().map {
            UnraidVm(it.optString("id"), it.optString("name"), it.optString("state"))
        }

    suspend fun vm(id: String, command: String) {
        http.postJson("api/vms", JSONObject().put("id", id).put("command", command).toString())
    }

    suspend fun scripts(): UnraidScriptList {
        val o = JSONObject(http.raw("api/scripts"))
        return UnraidScriptList(
            installed = o.optBoolean("installed"),
            starterRunning = o.optJSONObject("starter")?.optBoolean("running") ?: false,
            scripts = o.optJSONArray("scripts").objects().filter { !it.optBoolean("starter") }.map {
                UnraidScript(
                    id = it.optString("id"),
                    name = it.optString("name"),
                    description = it.optString("description"),
                    frequency = it.optString("frequency", "disabled"),
                    custom = it.optString("custom"),
                    running = it.optBoolean("running"),
                    last = it.optLong("last"),
                    foregroundOnly = it.optBoolean("foreground_only"),
                    arrayStarted = it.optBoolean("array_started"),
                )
            },
        )
    }

    /** Das Protokoll und ob es gekürzt ist. */
    suspend fun scriptLog(id: String): Pair<String, Boolean> {
        val o = JSONObject(http.raw("api/scripts/${enc(id)}/log"))
        return o.optString("log") to o.optBoolean("cut")
    }

    suspend fun deleteScriptLog(id: String) { http.delete("api/scripts/${enc(id)}/log") }

    suspend fun runScript(id: String) { http.postJson("api/scripts/${enc(id)}/run", "{}") }

    suspend fun abortScript(id: String) { http.postJson("api/scripts/${enc(id)}/abort", "{}") }

    suspend fun scheduleScript(id: String, frequency: String, custom: String) {
        http.putJson("api/scripts/${enc(id)}/schedule", JSONObject().put("frequency", frequency).put("custom", custom.trim()).toString())
    }

    private fun enc(name: String): String = Uri.encode(name)

    private fun storageText(s: JSONObject): String {
        fun label(x: String) = if (x == "array") "Array" else x
        val primary = label(s.optString("primary", "array"))
        val secondary = s.optString("secondary")
        if (secondary.isBlank()) return primary
        return if (s.optString("mover") == "to_primary") "${label(secondary)} → $primary" else "$primary → ${label(secondary)}"
    }

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }

    private fun JSONObject?.toMap(): Map<String, String> =
        if (this == null) emptyMap() else keys().asSequence().associateWith { optString(it) }
}
