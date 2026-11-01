package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EventLinesTest {
    @Test
    fun startThenMessageThenComment() {
        val parser = EventLines()
        assertNull(parser.feed("event: start"))
        assertEquals(EventLines.Latest(7), parser.feed("data: 7"))
        assertNull(parser.feed(""))
        assertNull(parser.feed(": ping"))
        assertNull(parser.feed("id: 8"))
    }
}
