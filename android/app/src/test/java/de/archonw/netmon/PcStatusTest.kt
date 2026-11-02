package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Test

class PcStatusTest {
    private fun row(status: String = "", job: PcJob? = null) =
        PcRow("a1b2c3d4e5f6", "Büro", "Familie", job, status, "", 0, 0, 0)

    private fun job(state: String, action: String = "backup", cancel: Boolean = false, percent: Int? = null) =
        PcJob(action, state, "02.11.26, 03:00", cancel, percent?.let { PcProgress("Platte 1", it, 0, "") })

    @Test
    fun lastResultWithoutJob() {
        assertEquals("Nie gesichert" to PcTone.QUIET, pcStatus(row()))
        assertEquals("Gesichert" to PcTone.OK, pcStatus(row("ok")))
        assertEquals("Fehler" to PcTone.BAD, pcStatus(row("failed")))
        assertEquals("Nicht gestartet" to PcTone.WARN, pcStatus(row("missed")))
    }

    @Test
    fun runningJobWinsOverLastResult() {
        assertEquals("Sicherung wartet auf den Start des PCs" to PcTone.RUN, pcStatus(row("failed", job("armed"))))
        assertEquals("Sicherung: PC startet das Sicherungssystem", pcStatus(row(job = job("booting"))).first)
        assertEquals("Wiederherstellung läuft (42 %)", pcStatus(row(job = job("running", "restore", percent = 42))).first)
        assertEquals("Sicherung läuft", pcStatus(row(job = job("running"))).first)
        assertEquals("Sicherung wird abgebrochen" to PcTone.WARN, pcStatus(row(job = job("running", cancel = true))))
    }

    @Test
    fun successfulRestoreReadsFinished() {
        val entry = PcHistory("restore", "ok", "", 0, 60, 0, 0, "")
        assertEquals("Fertig", pcResultText(entry).first)
        assertEquals("Gesichert", pcResultText(entry.copy(action = "backup")).first)
    }

    @Test
    fun tileNamesWhatNeedsAttention() {
        fun overview(pcs: List<PcRow>, waiting: Int = 0) = PcOverview(
            pcs, List(waiting) { PcWaiting("w$it", "", "PC", "", 0, "") }, true, 2048, 0, 0, true, "",
        )
        assertEquals("1 PC, 2 KB", pcTileText(overview(listOf(row("ok")))))
        assertEquals("1 PC, 2 KB, 1 Probleme", pcTileText(overview(listOf(row("failed")))))
        assertEquals("1 PC, 2 KB, 1 in Arbeit", pcTileText(overview(listOf(row(job = job("armed"))))))
        assertEquals("0 PCs, 2 KB, 1 wartet", pcTileText(overview(emptyList(), waiting = 1)))
    }
}
