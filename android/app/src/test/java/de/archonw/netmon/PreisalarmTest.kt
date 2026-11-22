package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PreisalarmTest {
    private fun product(good: List<String> = emptyList()) = PaProduct(
        id = "a1b2c3d4e5f6", name = "Einhell Kit", url = "https://geizhals.example.de/kit", urls = listOf("https://geizhals.example.de/kit"), terms = listOf("Einhell 4,0 Ah"),
        target = 50.0, belowPct = 15, error = "", history = listOf(1700000000L to 69.9, 1700021600L to 49.99),
        price = 49.99, checked = 1700021600L, count = 2, average = 69.9, lowest = 69.9, good = good,
    )

    private fun overview(vararg products: PaProduct) = PaOverview(products.toList(), 1700021600L, true)

    @Test
    fun tile() {
        val o = overview(product(listOf("Zielpreis erreicht")), product())
        assertEquals("1 von 2 gerade günstig", preisalarmTileText(o))
        assertTrue(preisalarmTileAlert(o))
        assertEquals("2 Produkte im Blick", preisalarmTileText(overview(product(), product())))
        assertFalse(preisalarmTileAlert(overview(product(), product())))
        assertEquals("1 Produkt im Blick", preisalarmTileText(overview(product())))
        assertEquals("Noch kein Produkt eingetragen", preisalarmTileText(overview()))
    }

    @Test
    fun reasons() {
        assertEquals("Zielpreis erreicht, neuer Tiefstpreis", goodText(listOf("Zielpreis erreicht", "neuer Tiefstpreis")))
        assertEquals("Neuer Tiefstpreis", goodText(listOf("neuer Tiefstpreis")))
    }
}
