package de.archonw.netmon

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.NumberFormat
import java.util.Locale

private const val PH_REFRESH_MS = 30_000L
private val PAUSES = listOf(30 to "30 s", 300 to "5 min", 1800 to "30 min", 3600 to "1 h")

private fun num(n: Long): String = NumberFormat.getIntegerInstance(Locale.GERMANY).format(n)

private fun minutes(seconds: Int) = "${(seconds + 59) / 60} min"

fun piholeTileText(o: PhOverview): String = when {
    !o.configured -> "Noch kein Pi-hole eingetragen"
    o.blocking == "enabled" -> o.instances.count { !it.online }.let { if (it > 0) t("Werbefilter an, {0} nicht erreichbar", it) else "Werbefilter an" }
    o.blocking == "disabled" -> o.instances.mapNotNull { it.timer }.maxOrNull()?.let { "Pausiert, noch ${minutes(it)}" } ?: "Pausiert ohne Ende"
    o.blocking == "mixed" -> "Nicht überall gleich"
    else -> "Kein Pi-hole erreichbar"
}

fun piholeTileAlert(o: PhOverview): Boolean = !o.configured || o.blocking != "enabled" || o.instances.any { !it.online }

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PiholeScreen(api: PiholeApi, onStats: () -> Unit, onOpen: (Int) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var confirmEndless by remember { mutableStateOf(false) }
    var confirmUpdate by remember { mutableStateOf(false) }
    var info by remember { mutableStateOf<String?>(null) }
    val (data, error) = rememberLoaded(reload, PH_REFRESH_MS) { api.overview() }

    fun run(action: suspend () -> PhPauseResult) {
        busy = true
        message = null
        scope.launch {
            try {
                val r = action()
                if (r.failed.isNotEmpty()) message = r.failed.joinToString("\n")
            } catch (e: ApiException) {
                message = e.message
            } finally {
                busy = false
                reload++
            }
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Pi-hole", onBack = onBack, trailing = {
            if (data?.configured == true) TextAction("Statistik", onStats)
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            info?.let { Text(it, color = p.allowed, fontSize = 14.sp) }
            when {
                data == null -> if (error == null) Text("Wird geladen …", color = p.muted)
                !data.configured -> HubCard {
                    Text("Es ist noch kein Pi-hole eingetragen.", color = p.text, fontSize = 15.sp)
                    Text("In Home-Net im Browser beim Modul Pi-hole unter Einstellungen eintragen.", color = p.muted, fontSize = 13.sp)
                }
                else -> {
                    val (state, color) = when (data.blocking) {
                        "enabled" -> "Werbefilter an" to p.allowed
                        "disabled" -> (data.instances.mapNotNull { it.timer }.maxOrNull()?.let { "Pausiert, noch ${minutes(it)}" } ?: "Pausiert ohne Ende") to p.unknown
                        "mixed" -> "Nicht überall gleich" to p.unknown
                        else -> "Kein Pi-hole erreichbar" to p.blocked
                    }
                    HubCard {
                        Text(state, color = color, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                        Text("Gilt für alle Pi-holes. Danach schalten sie sich von selbst wieder ein.", color = p.muted, fontSize = 13.sp)
                        if (data.blocking != "enabled" && data.blocking != "unknown") {
                            PillButton(if (busy) "Bitte warten …" else "Wieder einschalten", strong = true) { if (!busy) run { api.resume() } }
                        }
                        Text("Pausieren für", color = p.muted, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            PAUSES.forEach { (s, label) -> PillButton(label) { if (!busy) run { api.pause(s) } } }
                            if (data.manager) PillButton("Ohne Ende") { if (!busy) confirmEndless = true }
                        }
                    }
                    SectionTitle("Pi-holes")
                    data.instances.forEachIndexed { n, i ->
                        HubCard(onClick = { onOpen(n) }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(i.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                                Text(i.url.removePrefix("http://").removePrefix("https://"), color = p.muted, fontSize = 12.sp)
                            }
                            if (i.online) {
                                Text(
                                    listOf(
                                        if (i.blocking == "enabled") "Filter an" else i.timer?.let { "Pausiert, noch ${minutes(it)}" } ?: "Pausiert",
                                        i.version.takeIf { it.isNotBlank() }?.let { "Pi-hole $it" },
                                    ).filterNotNull().joinToString(" · "),
                                    color = if (i.blocking == "enabled") p.muted else p.unknown, fontSize = 13.sp,
                                )
                            } else {
                                Text(i.error, color = p.blocked, fontSize = 13.sp)
                            }
                            if (i.updates.isNotEmpty()) {
                                Text("Update verfügbar: ${i.updates.joinToString(", ")}", color = p.accentText, fontSize = 13.sp)
                            }
                        }
                    }
                    if (data.manager) {
                        PillButton("Alle aktualisieren", strong = data.instances.any { it.updates.isNotEmpty() }) { confirmUpdate = true }
                    }
                    Spacer(Modifier.height(16.dp))
                }
            }
        }
    }

    if (confirmEndless) {
        FormDialog(
            title = "Werbefilter ohne Ende pausieren?", confirm = "Pausieren",
            onDismiss = { confirmEndless = false },
            onConfirm = { api.pause(null).failed.takeIf { it.isNotEmpty() }?.let { message = it.joinToString("\n") } },
            onDone = { confirmEndless = false; reload++ },
        ) {
            Text("Er bleibt dann auf allen Pi-holes aus, bis jemand ihn wieder einschaltet.", color = LocalPalette.current.text, fontSize = 14.sp)
        }
    }

    if (confirmUpdate) {
        FormDialog(
            title = "Alle Pi-holes aktualisieren?", confirm = "Aktualisieren",
            onDismiss = { confirmUpdate = false },
            onConfirm = { api.update(null) },
            onDone = { confirmUpdate = false; info = "Update gestartet. Die Pi-holes werden nacheinander aktualisiert, den Stand siehst du beim jeweiligen Pi-hole."; reload++ },
        ) {
            Text("Die Pi-holes werden nacheinander aktualisiert, damit immer welche antworten. Das dauert ein paar Minuten.", color = LocalPalette.current.text, fontSize = 14.sp)
        }
    }
}

private fun duration(s: Long): String {
    val d = s / 86400
    val h = s % 86400 / 3600
    val m = s % 3600 / 60
    return if (d > 0) "$d Tg. $h Std." else if (h > 0) "$h Std. $m Min." else "$m Min."
}

private fun gb(b: Long): String =
    if (b >= 1L shl 30) String.format(Locale.GERMANY, "%.1f GB", b / 1073741824.0) else "${b / 1048576} MB"

@Composable
fun PiholeDetailScreen(api: PiholeApi, index: Int, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var confirmUpdate by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    val (d, error) = rememberLoaded(reload, PH_REFRESH_MS) { api.detail(index) }
    val updating = d?.updateJob?.busy == true
    LaunchedEffect(updating, reload) {
        if (updating) { delay(5_000); reload++ }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(d?.name ?: "Pi-hole", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (d == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
                return@Column
            }
            HubCard {
                Text(
                    if (d.blocking == "enabled") "Filter an" else d.timer?.let { "Pausiert, noch ${minutes(it)}" } ?: "Pausiert",
                    color = if (d.blocking == "enabled") p.allowed else p.unknown, fontSize = 18.sp, fontWeight = FontWeight.Bold,
                )
                Text(d.url, color = p.muted, fontSize = 13.sp)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatTile("Anfragen (24 h)", num(d.total), "${d.clients} Geräte aktiv", Modifier.weight(1f))
                StatTile("Geblockt", num(d.blocked), String.format(Locale.GERMANY, "%.1f %%", d.percent), Modifier.weight(1f))
            }
            SectionTitle("Anfragen")
            HubCard {
                InfoRow("Aus dem Zwischenspeicher", num(d.cached))
                InfoRow("Weitergeleitet", num(d.forwarded))
                InfoRow("Verschiedene Domains", num(d.uniqueDomains))
            }
            SectionTitle("Blocklisten")
            HubCard {
                InfoRow("Domains auf den Listen", num(d.gravity))
                if (d.lists > 0) InfoRow("Listen", d.lists.toString())
                InfoRow("Eigene Ausnahmen", d.ownAllowed.toString())
                InfoRow("Eigene Sperren", d.ownDenied.toString())
                if (d.gravityUpdate > 0) InfoRow("Stand", formatEpoch(d.gravityUpdate))
            }
            SectionTitle("Version")
            HubCard {
                d.versions.forEach { (part, v) -> InfoRow(part, v) }
                if (d.updates.isNotEmpty()) {
                    Text("Update verfügbar: ${d.updates.joinToString(", ")}", color = p.unknown, fontSize = 13.sp)
                } else {
                    Text("Aktuell", color = p.muted, fontSize = 13.sp)
                }
                d.updateJob?.let { UpdateJobText(it) }
                message?.let { Text(it, color = p.blocked, fontSize = 13.sp) }
                if (d.manager) {
                    Spacer(Modifier.height(4.dp))
                    PillButton(if (updating) "Update läuft …" else "Pi-hole aktualisieren", strong = d.updates.isNotEmpty()) {
                        if (!updating) confirmUpdate = true
                    }
                }
            }
            SectionTitle("Gerät")
            HubCard {
                if (d.model.isNotBlank()) InfoRow("Modell", d.model)
                if (d.hostname.isNotBlank()) InfoRow("Name", d.hostname)
                if (d.os.isNotBlank()) InfoRow("System", d.os)
                if (d.uptime > 0) InfoRow("Gerät läuft seit", duration(d.uptime))
                if (d.ftlUptime > 0) InfoRow("Pi-hole läuft seit", duration(d.ftlUptime))
                d.temp?.let { InfoRow("Temperatur", String.format(Locale.GERMANY, "%.1f °C", it)) }
                if (d.cpus > 0) InfoRow("Prozessor", t("{0} Kerne, Last {1}", d.cpus, d.load.joinToString(" / ") { String.format(Locale.GERMANY, "%.2f", it) }))
                if (d.ramTotal > 0) InfoRow("Arbeitsspeicher", "${gb(d.ramUsed)} von ${gb(d.ramTotal)} belegt")
            }
            if (d.messages.isNotEmpty()) {
                SectionTitle("Hinweise von Pi-hole")
                d.messages.forEach { m -> HubCard { Text(m, color = p.text, fontSize = 13.sp) } }
            }
            Spacer(Modifier.height(16.dp))
        }
    }

    if (confirmUpdate) {
        FormDialog(
            title = "${d?.name ?: "Pi-hole"} aktualisieren?", confirm = "Aktualisieren",
            onDismiss = { confirmUpdate = false },
            onConfirm = { message = null; api.update(index) },
            onDone = { confirmUpdate = false; reload++ },
        ) {
            Text("Das dauert ein paar Minuten. Solange beantworten die anderen Pi-holes die Anfragen.", color = p.text, fontSize = 14.sp)
        }
    }
}

@Composable
private fun UpdateJobText(job: PhJob) {
    val p = LocalPalette.current
    val (text, color) = when (job.state) {
        "waiting" -> "Wartet auf die anderen Pi-holes" to p.unknown
        "running" -> "Wird gerade aktualisiert …" to p.unknown
        "ok" -> "Update fertig" to p.allowed
        else -> "Update fehlgeschlagen" to p.blocked
    }
    Text(text + if (job.finished > 0) " · ${formatEpoch(job.finished)}" else "", color = color, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    if (job.log.isNotEmpty() && !job.busy) {
        Text(job.log.takeLast(if (job.state == "ok") 3 else 8).joinToString("\n"), color = p.muted, fontSize = 12.sp)
    }
}

@Composable
private fun PillButton(label: String, strong: Boolean = false, onClick: () -> Unit) {
    val p = LocalPalette.current
    Text(
        label, color = if (strong) p.onAccent else p.accentText, fontWeight = FontWeight.SemiBold, fontSize = 15.sp,
        modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(if (strong) p.accent else p.accentSoft)
            .clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 9.dp),
    )
}

@Composable
fun PiholeStatsScreen(api: PiholeApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    val (data, error) = rememberLoaded(0, 60_000L) { api.stats() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Statistik", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (data == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
                return@Column
            }
            data.errors.forEach { Text(it, color = p.blocked, fontSize = 13.sp) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatTile("Anfragen (24 h)", num(data.total), "${data.clientsActive} Geräte aktiv", Modifier.weight(1f))
                StatTile("Geblockt", num(data.blocked), String.format(Locale.GERMANY, "%.1f %%", data.percent), Modifier.weight(1f))
            }
            StatTile("Blockliste", num(data.gravity), if (data.gravityUpdate > 0) "Stand ${formatEpoch(data.gravityUpdate)}" else "", Modifier.fillMaxWidth())
            HubCard {
                Text("Verlauf (24 h)", color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                HistoryBars(data.history, p.line, p.accent)
                Text("Grau: alle Anfragen, farbig: davon geblockt", color = p.muted, fontSize = 12.sp)
            }
            CountCard("Am häufigsten geblockt", data.topBlocked)
            CountCard("Häufigste Geräte", data.topClients)
            CountCard("Am häufigsten erlaubt", data.topPermitted)
            Text("Zusammengezählt aus ${data.answered} von ${data.instances} Pi-holes.", color = p.muted, fontSize = 12.sp)
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun HistoryBars(history: List<PhSlot>, base: Color, blocked: Color) {
    val max = (history.maxOfOrNull { it.total } ?: 0L).coerceAtLeast(1L)
    Row(Modifier.fillMaxWidth().height(90.dp).padding(top = 6.dp), verticalAlignment = Alignment.Bottom) {
        history.forEach { h ->
            val share = h.total.toFloat() / max
            Box(Modifier.weight(1f).fillMaxHeight(share.coerceAtLeast(0.01f)).padding(horizontal = 0.3.dp).background(base), contentAlignment = Alignment.BottomCenter) {
                if (h.total > 0 && h.blocked > 0) {
                    Box(Modifier.fillMaxWidth().fillMaxHeight((h.blocked.toFloat() / h.total).coerceIn(0.02f, 1f)).background(blocked))
                }
            }
        }
    }
}

@Composable
private fun CountCard(title: String, items: List<PhCount>) {
    val p = LocalPalette.current
    HubCard {
        Text(title, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
        if (items.isEmpty()) Text("Noch nichts", color = p.muted, fontSize = 13.sp)
        items.forEach { c ->
            Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                Text(c.label, color = p.text, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Text(num(c.count), color = p.muted, fontSize = 14.sp)
            }
        }
    }
}
