package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PiholeTileTest {
    private fun inst(blocking: String = "enabled", timer: Int? = null, online: Boolean = true) =
        PhInstance("Pi", "http://192.168.1.2", online, if (online) "" else "Pi ist nicht erreichbar", blocking, timer, "v6.1")

    private fun overview(blocking: String, vararg instances: PhInstance) = PhOverview(true, false, blocking, instances.toList())

    @Test
    fun states() {
        assertEquals("Noch kein Pi-hole eingetragen", piholeTileText(PhOverview(false, false, "unknown", emptyList())))
        assertEquals("Werbefilter an", piholeTileText(overview("enabled", inst(), inst())))
        assertEquals("Werbefilter an, 1 nicht erreichbar", piholeTileText(overview("enabled", inst(), inst(online = false))))
        assertEquals("Pausiert, noch 5 min", piholeTileText(overview("disabled", inst("disabled", 241), inst("disabled", 299))))
        assertEquals("Pausiert ohne Ende", piholeTileText(overview("disabled", inst("disabled"))))
        assertEquals("Nicht überall gleich", piholeTileText(overview("mixed", inst(), inst("disabled", 30))))
        assertEquals("Kein Pi-hole erreichbar", piholeTileText(overview("unknown", inst(online = false))))
    }

    @Test
    fun alert() {
        assertFalse(piholeTileAlert(overview("enabled", inst())))
        assertTrue(piholeTileAlert(overview("disabled", inst("disabled", 30))))
        assertTrue(piholeTileAlert(overview("enabled", inst(), inst(online = false))))
    }
}
