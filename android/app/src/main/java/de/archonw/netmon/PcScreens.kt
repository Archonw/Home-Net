package de.archonw.netmon

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val PC_REFRESH_MS = 30_000L
private const val PC_BUSY_REFRESH_MS = 5_000L

private val AFTER_CHOICES = listOf("poweroff" to "Danach ausschalten", "reboot" to "Danach normal starten")

/** Art des Zustands, daraus wird die Farbe. */
enum class PcTone { OK, BAD, WARN, RUN, QUIET }

/** Was gerade mit dem PC los ist, wie in der Weboberfläche. */
fun pcStatus(row: PcRow): Pair<String, PcTone> {
    row.job?.let { return pcJobText(it) }
    return when (row.status) {
        "ok" -> "Gesichert" to PcTone.OK
        "failed" -> "Fehler" to PcTone.BAD
        "cancelled" -> "Abgebrochen" to PcTone.WARN
        "missed" -> "Nicht gestartet" to PcTone.WARN
        else -> "Nie gesichert" to PcTone.QUIET
    }
}

fun pcJobText(job: PcJob): Pair<String, PcTone> {
    val what = if (job.isRestore) "Wiederherstellung" else "Sicherung"
    return when {
        job.cancel -> "$what wird abgebrochen" to PcTone.WARN
        job.state == "armed" -> "$what wartet auf den Start des PCs" to PcTone.RUN
        job.state == "booting" -> "$what: PC startet das Sicherungssystem" to PcTone.RUN
        else -> "$what läuft" + (job.progress?.let { " (${it.percent} %)" } ?: "") to PcTone.RUN
    }
}

fun pcResultText(entry: PcHistory): Pair<String, PcTone> = when (entry.result) {
    "ok" -> (if (entry.action == "restore") "Fertig" else "Gesichert") to PcTone.OK
    "failed" -> "Fehler" to PcTone.BAD
    "cancelled" -> "Abgebrochen" to PcTone.WARN
    "missed" -> "Nicht gestartet" to PcTone.WARN
    else -> entry.result to PcTone.QUIET
}

@Composable
private fun toneColor(tone: PcTone): Color {
    val p = LocalPalette.current
    return when (tone) {
        PcTone.OK -> p.allowed
        PcTone.BAD -> p.blocked
        PcTone.WARN -> p.unknown
        PcTone.RUN -> p.accentText
        PcTone.QUIET -> p.muted
    }
}

/** Fasst die PC-Sicherung für die Kachel auf der Startseite zusammen. */
fun pcTileText(o: PcOverview): String =
    listOfNotNull(
        if (o.pcs.size == 1) "1 PC" else t("{0} PCs", o.pcs.size),
        formatBytes(o.bytes),
        when {
            o.waiting.isNotEmpty() -> t("{0} wartet", o.waiting.size)
            o.busy > 0 -> t("{0} in Arbeit", o.busy)
            o.problems > 0 -> t("{0} Probleme", o.problems)
            else -> null
        },
    ).joinToString(", ")

