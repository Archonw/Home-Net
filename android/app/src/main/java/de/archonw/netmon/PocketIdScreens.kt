package de.archonw.netmon

import android.content.Intent
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

private const val PID_REFRESH_MS = 60_000L

fun pocketIdTileText(o: PidOverview): String = when {
    !o.configured -> "API-Schlüssel fehlt"
    o.error.isNotBlank() -> o.error
    else -> {
        val waiting = o.users.count { !it.disabled && it.passkeys == 0 }
        "${o.users.size} Benutzer" + if (waiting > 0) ", $waiting noch ohne Passkey" else ""
    }
}

private fun PidUser.subtitle(apps: List<PidApp>): String {
    val names = apps.filter { it.group in groups }.map { it.label }
    return listOfNotNull(
        username,
        if (disabled) "gesperrt" else if (passkeys == 0) "noch kein Passkey" else null,
        if (names.isEmpty()) "keine Apps" else names.joinToString(", "),
    ).joinToString(" · ")
}

@Composable
fun PocketIdScreen(api: PocketIdApi, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var adding by remember { mutableStateOf(false) }
    var invite by remember { mutableStateOf<Pair<String, PidInvite>?>(null) }
    val (data, error) = rememberLoaded(reload, PID_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Pocket ID", onBack = onBack, trailing = {
            if (data?.configured == true && data.error.isBlank()) TextAction("Neu") { adding = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            when {
                data == null -> if (error == null) Text("Wird geladen …", color = p.muted)
                !data.configured -> HubCard {
                    Text("Es fehlt noch der API-Schlüssel von Pocket ID.", color = p.text, fontSize = 15.sp)
                    Text("In Home-Net im Browser beim Modul Pocket ID unter Einstellungen eintragen.", color = p.muted, fontSize = 13.sp)
                }
                data.error.isNotBlank() -> Text(data.error, color = p.blocked, fontSize = 14.sp)
                else -> {
                    data.users.forEach { u ->
                        HubCard(onClick = { onOpen(u.id) }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(u.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                                if (u.admin) Text("Admin", color = p.muted, fontSize = 12.sp)
                            }
                            Text(u.subtitle(data.apps), color = if (u.disabled) p.blocked else p.muted, fontSize = 13.sp)
                        }
                    }
                    if (data.openApps.isNotEmpty()) {
                        Text("Für alle offen: ${data.openApps.joinToString(", ")}", color = p.muted, fontSize = 12.sp)
                    }
                    Spacer(Modifier.height(16.dp))
                }
            }
        }
    }

    if (adding && data != null) {
        var first by remember { mutableStateOf("") }
        var last by remember { mutableStateOf("") }
        var username by remember { mutableStateOf("") }
        var touched by remember { mutableStateOf(false) }
        var email by remember { mutableStateOf("") }
        val chosen = remember { mutableStateListOf<String>() }
        var created by remember { mutableStateOf<PidInvite?>(null) }
        FormDialog(
            title = "Neuer Benutzer", confirm = "Anlegen", onDismiss = { adding = false },
            onConfirm = { created = api.create(first, last, username, email, chosen.toList()) },
            onDone = {
                adding = false
                reload++
                created?.let { invite = first.ifBlank { username } to it }
            },
        ) {
            OutlinedTextField(first, {
                first = it
                if (!touched) username = it.lowercase().filter { c -> c.isLetterOrDigit() || c in "_.-@" }
            }, label = { Text("Vorname") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(last, { last = it }, label = { Text("Nachname") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(username, { username = it.lowercase(); touched = true }, label = { Text("Benutzername") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                email, { email = it }, label = { Text("E-Mail (freiwillig)") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
            )
            if (data.apps.isNotEmpty()) SectionTitle("Apps")
            data.apps.forEach { a ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = a.group in chosen, onCheckedChange = { if (it) chosen.add(a.group) else chosen.remove(a.group) })
                    Text(a.label, color = LocalPalette.current.text)
                }
            }
            Text("Danach gibt es einen Einladungslink. Damit legt die Person ihren Passkey an.", color = LocalPalette.current.muted, fontSize = 12.sp)
        }
    }
    invite?.let { (name, inv) -> InviteDialog(name, inv) { invite = null } }
}

@Composable
fun PocketIdUserScreen(api: PocketIdApi, id: String, onBack: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    var reload by remember { mutableStateOf(0) }
    var editing by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf<Triple<String, String, suspend () -> Unit>?>(null) }
    var invite by remember { mutableStateOf<PidInvite?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    val pending = remember { mutableStateMapOf<String, Boolean>() }
    val (data, error) = rememberLoaded(reload, PID_REFRESH_MS) { api.overview() }
    val user = data?.users?.firstOrNull { it.id == id }

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
                    InfoRow("Benutzername", user.username)
                    if (user.email.isNotBlank()) InfoRow("E-Mail", user.email)
                    InfoRow("Passkeys", user.passkeys?.toString() ?: "–")
                    if (user.admin) InfoRow("Rolle", "Admin in Pocket ID")
                    if (user.disabled) Text("Gesperrt: kann sich nirgends anmelden.", color = p.blocked, fontSize = 14.sp)
                }
                SectionTitle("Apps")
                HubCard {
                    if (data.apps.isEmpty()) Text("In Pocket ID sind noch keine Apps auf Gruppen beschränkt.", color = p.muted, fontSize = 13.sp)
                    data.apps.forEach { a ->
                        val on = pending[a.group] ?: (a.group in user.groups)
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(a.label, color = p.text, fontSize = 15.sp)
                                Text("Gruppe ${a.groupName}", color = p.muted, fontSize = 12.sp)
                            }
                            Switch(checked = on, enabled = a.group !in pending, onCheckedChange = { wanted ->
                                pending[a.group] = wanted
                                message = null
                                scope.launch {
                                    try {
                                        api.setApp(user.id, a.group, wanted)
                                        reload++
                                    } catch (e: ApiException) {
                                        message = e.message
                                    } finally {
                                        pending.remove(a.group)
                                    }
                                }
                            })
                        }
                    }
                }
                if (data.openApps.isNotEmpty()) Text("Ohne Einschränkung nutzbar: ${data.openApps.joinToString(", ")}", color = p.muted, fontSize = 12.sp)
                SectionTitle("Anmeldung")
                HubCard {
                    if (user.passkeys == 0) Text("Hat noch keinen Passkey. Schick einen Einladungslink.", color = p.unknown, fontSize = 13.sp)
                    Row {
                        TextAction("Einladungslink") {
                            message = null
                            scope.launch {
                                try { invite = api.invite(user.id) } catch (e: ApiException) { message = e.message }
                            }
                        }
                        if (user.email.isNotBlank()) TextAction("Per E-Mail") {
                            confirm = Triple("Einladung an ${user.email} schicken?", "Schicken") { api.invite(user.id, byEmail = true) }
                        }
                    }
                    if (!user.admin) {
                        Row {
                            TextAction(if (user.disabled) "Entsperren" else "Sperren") {
                                confirm = if (user.disabled) {
                                    Triple("${user.name} entsperren?", "Entsperren") { api.setDisabled(user.id, false) }
                                } else {
                                    Triple("${user.name} sperren? Die Anmeldung an allen Apps geht dann nicht mehr.", "Sperren") { api.setDisabled(user.id, true) }
                                }
                            }
                            TextAction("Löschen") {
                                confirm = Triple("${user.name} aus Pocket ID löschen? Die Konten in den Apps bleiben bestehen.", "Löschen") {
                                    api.delete(user.id)
                                    onBack()
                                }
                            }
                        }
                    } else {
                        Text("Admins von Pocket ID lassen sich nur in Pocket ID sperren oder löschen.", color = p.muted, fontSize = 12.sp)
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    if (editing && user != null) {
        var first by remember { mutableStateOf(user.firstName) }
        var last by remember { mutableStateOf(user.lastName) }
        var email by remember { mutableStateOf(user.email) }
        FormDialog(
            title = "${user.name} bearbeiten", confirm = "Speichern", onDismiss = { editing = false },
            onConfirm = { api.update(user.id, first, last, email) }, onDone = { editing = false; reload++ },
        ) {
            OutlinedTextField(first, { first = it }, label = { Text("Vorname") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(last, { last = it }, label = { Text("Nachname") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                email, { email = it }, label = { Text("E-Mail") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth(),
            )
        }
    }
    confirm?.let { (question, label, action) ->
        FormDialog(
            title = question, confirm = label, onDismiss = { confirm = null },
            onConfirm = action, onDone = { confirm = null; reload++ },
        ) {}
    }
    invite?.let { inv -> InviteDialog(user?.firstName?.ifBlank { null } ?: user?.name ?: "", inv) { invite = null } }
}

@Composable
private fun InviteDialog(name: String, invite: PidInvite, onClose: () -> Unit) {
    val p = LocalPalette.current
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    val text = "Hallo $name, mit diesem Link richtest du deinen Zugang ein (einmalig, gültig bis ${formatEpoch(invite.expires)}): ${invite.link}"
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Einladung für $name") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Mit dem Link legt $name einmalig einen Passkey an. Er gilt bis ${formatEpoch(invite.expires)}.", color = p.text, fontSize = 14.sp)
                Text(invite.link, color = p.muted, fontSize = 13.sp)
                Row {
                    TextAction("Teilen") {
                        val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
                        context.startActivity(Intent.createChooser(send, "Einladung teilen"))
                    }
                    TextAction(if (copied) "Kopiert" else "Kopieren") {
                        clipboard.setText(AnnotatedString(invite.link))
                        copied = true
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Fertig") } },
    )
}
