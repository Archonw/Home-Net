package de.archonw.netmon

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

private const val NC_REFRESH_MS = 60_000L
private const val QUOTA_HINT = "z. B. 5 GB, leer für den Standard, „unbegrenzt“ für keine Grenze"

fun nextcloudTileText(o: NcOverview): String = when {
    !o.configured -> "Anmeldung fehlt"
    o.error.isNotBlank() -> o.error
    o.maintenance -> "Wartungsmodus"
    else -> {
        val updates = (o.info?.appUpdates?.size ?: 0) + if (o.info?.serverUpdate.isNullOrBlank()) 0 else 1
        listOfNotNull(
            "Nextcloud ${o.version}",
            o.userCount?.let { t("{0} Benutzer", it) },
            if (updates > 0) t("{0} Updates", updates) else null,
            if (o.info?.storageLow == true) t("Speicher knapp") else null,
        ).joinToString(" · ")
    }
}

fun nextcloudTileAlert(o: NcOverview): Boolean =
    !o.configured || o.error.isNotBlank() || o.maintenance || o.info?.storageLow == true

private fun NcUser.subtitle(): String = listOfNotNull(
    if (id != name) id else null,
    if (!enabled) t("gesperrt") else null,
    if (quota != null) t("{0} von {1} belegt", formatBytes(used), t(quotaText)) else formatBytes(used),
).joinToString(" · ")

private fun NcUser.quotaInput(): String = if (quotaText == "Standard") "" else quotaText

