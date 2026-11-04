package de.archonw.netmon

import androidx.compose.foundation.background
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
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val UNRAID_REFRESH_MS = 20_000L

private val RIGHT_LABELS = listOf("read-write" to "Lesen und schreiben", "read-only" to "Nur lesen", "no-access" to "Kein Zugriff")
private val SECURITY_LABELS = listOf(
    "public" to "Öffentlich: alle lesen und schreiben",
    "secure" to "Geschützt: alle lesen, Ausgewählte schreiben",
    "private" to "Privat: nur Ausgewählte",
)

/** Fasst den Server für die Kachel auf der Startseite zusammen. */
fun unraidTileText(o: UnraidOverview): String {
    if (!o.available) return "Keine Daten von Unraid"
    val array = when {
        !o.started -> "Array gestoppt"
        o.check.running -> o.check.percent?.let { t("Prüfung {0}", "%.0f %%".format(it)) } ?: "Prüfung läuft"
        else -> "Array läuft"
    }
    return t(array) + ", " + when {
        o.problems > 0 -> t("{0} Probleme", o.problems)
        o.warnings > 0 -> t("{0} Platte warm", o.warnings)
        else -> t("alle Platten in Ordnung")
    }
}

private fun uptimeText(seconds: Long): String {
    val days = seconds / 86400
    val hours = seconds % 86400 / 3600
    return if (days > 0) "$days Tage $hours Std." else "$hours Std. ${seconds % 3600 / 60} Min."
}

