package de.archonw.netmon

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test

class I18nTest {
    @After
    fun off() = I18n.load(emptyMap(), on = false)

    @Test
    fun exactTemplateAndNested() {
        I18n.load(
            mapOf(
                "Abbrechen" to "Cancel",
                "Sicherung von {0} läuft" to "Backup of {0} running",
                "{0} ▾" to "{0} ▾",
                "Name" to "Name",
                "Neueste zuerst" to "Newest first",
                "{0} von {1} Geräten" to "{0} of {1} devices",
            ),
        )
        assertEquals("Cancel", I18n.translate("Abbrechen"))
        assertEquals("  Cancel ", I18n.translate("  Abbrechen "))
        assertEquals("Backup of Küche running", I18n.translate("Sicherung von Küche läuft"))
        assertEquals("Newest first ▾", I18n.translate("Neueste zuerst ▾"))
        assertEquals("3 of 7 devices", I18n.translate("3 von 7 Geräten"))
        assertEquals("Unbekannt bleibt", I18n.translate("Unbekannt bleibt"))
        assertEquals("Backup of Pi running", t("Sicherung von {0} läuft", "Pi"))
        assertEquals("Cancel\nBackup of Pi running\nfrei", I18n.translate("Abbrechen\nSicherung von Pi läuft\nfrei"))
    }

    @Test
    fun germanStaysGerman() {
        I18n.load(mapOf("Abbrechen" to "Cancel"), on = false)
        assertEquals("Abbrechen", I18n.translate("Abbrechen"))
    }
}