@Composable
fun PcOverviewScreen(api: PcApi, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var data by remember { mutableStateOf<PcOverview?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var assigning by remember { mutableStateOf<PcWaiting?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(reload) {
        while (true) {
            try {
                data = api.overview()
                error = null
            } catch (e: ApiException) {
                error = e.message
            }
            val o = data
            delay(if (o != null && (o.busy > 0 || o.waiting.isNotEmpty())) PC_BUSY_REFRESH_MS else PC_REFRESH_MS)
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("PC-Sicherung", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val o = data
            if (o == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("PCs", "${o.pcs.size}", "${o.busy} gerade in Arbeit", Modifier.weight(1f))
                    StatTile("Belegt", formatBytes(o.bytes), "alle Sicherungen", Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Speicher frei", formatBytes(o.availBytes), "von ${formatBytes(o.totalBytes)}", Modifier.weight(1f))
                    StatTile(
                        "Probleme", "${o.problems}", "letzte misslungen", Modifier.weight(1f),
                        color = if (o.problems > 0) p.blocked else p.allowed,
                    )
                }
                if (o.manager && !o.netbootOn) {
                    HubCard {
                        Text("Der Netzwerkstart ist noch aus.", color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text(
                            "Ohne ihn starten PCs nur vom Stick. Einschalten in der Weboberfläche unter Einstellungen.",
                            color = p.muted, fontSize = 13.sp,
                        )
                    }
                }
                if (o.netbootOn && o.netbootError.isNotBlank()) {
                    Text("Netzwerkstart gestört: ${o.netbootError}", color = p.blocked, fontSize = 14.sp)
                }
                if (o.waiting.isNotEmpty()) {
                    SectionTitle("Wartende PCs (vom Stick gestartet)")
                    o.waiting.forEach { w ->
                        HubCard {
                            Text(w.label, color = p.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                            Text(
                                listOf(w.mac, t("seit {0}", formatEpoch(w.since)), w.disks).filter { it.isNotBlank() }.joinToString(" · "),
                                color = p.muted, fontSize = 12.sp,
                            )
                            if (o.manager) {
                                Row {
                                    TextAction("Auftrag geben") { assigning = w }
                                    TextAction("Ignorieren") {
                                        scope.launch {
                                            try {
                                                api.dismiss(w.id)
                                                reload++
                                            } catch (e: ApiException) {
                                                error = e.message
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                o.pcs.groupBy { it.category.ifBlank { "Ohne Kategorie" } }.forEach { (category, rows) ->
                    SectionTitle(category)
                    rows.forEach { row -> PcRowCard(row) { onOpen(row.id) } }
                }
                if (o.pcs.isEmpty()) {
                    HubCard {
                        Text(
                            if (o.manager) "Noch keine PCs. Lege sie in der Weboberfläche an oder starte einen PC vom Stick."
                            else "Für dich sind keine PCs freigegeben.",
                            color = p.muted, fontSize = 14.sp,
                        )
                    }
                } else if (o.manager) {
                    Text(
                        "PCs anlegen und Zeitpläne ändern geht in der Weboberfläche.",
                        color = p.muted, fontSize = 12.sp,
                    )
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }

    assigning?.let { w ->
        AssignDialog(api, w, data?.pcs.orEmpty(), onDone = {
            assigning = null
            reload++
        }, onDismiss = { assigning = null })
    }
}

@Composable
private fun PcRowCard(row: PcRow, onClick: () -> Unit) {
    val p = LocalPalette.current
    val (text, tone) = pcStatus(row)
    HubCard(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(row.name, color = p.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Text("$text · ${formatBytes(row.bytes)}", color = toneColor(tone), fontSize = 13.sp)
                Text(
                    t("Gesichert {0}", formatEpoch(row.okTime)) + " · " + if (row.next > 0) t("nächste {0}", formatEpoch(row.next)) else t("kein Zeitplan"),
                    color = p.muted, fontSize = 12.sp,
                )
            }
            Icon(NavIcons.chevron, contentDescription = null, tint = p.muted)
        }
    }
}

@Composable
fun PcDetailScreen(api: PcApi, id: String, onBack: () -> Unit) {
    val p = LocalPalette.current
    var data by remember { mutableStateOf<PcDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var dialog by remember { mutableStateOf<String?>(null) }
    var restoreRun by remember { mutableStateOf<PcRun?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(reload) {
        while (true) {
            try {
                data = api.detail(id)
                error = null
            } catch (e: ApiException) {
                error = e.message
            }
            delay(if (data?.row?.job != null) PC_BUSY_REFRESH_MS else PC_REFRESH_MS)
        }
    }

    fun act(done: String? = null, action: suspend () -> Unit) {
        busy = true
        scope.launch {
            try {
                action()
                notice = done
                reload++
            } catch (e: ApiException) {
                error = e.message
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(data?.row?.name ?: "PC", onBack = onBack, trailing = {
            TextAction("Aufwecken") { act("Wake-on-LAN gesendet") { api.wake(id) } }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            notice?.let { Text(it, color = p.muted, fontSize = 13.sp) }
            val d = data
            if (d == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else {
                val (text, tone) = pcStatus(d.row)
                val lastEntry = d.history.firstOrNull()
                HubCard {
                    Text(text, color = toneColor(tone), fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Text(
                        "${d.row.category.ifBlank { "Ohne Kategorie" }} · behält ${d.keep} Sicherungen",
                        color = p.muted, fontSize = 13.sp,
                    )
                    Text(d.macs.joinToString(", "), color = p.muted, fontSize = 13.sp)
                    if (d.row.job == null && lastEntry != null && lastEntry.result != "ok" && lastEntry.message.isNotBlank()) {
                        Text(lastEntry.message, color = toneColor(pcResultText(lastEntry).second), fontSize = 14.sp)
                    }
                }
                d.row.job?.let { job ->
                    HubCard {
                        val progress = job.progress
                        Text(
                            progress?.step?.ifBlank { null }
                                ?: if (job.isRestore) "Wiederherstellung von ${job.runLabel}" else "Sicherung",
                            color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp,
                        )
                        if (progress != null) {
                            LinearProgressIndicator(
                                progress = { progress.percent / 100f },
                                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                                color = p.accent,
                                trackColor = p.line,
                            )
                            Text(
                                if (progress.bytes > 0) t("{0} % · {1} gelesen", progress.percent, formatBytes(progress.bytes)) else "${progress.percent} %",
                                color = p.muted, fontSize = 12.sp,
                            )
                        }
                        if (job.state == "armed") {
                            Text(
                                "Der PC wurde per Wake-on-LAN geweckt. Läuft er schon, bitte neu starten: Er startet dann " +
                                    "übers Netz. Ohne Netzwerkstart den Stick einstecken.",
                                color = p.muted, fontSize = 13.sp,
                            )
                        }
                        if (progress != null && progress.log.isNotBlank()) {
                            Text(progress.log, color = p.muted, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                        }
                    }
                    PrimaryButton(if (busy) "Bitte warten …" else "Auftrag abbrechen", Modifier.fillMaxWidth(), enabled = !busy) {
                        dialog = "cancel"
                    }
                }
                if (d.row.job == null) {
                    PrimaryButton(if (busy) "Bitte warten …" else "Jetzt sichern", Modifier.fillMaxWidth(), enabled = !busy) {
                        dialog = "backup"
                    }
                    val newest = d.runs.firstOrNull { it.complete }
                    if (d.manager && newest != null) {
                        TextAction("Ganzen PC wiederherstellen") { restoreRun = newest }
                    }
                }
                if (d.archiveError.isNotBlank()) {
                    Text("Archiv nicht lesbar: ${d.archiveError}", color = p.blocked, fontSize = 14.sp)
                }
                SectionTitle("Sicherungen")
                HubCard {
                    if (d.runs.isEmpty()) Text("Noch keine Sicherung", color = p.muted, fontSize = 14.sp)
                    d.runs.forEach { run ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(run.label, color = p.text, fontSize = 14.sp)
                                Text(
                                    when {
                                        !run.complete -> "unvollständig, wird beim Aufräumen entfernt"
                                        run.added > 0 -> "${formatBytes(run.added)} neu gespeichert"
                                        else -> "vollständig"
                                    },
                                    color = if (run.complete) p.muted else p.unknown, fontSize = 12.sp,
                                )
                            }
                            if (d.manager && run.complete && d.row.job == null) {
                                TextAction("Zurückschreiben") { restoreRun = run }
                            }
                        }
                        Spacer(Modifier.height(4.dp))
                    }
                }
                if (d.history.isNotEmpty()) {
                    SectionTitle("Verlauf")
                    HubCard {
                        d.history.take(15).forEach { e ->
                            val (result, resultTone) = pcResultText(e)
                            val minutes = maxOf(1, ((e.end - e.start) / 60).toInt())
                            Text(
                                "${if (e.action == "restore") "Wiederherstellung" else "Sicherung"}: $result",
                                color = toneColor(resultTone), fontSize = 14.sp,
                            )
                            Text(
                                "${formatEpoch(e.end)} · $minutes min" + (if (e.who.isNotBlank()) " · ${e.who}" else ""),
                                color = p.muted, fontSize = 12.sp,
                            )
                            val extra = listOfNotNull(
                                e.message.ifBlank { null },
                                if (e.action == "backup" && e.result == "ok") {
                                    "gelesen ${formatBytes(e.processed)}, neu ${formatBytes(e.added)}"
                                } else null,
                            )
                            if (extra.isNotEmpty()) Text(extra.joinToString(" · "), color = p.muted, fontSize = 12.sp)
                            Spacer(Modifier.height(4.dp))
                        }
                    }
                }
                if (d.notes.isNotBlank()) {
                    SectionTitle("Notizen")
                    HubCard { Text(d.notes, color = p.text, fontSize = 14.sp) }
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }

    val d = data
    when (dialog) {
        "cancel" -> AlertDialog(
            onDismissRequest = { dialog = null },
            title = { Text("Auftrag abbrechen?") },
            text = { Text("Vorhandene Sicherungen bleiben erhalten.") },
            confirmButton = {
                TextButton(onClick = {
                    dialog = null
                    act { api.cancel(id) }
                }) { Text("Ja") }
            },
            dismissButton = { TextButton(onClick = { dialog = null }) { Text("Nein") } },
        )
        "backup" -> if (d != null) {
            var after by remember { mutableStateOf(d.after) }
            AlertDialog(
                onDismissRequest = { dialog = null },
                title = { Text("Sicherung starten?") },
                text = {
                    Column {
                        Text(
                            "Der Server weckt den PC. Er startet übers Netz oder vom Stick, sichert alle Platten und " +
                                "ist währenddessen nicht nutzbar.",
                            fontSize = 14.sp,
                        )
                        Spacer(Modifier.height(8.dp))
                        Choices(AFTER_CHOICES, after) { after = it }
                    }
                },
                confirmButton = {
                    TextButton(onClick = {
                        dialog = null
                        act { api.backup(id, after) }
                    }) { Text("Starten") }
                },
                dismissButton = { TextButton(onClick = { dialog = null }) { Text("Abbrechen") } },
            )
        }
    }

    restoreRun?.let { run ->
        RestoreDialog(
            name = d?.row?.name ?: "PC",
            runs = d?.runs.orEmpty().filter { it.complete },
            initial = run,
            onConfirm = { chosen, after ->
                restoreRun = null
                act { api.restore(id, chosen.id, after) }
            },
            onDismiss = { restoreRun = null },
        )
    }
}

@Composable
private fun Choices(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
    Column {
        options.forEach { (value, label) ->
            Row(
                Modifier.fillMaxWidth().clickable { onSelect(value) }.heightIn(min = 40.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = value == selected, onClick = { onSelect(value) })
                Text(label, fontSize = 14.sp)
            }
        }
    }
}

/** Zweistufig: erst Stand wählen, dann ausdrücklich bestätigen, denn alle Platten werden überschrieben. */
@Composable
private fun RestoreDialog(
    name: String,
    runs: List<PcRun>,
    initial: PcRun,
    onConfirm: (PcRun, String) -> Unit,
    onDismiss: () -> Unit,
) {
    var run by remember { mutableStateOf(initial) }
    var after by remember { mutableStateOf("reboot") }
    var sure by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (sure) "Wirklich überschreiben?" else "$name wiederherstellen") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (!sure) {
                    Text("Stand", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Choices(runs.map { it.id to it.label }, run.id) { picked -> run = runs.first { it.id == picked } }
                    Spacer(Modifier.height(8.dp))
                    Choices(AFTER_CHOICES, after) { after = it }
                } else {
                    Text(
                        "Alle Platten von $name werden auf den Stand ${run.label} zurückgesetzt. Was seitdem auf dem PC " +
                            "gespeichert wurde, geht verloren. Einzelne Partitionen lassen sich in der Weboberfläche wählen.",
                        fontSize = 14.sp,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { if (sure) onConfirm(run, after) else sure = true }) {
                Text(if (sure) "Ja, überschreiben" else "Weiter")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Abbrechen") } },
    )
}

/** Auftrag für einen PC, der vom Stick gestartet hat: sichern oder (bei einem bekannten PC) zurückschreiben. */
@Composable
private fun AssignDialog(api: PcApi, waiting: PcWaiting, pcs: List<PcRow>, onDone: () -> Unit, onDismiss: () -> Unit) {
    val p = LocalPalette.current
    var target by remember { mutableStateOf(waiting.pcId.ifBlank { pcs.firstOrNull()?.id ?: NEW_PC }) }
    var newName by remember { mutableStateOf("") }
    var action by remember { mutableStateOf("backup") }
    var runs by remember { mutableStateOf<List<PcRun>>(emptyList()) }
    var run by remember { mutableStateOf<String?>(null) }
    var after by remember { mutableStateOf("reboot") }
    var sure by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(target) {
        runs = emptyList()
        run = null
        if (target == NEW_PC) {
            action = "backup"
            return@LaunchedEffect
        }
        try {
            runs = api.detail(target).runs.filter { it.complete }
            run = runs.firstOrNull()?.id
            if (runs.isEmpty()) action = "backup"
        } catch (e: ApiException) {
            error = e.message
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (sure) "Wirklich überschreiben?" else "Auftrag für ${waiting.label}") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (sure) {
                    val name = pcs.firstOrNull { it.id == target }?.name ?: "den PC"
                    val label = runs.firstOrNull { it.id == run }?.label.orEmpty()
                    Text(
                        "Alle Platten dieses PCs werden mit der Sicherung von $name vom $label überschrieben.",
                        fontSize = 14.sp,
                    )
                } else {
                    Text("PC", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Choices(pcs.map { it.id to it.name } + (NEW_PC to "Neuer PC"), target) { target = it }
                    if (target == NEW_PC) {
                        OutlinedTextField(
                            value = newName, onValueChange = { newName = it }, singleLine = true,
                            label = { Text("Name") }, modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    if (runs.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text("Aufgabe", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        Choices(listOf("backup" to "Sichern", "restore" to "Zurückschreiben"), action) { action = it }
                        if (action == "restore") {
                            Choices(runs.map { it.id to it.label }, run.orEmpty()) { run = it }
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Choices(AFTER_CHOICES, after) { after = it }
                }
                error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            }
        },
        confirmButton = {
            TextButton(enabled = !busy, onClick = {
                if (target == NEW_PC && newName.isBlank()) {
                    error = "Bitte einen Namen eintragen."
                    return@TextButton
                }
                if (action == "restore" && !sure) {
                    sure = true
                    return@TextButton
                }
                busy = true
                scope.launch {
                    try {
                        api.assign(
                            waiting.id, target.takeIf { it != NEW_PC }, newName, action,
                            run.takeIf { action == "restore" }, after,
                        )
                        onDone()
                    } catch (e: ApiException) {
                        error = e.message
                        sure = false
                    } finally {
                        busy = false
                    }
                }
            }) { Text(if (action == "restore" && sure) "Ja, überschreiben" else if (action == "restore") "Weiter" else "Starten") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Abbrechen") } },
    )
}

private const val NEW_PC = "neu"
