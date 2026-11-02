package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerWatchTest {
    private val quarter = 15 * 60_000L

    @Test
    fun warnsOnceAfterTimeAndFailsThenReportsRecovery() {
        val watch = ServerWatch(quarter, 0)
        assertNull(watch.failed(1000, true))
        assertNull(watch.failed(2000, true))
        assertNull(watch.failed(quarter - 1, true))
        assertEquals(ServerWatch.Change.DOWN, watch.failed(quarter, true))
        assertNull(watch.failed(quarter + 1, true))
        assertEquals(ServerWatch.Change.UP, watch.contact(quarter + 2))
        assertNull(watch.contact(quarter + 3))
    }

    @Test
    fun noInternetOnThePhoneNeverWarns() {
        val watch = ServerWatch(quarter, 0)
        repeat(10) { assertNull(watch.failed(quarter * (it + 1), false)) }
    }

    @Test
    fun shortOutagesStaySilent() {
        val watch = ServerWatch(quarter, 0)
        assertNull(watch.failed(quarter * 2, true))
        assertNull(watch.failed(quarter * 2 + 1, true))
        assertNull(watch.contact(quarter * 2 + 2))
        assertNull(watch.failed(quarter * 3, true))
    }
}

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