/** Lädt [load] neu, sobald sich [reload] ändert, und dann alle [every] Millisekunden. */
@Composable
internal fun <T> rememberLoaded(reload: Int, every: Long, load: suspend () -> T): Pair<T?, String?> {
    var data by remember { mutableStateOf<T?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(reload) {
        while (true) {
            try {
                data = load()
                error = null
            } catch (e: ApiException) {
                error = e.message
            }
            delay(every)
        }
    }
    return data to error
}

@Composable
fun UnraidOverviewScreen(
    api: UnraidApi,
    onShares: () -> Unit,
    onUsers: () -> Unit,
    onDocker: () -> Unit,
    onScripts: () -> Unit,
    settings: Settings,
    onBack: () -> Unit,
) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var arranging by remember { mutableStateOf(false) }
    var sections by remember { mutableStateOf(settings.unraidSections) }
    var menuOrder by remember { mutableStateOf(settings.unraidMenu) }
    var actionError by remember { mutableStateOf<String?>(null) }
    var confirm by remember { mutableStateOf<Pair<String, String>?>(null) }
    val scope = rememberCoroutineScope()
    val (data, error) = rememberLoaded(reload, UNRAID_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(data?.name?.takeIf { it.isNotBlank() }?.let { "Unraid: $it" } ?: "Unraid", onBack = onBack, trailing = {
            if (data?.available == true && !arranging) TextAction("Anordnen") { arranging = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            (actionError ?: error)?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val o = data
            if (o == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else if (!o.available) {
                Text("Die Daten von Unraid sind nicht sichtbar. Läuft Home-Net direkt auf dem Unraid-Server?", color = p.blocked)
            } else {
                if (arranging) {
                    ArrangeEditor(
                        sections, { sections = it; settings.unraidSections = it },
                        menuOrder, { menuOrder = it; settings.unraidMenu = it },
                    ) { arranging = false }
                }
                if (!arranging) sections.forEach { section ->
                    when (section) {
                        "tiles" -> {
                            val used = if (o.memTotal > 0) ((o.memTotal - o.memAvailable) * 100 / o.memTotal).toInt() else 0
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                StatTile(
                                    "Array", if (!o.started) o.arrayState.ifBlank { "Gestoppt" } else if (o.check.running) "Prüfung" else "Gestartet",
                                    if (o.missing > 0) "${o.missing} Platte fehlt" else "Unraid ${o.version}", Modifier.weight(1f),
                                    color = if (!o.started || o.missing > 0) p.blocked else p.allowed,
                                )
                                StatTile("Läuft seit", uptimeText(o.uptime), "Last ${"%.1f".format(o.load)} bei ${o.cpus} Kernen", Modifier.weight(1f))
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                StatTile("Arbeitsspeicher", "$used %", "von ${formatBytes(o.memTotal)}", Modifier.weight(1f))
                                StatTile(
                                    "Probleme", "${o.problems}", if (o.warnings > 0) "${o.warnings} Platte warm" else "Platten und Array",
                                    Modifier.weight(1f), color = if (o.problems > 0) p.blocked else if (o.warnings > 0) p.unknown else p.allowed,
                                )
                            }
                        }
                        "menu" -> {
                            if (o.manager) {
                                HubCard {
                                    menuOrder.forEach { item ->
                                        when (item) {
                                            "shares" -> NavRow("Freigaben", onShares)
                                            "users" -> NavRow("Benutzer", onUsers)
                                            "docker" -> NavRow("Docker und VMs", onDocker)
                                            "scripts" -> { if (o.userScripts) NavRow("User Scripts", onScripts) }
                                        }
                                    }
                                }
                            }
                        }
                        "disks" -> {
                            SectionTitle("Platten")
                            HubCard {
                                o.disks.forEach { d ->
                                    val kind = when (d.kind) { "parity" -> "Parität"; "data" -> "Daten"; "pool" -> "Pool"; else -> "USB-Stick" }
                                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Column(Modifier.weight(1f)) {
                                            Text("${d.name} · $kind", color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                            if (d.mounted && d.size > 0) {
                                                Text("${formatBytes(d.free)} frei von ${formatBytes(d.size)}", color = p.muted, fontSize = 12.sp)
                                            }
                                        }
                                        Column(horizontalAlignment = Alignment.End) {
                                            Text(
                                                if (d.spundown) "schläft" else d.temp?.let { "$it °C" } ?: "",
                                                color = when { o.critical(d) -> p.blocked; o.hot(d) -> p.unknown; else -> p.muted }, fontSize = 13.sp,
                                            )
                                            Text(
                                                if (d.errors > 0) "${d.statusText}, ${d.errors} Fehler" else d.statusText,
                                                color = if (d.ok && d.errors == 0) p.allowed else p.blocked, fontSize = 13.sp,
                                            )
                                        }
                                    }
                                }
                            }
                        }
                        "parity" -> {
                            SectionTitle("Paritätsprüfung")
                            HubCard {
                                val c = o.check
                                if (c.running) {
                                    Text(if (c.paused) "Angehalten" else "Läuft: ${"%.1f".format(c.percent ?: 0.0)} %", color = p.accentText, fontWeight = FontWeight.SemiBold)
                                    LinearProgressIndicator(
                                        progress = { ((c.percent ?: 0.0) / 100).toFloat() },
                                        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp), color = p.accent, trackColor = p.line,
                                    )
                                } else {
                                    Text(
                                        if (c.last > 0) t("Zuletzt {0}: {1}", formatEpoch(c.last), when {
                                            c.lastExit != 0 -> t("abgebrochen")
                                            c.lastErrors > 0 -> t("{0} Fehler", c.lastErrors)
                                            else -> t("keine Fehler")
                                        }) else "Noch keine Prüfung",
                                        color = if (c.lastErrors > 0) p.blocked else p.text, fontSize = 14.sp,
                                    )
                                }
                                if (o.manager && o.writable && o.started) {
                                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                        if (c.running) {
                                            if (c.paused) TextAction("Fortsetzen") { confirm = "resume" to "Prüfung fortsetzen?" }
                                            else TextAction("Anhalten") { confirm = "pause" to "Prüfung anhalten?" }
                                            TextAction("Abbrechen") { confirm = "cancel" to "Laufende Prüfung abbrechen?" }
                                        } else {
                                            TextAction("Prüfung starten") { confirm = "check" to "Paritätsprüfung mit Korrektur starten? Die Platten sind dabei viele Stunden stark beschäftigt." }
                                            if (!o.mover) TextAction("Mover starten") { confirm = "mover" to "Mover jetzt starten?" }
                                        }
                                    }
                                }
                            }
                        }
                        "notes" -> {
                            if (o.notes.isNotEmpty()) {
                                SectionTitle("Ungelesene Meldungen von Unraid")
                                HubCard {
                                    o.notes.forEach { n ->
                                        Text(
                                            n.subject, fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                                            color = when (n.importance) { "alert" -> p.blocked; "warning" -> p.unknown; else -> p.text },
                                        )
                                        Text("${n.description} · ${formatEpoch(n.time)}", color = p.muted, fontSize = 12.sp)
                                        Spacer(Modifier.height(4.dp))
                                    }
                                }
                            }
                        }
                    }
                }
                Text("Dateien und Einstellungen gibt es in der Weboberfläche.", color = p.muted, fontSize = 12.sp)
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    confirm?.let { (command, question) ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(question) },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    scope.launch {
                        try {
                            api.arrayCommand(command)
                            actionError = null
                            reload++
                        } catch (e: ApiException) {
                            actionError = e.message
                        }
                    }
                }) { Text("Ja") }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Nein") } },
        )
    }
}

