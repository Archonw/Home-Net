package de.archonw.netmon

import org.junit.Assert.assertEquals
import org.junit.Test

class ListOptionsTest {
    private fun dev(name: String, online: Boolean, status: String, host: String = "") = Device(
        mac = name, name = name, hostname = host, ip = "", iface = "", status = status,
        online = online, randomMac = false, firstSeen = "", lastSeen = "",
    )

    private val devices = listOf(
        dev("zeta", online = true, status = "allowed"),
        dev("Äpfel", online = false, status = "unknown"),
        dev("beta", online = false, status = "blocked"),
        dev("Alpha", online = true, status = "unknown"),
    )

    private fun names(list: List<Device>) = list.map { it.name }

    @Test fun nameAscendingIgnoresCaseAndUmlauts() =
        assertEquals(listOf("Alpha", "Äpfel", "beta", "zeta"), names(arrange(devices, SortOrder.NAME_ASC)))

    @Test fun nameDescending() =
        assertEquals(listOf("zeta", "beta", "Äpfel", "Alpha"), names(arrange(devices, SortOrder.NAME_DESC)))

    @Test fun onlineFirstThenName() =
        assertEquals(listOf("Alpha", "zeta", "Äpfel", "beta"), names(arrange(devices, SortOrder.ONLINE_FIRST)))

    @Test fun offlineFirstThenName() =
        assertEquals(listOf("Äpfel", "beta", "Alpha", "zeta"), names(arrange(devices, SortOrder.OFFLINE_FIRST)))

    @Test fun allowedFirst() =
        assertEquals("zeta", arrange(devices, SortOrder.ALLOWED_FIRST).first().name)

    @Test fun notAllowedFirstUnknownBeforeBlocked() =
        assertEquals(listOf("Alpha", "Äpfel", "beta", "zeta"), names(arrange(devices, SortOrder.NOT_ALLOWED_FIRST)))

    @Test fun filters() {
        assertEquals(listOf("Alpha", "zeta"), names(arrange(devices, SortOrder.NAME_ASC, OnlineFilter.ONLINE)))
        assertEquals(listOf("Alpha", "Äpfel"), names(arrange(devices, SortOrder.NAME_ASC, status = StatusFilter.UNKNOWN)))
        assertEquals(listOf("Äpfel"), names(arrange(devices, SortOrder.NAME_ASC, OnlineFilter.OFFLINE, StatusFilter.UNKNOWN)))
    }

    @Test fun unnamedDevicesUseHostname() {
        val d = dev("", online = true, status = "allowed", host = "drucker")
        assertEquals("drucker", d.title)
    }
}
