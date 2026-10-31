package de.archonw.netmon

import java.text.Collator
import java.util.Locale

enum class SortOrder(val label: String) {
    NAME_ASC("Name A–Z"),
    NAME_DESC("Name Z–A"),
    IP_ASC("IP aufsteigend"),
    IP_DESC("IP absteigend"),
    ONLINE_FIRST("Online zuerst"),
    OFFLINE_FIRST("Offline zuerst"),
    ALLOWED_FIRST("Erlaubte zuerst"),
    NOT_ALLOWED_FIRST("Nicht erlaubte zuerst"),
}

enum class OnlineFilter(val label: String) {
    ALL("Alle"),
    ONLINE("Online"),
    OFFLINE("Offline"),
}

enum class StatusFilter(val label: String, val status: String?) {
    ALL("Alle", null),
    ALLOWED("Erlaubt", "allowed"),
    BLOCKED("Gesperrt", "blocked"),
    UNKNOWN("Unbekannt", "unknown"),
}

/** Ein Filter für beide Designs: Chips (Soft) bzw. Zählkacheln (Kacheln). */
enum class QuickFilter(val label: String, val online: OnlineFilter, val status: StatusFilter) {
    ALL("Alle", OnlineFilter.ALL, StatusFilter.ALL),
    ONLINE("Online", OnlineFilter.ONLINE, StatusFilter.ALL),
    UNKNOWN("Unbekannt", OnlineFilter.ALL, StatusFilter.UNKNOWN),
    BLOCKED("Gesperrt", OnlineFilter.ALL, StatusFilter.BLOCKED),
}

enum class ThemeMode(val label: String) {
    SYSTEM("Wie System"),
    LIGHT("Hell"),
    DARK("Dunkel"),
}

private val collator: Collator = Collator.getInstance(Locale.GERMAN).apply { strength = Collator.PRIMARY }

/** IPv4-Adresse als Zahl, damit .9 vor .10 kommt; null ohne (gültige) IP. */
fun ipKey(ip: String): Long? {
    val parts = ip.trim().split(".")
    if (parts.size != 4) return null
    var key = 0L
    for (part in parts) {
        val n = part.toIntOrNull() ?: return null
        if (n !in 0..255) return null
        key = key * 256 + n
    }
    return key
}

/** Suche über Name, Hostname, IP und MAC; ohne Suchbegriff bleibt die Liste unverändert. */
fun search(devices: List<Device>, query: String): List<Device> {
    val q = query.trim()
    if (q.isEmpty()) return devices
    return devices.filter { d ->
        listOf(d.name, d.hostname, d.ip, d.mac).any { it.contains(q, ignoreCase = true) }
    }
}

fun arrange(devices: List<Device>, sort: SortOrder, filter: QuickFilter, query: String = ""): List<Device> =
    arrange(search(devices, query), sort, filter.online, filter.status)

/** Filtert und sortiert die Geräteliste. Bei Gleichstand entscheidet immer der Name (A–Z). */
fun arrange(
    devices: List<Device>,
    sort: SortOrder,
    online: OnlineFilter = OnlineFilter.ALL,
    status: StatusFilter = StatusFilter.ALL,
): List<Device> {
    val byName = Comparator<Device> { a, b -> collator.compare(a.title, b.title) }
        .thenBy { it.mac }
    val filtered = devices.filter { d ->
        (online == OnlineFilter.ALL || d.online == (online == OnlineFilter.ONLINE)) &&
            (status.status == null || d.status == status.status)
    }
    val comparator = when (sort) {
        SortOrder.NAME_ASC -> byName
        SortOrder.NAME_DESC -> byName.reversed()
        // Geräte ohne IP stehen in beiden Richtungen am Ende
        SortOrder.IP_ASC -> compareBy<Device> { ipKey(it.ip) == null }.thenBy { ipKey(it.ip) }.then(byName)
        SortOrder.IP_DESC -> compareBy<Device> { ipKey(it.ip) == null }
            .thenByDescending { ipKey(it.ip) }.then(byName)
        SortOrder.ONLINE_FIRST -> compareBy<Device> { !it.online }.then(byName)
        SortOrder.OFFLINE_FIRST -> compareBy<Device> { it.online }.then(byName)
        SortOrder.ALLOWED_FIRST -> compareBy<Device> { it.status != "allowed" }.then(byName)
        SortOrder.NOT_ALLOWED_FIRST -> compareBy<Device> {
            when (it.status) {
                "unknown" -> 0
                "blocked" -> 1
                else -> 2
            }
        }.then(byName)
    }
    return filtered.sortedWith(comparator)
}