val UNRAID_SECTIONS = listOf("tiles", "menu", "disks", "parity", "notes")
val UNRAID_MENU = listOf("shares", "users", "docker", "scripts")
private val SECTION_LABELS = mapOf(
    "tiles" to "Kacheln (Array, Speicher, Probleme)",
    "menu" to "Menü",
    "disks" to "Platten",
    "parity" to "Paritätsprüfung",
    "notes" to "Meldungen von Unraid",
)
private val MENU_LABELS = mapOf("shares" to "Freigaben", "users" to "Benutzer", "docker" to "Docker und VMs", "scripts" to "User Scripts")

@Composable
private fun ArrangeEditor(
    sections: List<String>,
    onSections: (List<String>) -> Unit,
    menu: List<String>,
    onMenu: (List<String>) -> Unit,
    onDone: () -> Unit,
) {
    ArrangeHint()
    SectionTitle("Bereiche der Übersicht")
    OrderList(sections, SECTION_LABELS, onSections)
    SectionTitle("Menüpunkte")
    OrderList(menu, MENU_LABELS, onMenu)
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        TextAction("Zurücksetzen") { onSections(UNRAID_SECTIONS); onMenu(UNRAID_MENU) }
        TextAction("Fertig", onDone)
    }
}

@Composable
private fun NavRow(label: String, onClick: () -> Unit) {
    val p = LocalPalette.current
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = p.text, fontSize = 16.sp, modifier = Modifier.weight(1f))
        Icon(NavIcons.chevron, contentDescription = null, tint = p.muted)
    }
}

@Composable
private fun Choice(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
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

/** Führt [action] aus und meldet Fehler im Dialog statt ihn zu schließen. */
private class Submitter {
    var busy by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
}

@Composable
internal fun FormDialog(
    title: String,
    confirm: String,
    onDismiss: () -> Unit,
    onConfirm: suspend () -> Unit,
    onDone: () -> Unit,
    content: @Composable () -> Unit,
) {
    val state = remember { Submitter() }
    val scope = rememberCoroutineScope()
    AlertDialog(
        onDismissRequest = { if (!state.busy) onDismiss() },
        title = { Text(title) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                content()
                state.error?.let { Text(it, color = LocalPalette.current.blocked, fontSize = 14.sp) }
            }
        },
        confirmButton = {
            TextButton(enabled = !state.busy, onClick = {
                state.busy = true
                state.error = null
                scope.launch {
                    try {
                        onConfirm()
                        onDone()
                    } catch (e: ApiException) {
                        state.error = e.message
                    } catch (e: IllegalArgumentException) {
                        state.error = e.message
                    } finally {
                        state.busy = false
                    }
                }
            }) { Text(if (state.busy) "Bitte warten …" else confirm) }
        },
        dismissButton = { TextButton(enabled = !state.busy, onClick = onDismiss) { Text("Abbrechen") } },
    )
}

