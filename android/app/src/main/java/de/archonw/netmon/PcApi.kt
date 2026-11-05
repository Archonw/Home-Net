package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

data class PcProgress(val step: String, val percent: Int, val bytes: Long, val log: String)

/** Ein laufender Auftrag: armed (wartet auf den PC), booting, running. */
data class PcJob(
    val action: String,
    val state: String,
    val runLabel: String,
    val cancel: Boolean,
    val progress: PcProgress?,
    /** Wartet auf das Wartungsfenster statt auf den nächsten Start. */
    val inWindow: Boolean = false,
) {
    val isRestore: Boolean get() = action == "restore"

    companion object {
        fun from(o: JSONObject?): PcJob? = o?.let {
            PcJob(
                action = it.optString("action"),
                state = it.optString("state"),
                runLabel = it.optString("run_label"),
                cancel = it.optBoolean("cancel"),
                progress = it.optJSONObject("progress")?.let { p ->
                    PcProgress(p.optString("step"), p.optInt("percent"), p.optLong("bytes"), p.optString("log"))
                },
                inWindow = it.optString("when") == "window",
            )
        }
    }
}

data class PcRow(
    val id: String,
    val name: String,
    val category: String,
    val job: PcJob?,
    val status: String,
    val message: String,
    val okTime: Long,
    val bytes: Long,
    val next: Long,
    /** Wartungsfenster wie »23–6 Uhr«, leer ohne. */
    val window: String = "",
    val windowOpen: Boolean = true,
) {
    companion object {
        fun from(o: JSONObject) = PcRow(
            id = o.getString("id"),
            name = o.optString("name"),
            category = o.optString("category"),
            job = PcJob.from(o.optJSONObject("job")),
            status = o.optString("status"),
            message = o.optString("message"),
            okTime = o.optLong("ok_time"),
            bytes = o.optLong("bytes"),
            next = o.optLong("next"),
            window = o.optString("window"),
            windowOpen = o.optBoolean("window_open", true),
        )
    }
}

/** Ein PC, der vom Stick gestartet hat und auf seinen Auftrag wartet. */
data class PcWaiting(val id: String, val mac: String, val label: String, val pcId: String, val since: Long, val disks: String)

data class PcOverview(
    val pcs: List<PcRow>,
    val waiting: List<PcWaiting>,
    val manager: Boolean,
    val bytes: Long,
    val availBytes: Long,
    val totalBytes: Long,
    val netbootOn: Boolean,
    val netbootError: String,
) {
    val problems: Int get() = pcs.count { it.job == null && (it.status == "failed" || it.status == "missed") }
    val busy: Int get() = pcs.count { it.job != null }
}

data class PcRun(val id: String, val label: String, val complete: Boolean, val added: Long)

data class PcHistory(
    val action: String,
    val result: String,
    val message: String,
    val start: Long,
    val end: Long,
    val added: Long,
    val processed: Long,
    val who: String,
)

data class PcDetail(
    val row: PcRow,
    val runs: List<PcRun>,
    val history: List<PcHistory>,
    val archiveError: String,
    val macs: List<String>,
    val after: String,
    val keep: Int,
    val notes: String,
    val manager: Boolean,
)

/** PC-Sicherung, nur über den gemeinsamen Zugang erreichbar. */
class PcApi(private val http: PiApi) {

    suspend fun overview(): PcOverview {
        val o = JSONObject(http.raw("api/overview"))
        val disk = o.optJSONObject("disk") ?: JSONObject()
        val netboot = o.optJSONObject("netboot") ?: JSONObject()
        return PcOverview(
            pcs = o.getJSONArray("pcs").objects().map(PcRow::from),
            waiting = o.optJSONArray("waiting").objects().map {
                val label = it.optString("pc_name").ifBlank {
                    listOf(it.optString("vendor"), it.optString("product")).filter(String::isNotBlank).joinToString(" ")
                }.ifBlank { "Unbekannter PC" }
                PcWaiting(it.getString("id"), it.optString("mac"), label, it.optString("pc"), it.optLong("since"), it.optString("disks"))
            },
            manager = o.optBoolean("manager"),
            bytes = o.optLong("bytes"),
            availBytes = disk.optLong("avail"),
            totalBytes = disk.optLong("total"),
            netbootOn = netboot.optBoolean("enabled"),
            netbootError = netboot.optString("error"),
        )
    }

    suspend fun detail(id: String): PcDetail {
        val o = JSONObject(http.raw("api/pcs/$id"))
        return PcDetail(
            row = PcRow.from(o),
            runs = o.optJSONArray("runs").objects().map {
                PcRun(it.getString("id"), it.optString("label"), it.optBoolean("complete"), it.optLong("added"))
            },
            history = o.optJSONArray("history").objects().map {
                PcHistory(
                    it.optString("action"), it.optString("result"), it.optString("message"), it.optLong("start"),
                    it.optLong("end"), it.optLong("added"), it.optLong("processed"), it.optString("who"),
                )
            },
            archiveError = o.optString("archive_error"),
            macs = o.optJSONArray("macs").strings(),
            after = o.optString("after", "poweroff"),
            keep = o.optInt("keep"),
            notes = o.optString("notes"),
            manager = o.optBoolean("manager"),
        )
    }

    /** [whenToRun] »next« (nächster Start) oder »window« (Wartungsfenster), null überlässt es dem PC. */
    suspend fun backup(id: String, after: String, whenToRun: String? = null) {
        http.postJson("api/pcs/$id/backup", JSONObject().put("after", after).putOpt("when", whenToRun).toString())
    }

    /** Schreibt den ganzen PC auf den Stand von [run] zurück. */
    suspend fun restore(id: String, run: String, after: String, whenToRun: String? = null) {
        http.postJson(
            "api/pcs/$id/restore",
            JSONObject().put("run", run).put("after", after).putOpt("when", whenToRun).toString(),
        )
    }

    suspend fun cancel(id: String) { http.postJson("api/pcs/$id/cancel", "{}") }

    suspend fun wake(id: String) { http.postJson("api/pcs/$id/wake", "{}") }

    /** Gibt einem vom Stick gestarteten PC seinen Auftrag; ohne [pcId] wird er als neuer PC [newName] angelegt. */
    suspend fun assign(waitingId: String, pcId: String?, newName: String, action: String, run: String?, after: String) {
        val body = JSONObject().put("action", action).put("after", after)
        if (pcId != null) body.put("pc", pcId) else body.put("new", JSONObject().put("name", newName.trim()))
        if (run != null) body.put("run", run)
        http.postJson("api/waiting/$waitingId/assign", body.toString())
    }

    suspend fun dismiss(waitingId: String) { http.delete("api/waiting/$waitingId") }

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
}