@Composable
fun NextcloudScreen(api: NextcloudApi, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var adding by remember { mutableStateOf(false) }
    var addingGroup by remember { mutableStateOf(false) }
    val (data, error) = rememberLoaded(reload, NC_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Nextcloud", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            when {
                data == null -> if (error == null) Text("Wird geladen …", color = p.muted)
                !data.configured -> HubCard {
                    Text("Es fehlt noch die Anmeldung für Nextcloud.", color = p.text, fontSize = 15.sp)
                    Text("In Home-Net im Browser beim Modul Nextcloud unter Einstellungen eintragen.", color = p.muted, fontSize = 13.sp)
                }
                data.error.isNotBlank() -> Text(data.error, color = p.blocked, fontSize = 14.sp)
                else -> {
                    HubCard {
                        InfoRow("Version", data.version.ifBlank { "–" })
                        InfoRow("Zustand", if (data.maintenance) t("Wartungsmodus") else t("Läuft"))
                        data.userCount?.let { InfoRow("Benutzer", it.toString()) }
                        data.info?.let { i ->
                            i.freeBytes?.let { InfoRow("Frei auf dem Datenträger", formatBytes(it)) }
                            if (i.storageLow) Text("Der Speicher wird knapp.", color = p.blocked, fontSize = 13.sp)
                            InfoRow("Dateien", i.files.toString())
                            InfoRow("Aktiv (24 Stunden)", i.active24h.toString())
                        }
                    }
                    val info = data.info
                    if (info == null) {
                        Text("Für Speicher und Updates braucht Nextcloud die App „Monitoring“ (serverinfo).", color = p.muted, fontSize = 12.sp)
                    } else {
                        SectionTitle("Updates")
                        HubCard {
                            if (info.serverUpdate.isNotBlank()) Text(t("Nextcloud {0} ist verfügbar", info.serverUpdate), color = p.unknown, fontSize = 14.sp)
                            if (info.appUpdates.isNotEmpty()) {
                                Text(t("{0} App-Updates", info.appUpdates.size), color = p.unknown, fontSize = 14.sp)
                                Text(info.appUpdates.joinToString(", ") { "${it.id} ${it.version}" }, color = p.muted, fontSize = 12.sp)
                                Text("Installieren in Nextcloud unter Apps → Updates.", color = p.muted, fontSize = 12.sp)
                            }
                            if (info.serverUpdate.isBlank() && info.appUpdates.isEmpty()) Text("Alles aktuell.", color = p.text, fontSize = 14.sp)
                        }
                    }
                    if (data.canManage) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) { SectionTitle("Benutzer") }
                            TextAction("Neuer Benutzer") { adding = true }
                            TextAction("Neue Gruppe") { addingGroup = true }
                        }
                        data.users.forEach { u ->
                            HubCard(onClick = { onOpen(u.id) }) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(u.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                                    if (u.admin) Text("Admin", color = p.muted, fontSize = 12.sp)
                                }
                                Text(u.subtitle(), color = if (u.enabled) p.muted else p.blocked, fontSize = 13.sp)
                                if (u.groups.isNotEmpty()) Text(u.groups.joinToString(", "), color = p.muted, fontSize = 12.sp)
                            }
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                }
            }
        }
    }

    if (adding && data != null) {
        var id by remember { mutableStateOf("") }
        var name by remember { mutableStateOf("") }
        var touched by remember { mutableStateOf(false) }
        var email by remember { mutableStateOf("") }
        var password by remember { mutableStateOf("") }
        var quota by remember { mutableStateOf("") }
        val chosen = remember { mutableStateListOf<String>() }
        FormDialog(
            title = "Neuer Benutzer", confirm = "Anlegen", onDismiss = { adding = false },
            onConfirm = { api.create(id, name, email, password, quota, chosen.toList()) },
            onDone = { adding = false; reload++ },
        ) {
            OutlinedTextField(name, {
                name = it
                if (!touched) id = it.lowercase().filter { c -> c.isLetterOrDigit() || c in "_.-@" }
            }, label = { Text("Anzeigename") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(id, { id = it; touched = true }, label = { Text("Benutzername") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                email, { email = it }, label = { Text("E-Mail") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                password, { password = it }, label = { Text("Passwort (freiwillig)") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
            )
            Text("Ohne Passwort schickt Nextcloud an die E-Mail-Adresse einen Link, mit dem die Person ihr Passwort festlegt.", color = LocalPalette.current.muted, fontSize = 12.sp)
            OutlinedTextField(quota, { quota = it }, label = { Text("Speicherplatz") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(QUOTA_HINT, color = LocalPalette.current.muted, fontSize = 12.sp)
            if (data.groups.isNotEmpty()) SectionTitle("Gruppen")
            data.groups.forEach { g ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = g in chosen, onCheckedChange = { if (it) chosen.add(g) else chosen.remove(g) })
                    Text(g, color = LocalPalette.current.text)
                }
            }
        }
    }
    if (addingGroup) {
        var group by remember { mutableStateOf("") }
        FormDialog(
            title = "Neue Gruppe", confirm = "Anlegen", onDismiss = { addingGroup = false },
            onConfirm = { api.createGroup(group) }, onDone = { addingGroup = false; reload++ },
        ) {
            OutlinedTextField(group, { group = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        }
    }
}

@Composable
fun NextcloudUserScreen(api: NextcloudApi, id: String, onBack: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableStateOf(0) }
    var editing by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf<Triple<String, String, suspend () -> Unit>?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    val pending = remember { mutableStateMapOf<String, Boolean>() }
    val (data, error) = rememberLoaded(reload, NC_REFRESH_MS) { api.overview() }
    val user = data?.users?.firstOrNull { it.id == id }
    val own = user != null && user.id.equals(data?.account, ignoreCase = true)

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(user?.name ?: "Benutzer", onBack = onBack, trailing = {
            if (user != null) TextAction("Bearbeiten") { editing = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            (error ?: data?.error?.takeIf { it.isNotBlank() })?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (data != null && user == null && error == null) Text("Diesen Benutzer gibt es nicht mehr.", color = p.muted)
            if (user != null && data != null) {
                HubCard {
                    InfoRow("Benutzername", user.id)
                    if (user.email.isNotBlank()) InfoRow("E-Mail", user.email)
                    InfoRow("Anmeldung", when (user.backend) {
                        "Database" -> t("Konto in Nextcloud")
                        "user_oidc" -> t("Über Pocket ID / OIDC")
                        else -> user.backend
                    })
                    InfoRow("Zuletzt angemeldet", if (user.lastLogin > 0) formatEpoch(user.lastLogin) else t("noch nie"))
                    InfoRow("Belegt", formatBytes(user.used))
                    InfoRow("Speicherplatz", t(user.quotaText))
                    user.quota?.takeIf { it > 0 }?.let { q ->
                        LinearProgressIndicator(
                            progress = { (user.used.toFloat() / q).coerceIn(0f, 1f) },
                            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                        )
                    }
                    if (!user.enabled) Text("Gesperrt: kann sich nicht anmelden.", color = p.blocked, fontSize = 14.sp)
                    if (own) Text("Mit diesem Konto meldet sich Home-Net an.", color = p.muted, fontSize = 12.sp)
                }
                SectionTitle("Gruppen")
                HubCard {
                    if (data.groups.isEmpty()) Text("Keine Gruppen.", color = p.muted, fontSize = 13.sp)
                    data.groups.forEach { g ->
                        val on = pending[g] ?: (g in user.groups)
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(g, color = p.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
                            Switch(checked = on, enabled = g !in pending && !(own && g == "admin"), onCheckedChange = { wanted ->
                                pending[g] = wanted
                                message = null
                                scope.launch {
                                    try {
                                        api.setGroup(user.id, g, wanted)
                                        reload++
                                    } catch (e: ApiException) {
                                        message = e.message
                                    } finally {
                                        pending.remove(g)
                                    }
                                }
                            })
                        }
                    }
                }
                SectionTitle("Konto")
                HubCard {
                    if (user.email.isNotBlank()) Row {
                        TextAction("Willkommens-Mail") {
                            confirm = Triple(t("Willkommens-Mail an {0} schicken?", user.email), "Schicken") { api.welcome(user.id) }
                        }
                    }
                    if (!own) {
                        Row {
                            TextAction(if (user.enabled) "Sperren" else "Entsperren") {
                                confirm = if (user.enabled) {
                                    Triple(t("{0} sperren? Die Anmeldung geht dann nicht mehr, die Dateien bleiben.", user.name), "Sperren") { api.setEnabled(user.id, false) }
                                } else {
                                    Triple(t("{0} entsperren?", user.name), "Entsperren") { api.setEnabled(user.id, true) }
                                }
                            }
                            TextAction("Löschen") { deleting = true }
                        }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    if (editing && user != null) {
        var name by remember { mutableStateOf(user.name) }
        var email by remember { mutableStateOf(user.email) }
        var quota by remember { mutableStateOf(user.quotaInput()) }
        var password by remember { mutableStateOf("") }
        FormDialog(
            title = t("{0} bearbeiten", user.name), confirm = "Speichern", onDismiss = { editing = false },
            onConfirm = {
                api.update(
                    user.id,
                    name = name.takeIf { it != user.name },
                    email = email.takeIf { it != user.email },
                    quota = quota.takeIf { it != user.quotaInput() },
                    password = password,
                )
            },
            onDone = { editing = false; reload++ },
        ) {
            OutlinedTextField(name, { name = it }, label = { Text("Anzeigename") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                email, { email = it }, label = { Text("E-Mail") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(quota, { quota = it }, label = { Text("Speicherplatz") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Text(QUOTA_HINT, color = LocalPalette.current.muted, fontSize = 12.sp)
            if (user.canPassword) {
                OutlinedTextField(
                    password, { password = it }, label = { Text("Neues Passwort") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                )
                Text("Leer lassen zum Behalten.", color = LocalPalette.current.muted, fontSize = 12.sp)
            }
            if (user.backend == "user_oidc") {
                Text("Name und E-Mail kommen bei der nächsten Anmeldung wieder von Pocket ID.", color = LocalPalette.current.muted, fontSize = 12.sp)
            }
        }
    }
    if (deleting && user != null) {
        var typed by remember { mutableStateOf("") }
        FormDialog(
            title = t("{0} löschen?", user.name), confirm = "Löschen", onDismiss = { deleting = false },
            onConfirm = { api.delete(user.id, typed) },
            onDone = { deleting = false; onBack() },
        ) {
            Text("Das Konto wird mit allen Dateien gelöscht. Das lässt sich nicht rückgängig machen.", color = LocalPalette.current.text, fontSize = 14.sp)
            OutlinedTextField(typed, { typed = it }, label = { Text(t("Zum Bestätigen „{0}“ eintippen", user.id)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        }
    }
    confirm?.let { (question, label, action) ->
        FormDialog(
            title = question, confirm = label, onDismiss = { confirm = null },
            onConfirm = action, onDone = { confirm = null; reload++ },
        ) {}
    }
}