@Composable
fun UnraidSharesScreen(api: UnraidApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var editing by remember { mutableStateOf<UnraidShare?>(null) }
    var adding by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf<UnraidShare?>(null) }
    val (data, error) = rememberLoaded(reload, 60_000L) { api.shares() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Freigaben", onBack = onBack, trailing = {
            if (data?.writable == true) TextAction("Neu") { adding = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val d = data
            if (d == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else {
                if (!d.writable) Text("Änderungen sind für diese Unraid-Version noch nicht freigegeben.", color = p.unknown, fontSize = 13.sp)
                d.shares.forEach { s ->
                    HubCard(onClick = if (d.writable) ({ editing = s }) else null) {
                        Text(s.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                        val net = if (s.export == "-") "nicht im Netz" else SECURITY_LABELS.first { it.first == s.security }.second.substringBefore(":")
                        Text(listOf(s.comment, s.storage, "SMB $net").filter { it.isNotBlank() }.joinToString(" · "), color = p.muted, fontSize = 13.sp)
                        if (s.security != "public" && s.export != "-") {
                            val rights = d.users.mapNotNull { u ->
                                when (s.rightOf(u)) { "read-write" -> u; "read-only" -> "$u (lesen)"; else -> null }
                            }
                            Text(if (rights.isEmpty()) "Niemand hat Zugriff" else rights.joinToString(", "), color = p.muted, fontSize = 13.sp)
                        }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    val d = data
    editing?.let { share ->
        if (d == null) return@let
        var export by remember(share) { mutableStateOf(if (share.export == "-") "-" else share.export) }
        var security by remember(share) { mutableStateOf(share.security) }
        val rights = remember(share) { mutableStateMapOf<String, String>().apply { d.users.forEach { put(it, share.rightOf(it)) } } }
        FormDialog(
            title = share.name, confirm = "Speichern", onDismiss = { editing = null },
            onConfirm = {
                if (export != share.export || security != share.security) api.shareSecurity(share.name, export, security)
                if (security != "public") api.shareAccess(share.name, security, rights.toMap())
            },
            onDone = { editing = null; reload++ },
        ) {
            Text("Im Netzwerk freigeben", fontWeight = FontWeight.SemiBold)
            Choice(listOf("e" to "Ja", "eh" to "Ja, versteckt", "-" to "Nein"), if (export in setOf("e", "eh", "-")) export else "e") { export = it }
            Text("Sicherheit", fontWeight = FontWeight.SemiBold)
            Choice(SECURITY_LABELS, security) { security = it }
            if (security != "public") {
                d.users.forEach { user ->
                    Text(user, fontWeight = FontWeight.SemiBold)
                    Choice(if (security == "secure") RIGHT_LABELS.take(2) else RIGHT_LABELS, rights[user] ?: "no-access") { rights[user] = it }
                }
            }
            TextAction("Freigabe löschen …") { editing = null; deleting = share }
        }
    }
    if (adding && d != null) {
        var name by remember { mutableStateOf("") }
        var comment by remember { mutableStateOf("") }
        var place by remember { mutableStateOf(if (d.pools.isNotEmpty()) "${d.pools.first()}|array" else "array|") }
        var security by remember { mutableStateOf("private") }
        val rights = remember { mutableStateMapOf<String, String>().apply { d.users.forEach { put(it, "no-access") } } }
        val places = listOf("array|" to "Nur Array") + d.pools.flatMap { pool ->
            listOf("$pool|array" to "Erst $pool, dann Mover aufs Array", "$pool|" to "Nur $pool")
        }
        FormDialog(
            title = "Freigabe anlegen", confirm = "Anlegen", onDismiss = { adding = false },
            onConfirm = {
                require(name.isNotBlank()) { "Bitte einen Namen angeben." }
                val (primary, secondary) = place.split("|")
                api.addShare(name, comment, primary, secondary, security, rights.toMap())
            },
            onDone = { adding = false; reload++ },
        ) {
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(comment, { comment = it }, label = { Text("Beschreibung") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text("Speicherort", fontWeight = FontWeight.SemiBold)
            Choice(places, place) { place = it }
            Text("Sicherheit im Netzwerk", fontWeight = FontWeight.SemiBold)
            Choice(SECURITY_LABELS, security) { security = it }
            if (security != "public") {
                d.users.forEach { user ->
                    Text(user, fontWeight = FontWeight.SemiBold)
                    Choice(if (security == "secure") RIGHT_LABELS.take(2) else RIGHT_LABELS, rights[user] ?: "no-access") { rights[user] = it }
                }
            }
        }
    }
    deleting?.let { share ->
        FormDialog(
            title = "${share.name} löschen?", confirm = "Löschen", onDismiss = { deleting = null },
            onConfirm = { api.deleteShare(share.name) }, onDone = { deleting = null; reload++ },
        ) { Text("Unraid löscht nur leere Freigaben.") }
    }
}

@Composable
fun UnraidUsersScreen(api: UnraidApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var adding by remember { mutableStateOf(false) }
    var password by remember { mutableStateOf<UnraidUser?>(null) }
    var deleting by remember { mutableStateOf<UnraidUser?>(null) }
    val (data, error) = rememberLoaded(reload, 60_000L) { api.users() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Benutzer", onBack = onBack, trailing = {
            if (data?.second == true) TextAction("Neu") { adding = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val (users, writable) = data ?: (null to false)
            if (users == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else {
                users.forEach { u ->
                    HubCard {
                        Text(u.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                        val shares = u.rights.filterValues { it != "no-access" }.map { (s, r) -> if (r == "read-only") "$s (lesen)" else s }
                        Text(
                            if (u.name == "root") "Anmeldung an Unraid selbst"
                            else listOf(u.desc, if (shares.isEmpty()) "keine Freigaben" else shares.sorted().joinToString(", ")).filter { it.isNotBlank() }.joinToString(" · "),
                            color = p.muted, fontSize = 13.sp,
                        )
                        if (writable) {
                            Row {
                                TextAction("Passwort ändern") { password = u }
                                if (u.name != "root") TextAction("Löschen") { deleting = u }
                            }
                        }
                    }
                }
                Text("Welche Freigaben jemand sehen darf, stellst du bei der Freigabe ein.", color = p.muted, fontSize = 12.sp)
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    if (adding) {
        var name by remember { mutableStateOf("") }
        var desc by remember { mutableStateOf("") }
        var pw by remember { mutableStateOf("") }
        var pw2 by remember { mutableStateOf("") }
        FormDialog(
            title = "Benutzer anlegen", confirm = "Anlegen", onDismiss = { adding = false },
            onConfirm = {
                require(pw == pw2) { "Die Passwörter stimmen nicht überein." }
                api.addUser(name, desc, pw)
            },
            onDone = { adding = false; reload++ },
        ) {
            OutlinedTextField(name, { name = it.lowercase() }, label = { Text("Name (Kleinbuchstaben)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(desc, { desc = it }, label = { Text("Beschreibung") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            PasswordFields(pw, { pw = it }, pw2, { pw2 = it })
        }
    }
    password?.let { u ->
        var pw by remember(u) { mutableStateOf("") }
        var pw2 by remember(u) { mutableStateOf("") }
        FormDialog(
            title = "Passwort für ${u.name}", confirm = "Ändern", onDismiss = { password = null },
            onConfirm = {
                require(pw == pw2) { "Die Passwörter stimmen nicht überein." }
                api.setPassword(u.name, pw)
            },
            onDone = { password = null },
        ) {
            if (u.name == "root") Text("Das ist die Anmeldung an Unraid selbst (Weboberfläche und SSH).", color = LocalPalette.current.unknown)
            PasswordFields(pw, { pw = it }, pw2, { pw2 = it })
        }
    }
    deleting?.let { u ->
        FormDialog(
            title = "${u.name} löschen?", confirm = "Löschen", onDismiss = { deleting = null },
            onConfirm = { api.deleteUser(u.name) }, onDone = { deleting = null; reload++ },
        ) { Text("${u.name} verliert jeden Zugriff auf die Freigaben.") }
    }
}

@Composable
private fun PasswordFields(pw: String, onPw: (String) -> Unit, pw2: String, onPw2: (String) -> Unit) {
    OutlinedTextField(pw, onPw, label = { Text("Passwort") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
    OutlinedTextField(pw2, onPw2, label = { Text("Passwort wiederholen") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
}

private fun stateText(state: String): String = when (state) {
    "running" -> "Läuft"
    "exited" -> "Gestoppt"
    "paused" -> "Angehalten"
    "shutoff" -> "Aus"
    "crashed" -> "Abgestürzt"
    else -> state
}

@Composable
fun UnraidDockerScreen(api: UnraidApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var action by remember { mutableStateOf<Triple<String, String, suspend () -> Unit>?>(null) }
    val (containers, error) = rememberLoaded(reload, UNRAID_REFRESH_MS) { api.containers() }
    val (vms, vmError) = rememberLoaded(reload, UNRAID_REFRESH_MS) { api.vms() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Docker und VMs", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (containers == null && error == null) Text("Wird geladen …", color = p.muted)
            containers?.let { list ->
                SectionTitle("Container")
                HubCard {
                    list.forEach { c ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(c.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                Text(stateText(c.state), color = if (c.state == "running") p.allowed else p.muted, fontSize = 12.sp)
                            }
                            when {
                                c.protected -> Text("Home-Net", color = p.muted, fontSize = 12.sp)
                                c.state == "running" -> {
                                    TextAction("Neu starten") { action = Triple("${c.name} neu starten?", "Neu starten") { api.container(c.id, "restart") } }
                                    TextAction("Stoppen") { action = Triple("${c.name} stoppen?", "Stoppen") { api.container(c.id, "stop") } }
                                }
                                else -> TextAction("Starten") { action = Triple("${c.name} starten?", "Starten") { api.container(c.id, "start") } }
                            }
                        }
                    }
                }
            }
            SectionTitle("Virtuelle Maschinen")
            vmError?.let { if (it != error) Text(it, color = p.blocked, fontSize = 14.sp) }
            vms?.let { list ->
                HubCard {
                    if (list.isEmpty()) Text("Keine VMs vorhanden", color = p.muted, fontSize = 14.sp)
                    list.forEach { v ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(v.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                Text(stateText(v.state), color = if (v.state == "running") p.allowed else p.muted, fontSize = 12.sp)
                            }
                            when (v.state) {
                                "running" -> TextAction("Herunterfahren") { action = Triple("${v.name} herunterfahren?", "Herunterfahren") { api.vm(v.id, "stop") } }
                                "paused" -> TextAction("Fortsetzen") { action = Triple("${v.name} fortsetzen?", "Fortsetzen") { api.vm(v.id, "resume") } }
                                else -> TextAction("Starten") { action = Triple("${v.name} starten?", "Starten") { api.vm(v.id, "start") } }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }

    action?.let { (question, label, run) ->
        FormDialog(
            title = question, confirm = label, onDismiss = { action = null },
            onConfirm = run, onDone = { action = null; reload++ },
        ) { Text("Das kann einen Moment dauern.") }
    }
}

private val FREQUENCY_LABELS = listOf(
    "disabled" to "Kein Zeitplan",
    "hourly" to "Stündlich",
    "daily" to "Täglich",
    "weekly" to "Wöchentlich",
    "monthly" to "Monatlich",
    "start" to "Beim Start des Arrays",
    "stop" to "Beim Stoppen des Arrays",
    "boot" to "Nur beim ersten Array-Start",
    "custom" to "Eigener Zeitplan (Cron)",
)

private fun frequencyText(s: UnraidScript): String =
    if (s.frequency == "custom") "Eigener Zeitplan: ${s.custom}"
    else FREQUENCY_LABELS.firstOrNull { it.first == s.frequency }?.second ?: s.frequency

@Composable
fun UnraidScriptsScreen(api: UnraidApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var action by remember { mutableStateOf<Triple<String, String, suspend () -> Unit>?>(null) }
    var scheduling by remember { mutableStateOf<UnraidScript?>(null) }
    var showing by remember { mutableStateOf<UnraidScript?>(null) }
    val (data, error) = rememberLoaded(reload, 10_000L) { api.scripts() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("User Scripts", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val d = data
            if (d == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else if (!d.installed) {
                Text("Das Plugin „User Scripts“ ist in Unraid nicht installiert.", color = p.muted)
            } else {
                if (!d.starterRunning) {
                    HubCard {
                        Text("Einmal einrichten: Home-Net Starter", color = p.text, fontWeight = FontWeight.SemiBold)
                        Text(
                            "Ansehen geht schon. Zum Starten, Abbrechen und für Zeitpläne in Unraid unter Settings › User Scripts " +
                                "beim „Home-Net Starter“ den Zeitplan auf „At Startup of Array“ stellen und ihn einmal mit " +
                                "„Run in background“ starten.",
                            color = p.muted, fontSize = 13.sp,
                        )
                    }
                }
                if (d.scripts.isEmpty()) Text("Noch keine Skripte angelegt.", color = p.muted)
                d.scripts.forEach { s ->
                    HubCard {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(s.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 15.sp, modifier = Modifier.weight(1f))
                            Text(if (s.running) "Läuft" else "", color = p.accentText, fontSize = 13.sp)
                        }
                        if (s.description.isNotBlank()) Text(s.description, color = p.muted, fontSize = 13.sp)
                        val notes = listOfNotNull(
                            frequencyText(s),
                            if (s.last > 0) "zuletzt ${formatEpoch(s.last)}" else null,
                            if (s.foregroundOnly) "nur in Unraid startbar" else null,
                            if (s.arrayStarted) "nur mit gestartetem Array" else null,
                        )
                        Text(notes.joinToString(" · "), color = p.muted, fontSize = 12.sp)
                        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            TextAction("Protokoll") { showing = s }
                            if (d.starterRunning) {
                                TextAction("Zeitplan") { scheduling = s }
                                if (s.running) {
                                    TextAction("Abbrechen") { action = Triple("${s.name} abbrechen?", "Abbrechen") { api.abortScript(s.id) } }
                                } else if (!s.foregroundOnly) {
                                    TextAction("Starten") { action = Triple("${s.name} starten?", "Starten") { api.runScript(s.id) } }
                                }
                            }
                        }
                    }
                }
                Text("Skripte anlegen und bearbeiten geht in Unraid.", color = p.muted, fontSize = 12.sp)
            }
            Spacer(Modifier.height(16.dp))
        }
    }

    action?.let { (question, label, run) ->
        FormDialog(
            title = question, confirm = label, onDismiss = { action = null },
            onConfirm = run, onDone = { action = null; reload++ },
        ) { Text(if (label == "Starten") "Das Skript läuft dann im Hintergrund auf dem Server." else "Das Skript wird sofort beendet.") }
    }
    scheduling?.let { s ->
        var frequency by remember(s) { mutableStateOf(s.frequency) }
        var custom by remember(s) { mutableStateOf(s.custom) }
        FormDialog(
            title = "Zeitplan: ${s.name}", confirm = "Speichern", onDismiss = { scheduling = null },
            onConfirm = { api.scheduleScript(s.id, frequency, custom) }, onDone = { scheduling = null; reload++ },
        ) {
            Choice(FREQUENCY_LABELS, frequency) { frequency = it }
            if (frequency == "custom") {
                OutlinedTextField(custom, { custom = it }, label = { Text("Cron-Angabe, z. B. 30 3 * * *") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("Minute Stunde Tag Monat Wochentag", color = LocalPalette.current.muted, fontSize = 12.sp)
            }
        }
    }
    showing?.let { s -> ScriptLogDialog(api, s) { showing = null; reload++ } }
}

@Composable
private fun ScriptLogDialog(api: UnraidApi, script: UnraidScript, onClose: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableStateOf(0) }
    var deleteError by remember { mutableStateOf<String?>(null) }
    val (log, error) = rememberLoaded(reload, 5_000L) { api.scriptLog(script.id) }
    val scroll = rememberScrollState()
    LaunchedEffect(log?.first) { scroll.scrollTo(scroll.maxValue) }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Protokoll: ${script.name}") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(scroll)) {
                (deleteError ?: error)?.let { Text(it, color = p.blocked, fontSize = 13.sp) }
                when {
                    log == null -> Text("Wird geladen …", color = p.muted)
                    log.first.isBlank() -> Text("Noch kein Protokoll vorhanden.", color = p.muted)
                    else -> {
                        if (log.second) Text("… nur der letzte Teil", color = p.muted, fontSize = 12.sp)
                        Text(log.first.trimEnd(), fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = p.text)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Schließen") } },
        dismissButton = {
            if (!script.running && !log?.first.isNullOrBlank()) {
                TextButton(onClick = {
                    scope.launch {
                        try {
                            api.deleteScriptLog(script.id)
                            deleteError = null
                            reload++
                        } catch (e: ApiException) {
                            deleteError = e.message
                        }
                    }
                }) { Text("Löschen") }
            }
        },
    )
}
