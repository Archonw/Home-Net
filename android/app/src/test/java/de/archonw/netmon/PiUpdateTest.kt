package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Test

class PiUpdateTest {
    private fun row(updates: Int, state: String = "") =
        PiRow("a", "Fenster", "", false, "ok", 0, 1, 1, "daily", true, "", "", updates, state, "all")

    private fun software(count: Int, state: String = "") =
        PiSoftware(state, 0, count, 0, 0, false, "", "", 0, "", emptyList(), emptyList())

    @Test
    fun labels() {
        assertEquals("", piUpdateLabel(row(-1)))
        assertEquals("", piUpdateLabel(row(0)))
        assertEquals("1 Update", piUpdateLabel(row(1)))
        assertEquals("58 Updates", piUpdateLabel(row(58)))
        assertEquals("Update läuft", piUpdateLabel(row(58, "upgrading")))
        assertEquals("1 Pi", piCount(1))
        assertEquals("2 Pis", piCount(2))
    }

    @Test
    fun softwareState() {
        assertEquals("Noch nicht geprüft", piSoftwareState(software(-1)))
        assertEquals("Alles aktuell", piSoftwareState(software(0)))
        assertEquals("29 Updates verfügbar", piSoftwareState(software(29)))
        assertEquals("Wird geprüft …", piSoftwareState(software(29, "checking")))
    }
}
