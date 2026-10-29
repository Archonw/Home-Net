package de.archonw.netmon

import java.text.Collator
import java.util.Locale

enum class SortOrder(val label: String) {
    NAME_ASC("Name A–Z"),
    NAME_DESC("Name Z–A"),
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

enum class ThemeMode(val label: String) {
    SYSTEM("Wie System"),
    LIGHT("Hell"),
    DARK("Dunkel"),
}

private val collator: Collator = Collator.getInstance(Locale.GERMAN).apply { strength = Collator.PRIMARY }

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
