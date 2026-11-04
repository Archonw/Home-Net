package de.archonw.netmon

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private const val OVERVIEW_REFRESH_MS = 30_000L
private const val RUNNING_REFRESH_MS = 5_000L

fun formatBytes(bytes: Long): String {
    var value = bytes.toDouble()
    val units = listOf("B", "KB", "MB", "GB", "TB")
    var unit = 0
    while (value >= 1024 && unit < units.lastIndex) {
        value /= 1024
        unit++
    }
    val text = if (unit <= 1) "%.0f".format(value) else "%.1f".format(value)
    return "$text ${units[unit]}".replace('.', ',')
}

fun formatEpoch(seconds: Long): String =
    if (seconds <= 0) "–" else Instant.ofEpochSecond(seconds).atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM.yy, HH:mm"))

/** "2026-10-31_204800" aus dem Dateinamen des Protokolls. */
fun formatStamp(stamp: String): String {
    val m = Regex("""(\d{4})-(\d\d)-(\d\d)_(\d\d)(\d\d)\d\d""").matchEntire(stamp) ?: return stamp
    val (y, mo, d, h, mi) = m.destructured
    return "$d.$mo.${y.takeLast(2)}, $h:$mi"
}

private data class PiState(val label: String, val color: Color)

val PI_UPDATE_MODES = listOf(
    "all" to "Bei „Alle aktualisieren“ dabei",
    "single" to "Nur einzeln aktualisieren",
    "careful" to "Gesperrt, nur einzeln nach Eintippen des Namens",
)

fun piCount(n: Int): String = if (n == 1) "1 Pi" else "$n Pis"

/** Kurzer Zusatz für die Zeile eines Pi in der Liste. */
fun piUpdateLabel(row: PiRow): String = when {
    row.updateState == "upgrading" -> "Update läuft"
    row.updates == 1 -> "1 Update"
    row.updates > 1 -> "${row.updates} Updates"
    else -> ""
}

fun piSoftwareState(s: PiSoftware): String = when {
    s.state == "upgrading" -> "Update läuft …"
    s.state == "checking" -> "Wird geprüft …"
    s.count < 0 -> "Noch nicht geprüft"
    s.count == 0 -> "Alles aktuell"
    s.count == 1 -> "1 Update verfügbar"
    else -> "${s.count} Updates verfügbar"
}

@Composable
private fun piState(row: PiRow): PiState {
    val p = LocalPalette.current
    return when {
        row.running -> PiState("Sicherung läuft", p.accentText)
        row.status == "ok" -> PiState("Gesichert", p.allowed)
        row.status == "failed" -> PiState("Fehler", p.blocked)
        row.status == "cancelled" -> PiState("Abgebrochen", p.unknown)
        else -> PiState("Nie gesichert", p.muted)
    }
}

@Composable
internal fun HubCard(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val p = LocalPalette.current
    val clickable = if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(p.card).then(clickable).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        content = content,
    )
}

@Composable
internal fun PageTitle(title: String, trailing: @Composable () -> Unit = {}, onBack: (() -> Unit)? = null) {
    val p = LocalPalette.current
    Row(
        Modifier.fillMaxWidth().statusBarsPadding().padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) {
            Icon(
                NavIcons.back, contentDescription = "Zurück", tint = p.text,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onBack).padding(6.dp),
            )
            Spacer(Modifier.size(8.dp))
        }
        Text(title, color = p.text, fontSize = 24.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
        trailing()
    }
}

@Composable
internal fun SectionTitle(text: String) {
    Text(
        text, color = LocalPalette.current.muted, fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 10.dp, bottom = 2.dp),
    )
}

@Composable
internal fun StatTile(label: String, value: String, detail: String, modifier: Modifier = Modifier, color: Color? = null) {
    val p = LocalPalette.current
    Column(modifier.clip(RoundedCornerShape(14.dp)).background(p.card).padding(12.dp)) {
        Text(label, color = p.muted, fontSize = 12.sp)
        Text(value, color = color ?: p.text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(detail, color = p.muted, fontSize = 12.sp, maxLines = 1)
    }
}

@Composable
internal fun InfoRow(label: String, value: String) {
    val p = LocalPalette.current
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, color = p.muted, fontSize = 14.sp, modifier = Modifier.weight(0.42f))
        Text(value, color = p.text, fontSize = 14.sp, modifier = Modifier.weight(0.58f))
    }
}

@Composable
internal fun TextAction(label: String, onClick: () -> Unit) {
    val p = LocalPalette.current
    Text(
        label, color = p.accentText, fontWeight = FontWeight.SemiBold, fontSize = 14.sp,
        modifier = Modifier.clip(RoundedCornerShape(10.dp)).clickable(onClick = onClick).padding(8.dp),
    )
}

/** Startseite: eine Kachel je Modul. Mit gemeinsamem Zugang kommt die Liste vom Server. */
@Composable
fun HomeScreen(
    settings: Settings, api: Api, onNetwork: () -> Unit, onPi: () -> Unit, onPc: () -> Unit, onUnraid: () -> Unit,
    onPocketId: () -> Unit, onWireGuard: () -> Unit, onPihole: () -> Unit, onProxy: () -> Unit,
) {
    val p = LocalPalette.current
    var network by remember { mutableStateOf("Wird geladen …") }
    var networkAlert by remember { mutableStateOf(false) }
    var pi by remember {
        mutableStateOf(if (settings.isPiConfigured) "Wird geladen …" else "Noch nicht eingerichtet")
    }
    var piAlert by remember { mutableStateOf(false) }
    var pc by remember { mutableStateOf("Wird geladen …") }
    var pcAlert by remember { mutableStateOf(false) }
    var unraid by remember { mutableStateOf("Wird geladen …") }
    var unraidAlert by remember { mutableStateOf(false) }
    var pocketId by remember { mutableStateOf("Wird geladen …") }
    var pocketIdAlert by remember { mutableStateOf(false) }
    var proxy by remember { mutableStateOf("Wird geladen …") }
    var proxyAlert by remember { mutableStateOf(false) }
    var wireGuard by remember { mutableStateOf("Wird geladen …") }
    var wireGuardAlert by remember { mutableStateOf(false) }
    var pihole by remember { mutableStateOf("Wird geladen …") }
    var piholeAlert by remember { mutableStateOf(false) }
    var installed by remember { mutableStateOf<Set<String>?>(null) }
    var hubError by remember { mutableStateOf<String?>(null) }
    var order by remember { mutableStateOf(settings.homeTiles) }
    var arranging by remember { mutableStateOf(false) }

    AppUpdatePrompt(api, settings)

    LaunchedEffect(Unit) {
        if (settings.isHubConfigured) {
            try {
                installed = hubModules(settings.hubUrl, settings.hubExternalUrl, settings.hubSecret).map { it.id }.toSet()
            } catch (e: ApiException) {
                hubError = e.message
            }
        }
        if (installed?.contains("netzwerkmonitor") != false) {
            launch {
                try {
                    val devices = api.devices()
                    val unknown = devices.count { it.status == "unknown" }
                    network = "${devices.count { it.online }} von ${devices.size} online" +
                        if (unknown > 0) ", $unknown unbekannt" else ""
                    networkAlert = unknown > 0
                } catch (e: ApiException) {
                    network = e.message ?: "Nicht erreichbar"
                    networkAlert = true
                }
            }
        }
        if (settings.isPiConfigured && installed?.contains("pi-wartung") != false) {
            launch {
                try {
                    val o = settings.piApi().overview()
                    pi = "${o.pis.size} Pis, ${o.images} Images" + when {
                        o.running > 0 -> ", ${o.running} läuft"
                        o.problems > 0 -> ", ${o.problems} Probleme"
                        else -> ""
                    }
                    piAlert = o.problems > 0
                } catch (e: ApiException) {
                    pi = e.message ?: "Nicht erreichbar"
                    piAlert = true
                }
            }
        }
        if (installed?.contains("pc-sicherung") == true) {
            launch {
                try {
                    val o = settings.pcApi().overview()
                    pc = pcTileText(o)
                    pcAlert = o.problems > 0
                } catch (e: ApiException) {
                    pc = e.message ?: "Nicht erreichbar"
                    pcAlert = true
                }
            }
        }
        if (installed?.contains("unraid") == true) {
            launch {
                try {
                    val o = settings.unraidApi().overview()
                    unraid = unraidTileText(o)
                    unraidAlert = o.problems > 0 || !o.available
                } catch (e: ApiException) {
                    unraid = e.message ?: "Nicht erreichbar"
                    unraidAlert = true
                }
            }
        }
        if (installed?.contains("pocket-id") == true) {
            launch {
                try {
                    val o = settings.pocketIdApi().overview()
                    pocketId = pocketIdTileText(o)
                    pocketIdAlert = !o.configured || o.error.isNotBlank()
                } catch (e: ApiException) {
                    pocketId = e.message ?: "Nicht erreichbar"
                    pocketIdAlert = true
                }
            }
        }
        if (installed?.contains("proxy-manager") == true) {
            launch {
                try {
                    val o = settings.proxyApi().overview()
                    proxy = proxyTileText(o)
                    proxyAlert = !o.configured || o.error.isNotBlank() || o.certsExpired > 0 || o.certsSoon > 0
                } catch (e: ApiException) {
                    proxy = e.message ?: "Nicht erreichbar"
                    proxyAlert = true
                }
            }
        }
        if (installed?.contains("wireguard") == true) {
            launch {
                try {
                    val o = settings.wireGuardApi().overview()
                    wireGuard = wireGuardTileText(o)
                    wireGuardAlert = !o.configured || o.error.isNotBlank()
                } catch (e: ApiException) {
                    wireGuard = e.message ?: "Nicht erreichbar"
                    wireGuardAlert = true
                }
            }
        }
        if (installed?.contains("pihole") == true) {
            launch {
                try {
                    val o = settings.piholeApi().overview()
                    pihole = piholeTileText(o)
                    piholeAlert = piholeTileAlert(o)
                } catch (e: ApiException) {
                    pihole = e.message ?: "Nicht erreichbar"
                    piholeAlert = true
                }
            }
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Home-Net", trailing = {
            if (!arranging && installed != null) TextAction("Anordnen") { arranging = true }
        })
        // Pc-Sicherung und Unraid nur über den gemeinsamen Zugang, darum erst sichtbar, wenn der Server das Modul meldet
        val visible = order.filter {
            when (it) {
                "network" -> installed?.contains("netzwerkmonitor") != false
                "pi" -> installed?.contains("pi-wartung") != false
                "pc" -> installed?.contains("pc-sicherung") == true
                "unraid" -> installed?.contains("unraid") == true
                "pocketid" -> installed?.contains("pocket-id") == true
                "proxy" -> installed?.contains("proxy-manager") == true
                "wireguard" -> installed?.contains("wireguard") == true
                "pihole" -> installed?.contains("pihole") == true
                else -> false
            }
        }
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            hubError?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (arranging) {
                ArrangeHint()
                OrderList(visible, HOME_TILE_LABELS) { changed ->
                    order = changed + order.filter { it !in changed }
                    settings.homeTiles = order
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextAction("Zurücksetzen") { order = HOME_TILES; settings.homeTiles = order }
                    TextAction("Fertig") { arranging = false }
                }
            } else {
                visible.forEach { tile ->
                    when (tile) {
                        "network" -> ServiceTile("Netzwerk", network, networkAlert, R.drawable.ic_tile_network, onNetwork)
                        "pi" -> ServiceTile("Pi-Wartung", pi, piAlert, R.drawable.ic_tile_pi, onPi)
                        "pc" -> ServiceTile("PC-Sicherung", pc, pcAlert, R.drawable.ic_tile_pc, onPc)
                        "unraid" -> ServiceTile("Unraid", unraid, unraidAlert, R.drawable.ic_tile_unraid, onUnraid)
                        "pocketid" -> ServiceTile("Pocket ID", pocketId, pocketIdAlert, R.drawable.ic_tile_pocketid, onPocketId)
                        "proxy" -> ServiceTile("Proxy Manager", proxy, proxyAlert, R.drawable.ic_tile_proxy, onProxy)
                        "wireguard" -> ServiceTile("WireGuard", wireGuard, wireGuardAlert, R.drawable.ic_tile_wireguard, onWireGuard)
                        "pihole" -> ServiceTile("Pi-hole", pihole, piholeAlert, R.drawable.ic_tile_pihole, onPihole)
                    }
                }
            }
        }
    }
}

val HOME_TILES = listOf("network", "pi", "pc", "unraid", "pocketid", "wireguard", "pihole", "proxy")
private val HOME_TILE_LABELS = mapOf(
    "network" to "Netzwerk", "pi" to "Pi-Wartung", "pc" to "PC-Sicherung", "unraid" to "Unraid", "pocketid" to "Pocket ID",
    "wireguard" to "WireGuard", "pihole" to "Pi-hole", "proxy" to "Proxy Manager",
)

/** Eine Adresse und ein Passwort für alle Module. */
@Composable
fun HubSetupScreen(settings: Settings, onSaved: () -> Unit, onBack: (() -> Unit)?) {
    val p = LocalPalette.current
    var url by remember { mutableStateOf(settings.hubUrl.ifBlank { "http://" }) }
    var externalUrl by remember { mutableStateOf(settings.hubExternalUrl) }
    var password by remember { mutableStateOf(if (settings.isHubConfigured) settings.password else "") }
    var username by remember { mutableStateOf(settings.hubUser) }
    var code by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(if (onBack != null) "Zugang" else "Home-Net einrichten", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "Die Adresse deines Home-Net-Servers, zum Beispiel http://192.168.1.10:8093, dein Konto und ein " +
                    "Kopplungscode. Den Code bekommst du im Browser unter Mein Konto.",
                color = p.muted, fontSize = 14.sp,
            )
            OutlinedTextField(
                value = url, onValueChange = { url = it }, singleLine = true,
                label = { Text("Adresse im Heimnetz") }, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            OutlinedTextField(
                value = externalUrl, onValueChange = { externalUrl = it }, singleLine = true,
                label = { Text("Adresse für unterwegs (optional)") }, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            OutlinedTextField(
                value = username, onValueChange = { username = it }, singleLine = true,
                label = { Text("Benutzername") }, modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = password, onValueChange = { password = it }, singleLine = true,
                label = { Text(if (settings.hubToken.isBlank()) "Passwort" else "Passwort, nur für eine neue Kopplung") },
                modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            )
            OutlinedTextField(
                value = code, onValueChange = { code = it }, singleLine = true,
                label = {
                    Text(if (settings.hubToken.isBlank()) "Kopplungscode (optional)" else "Gerät ist gekoppelt, neuer Code optional")
                },
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            PrimaryButton(if (busy) "Prüfe …" else "Speichern und testen", Modifier.fillMaxWidth(), enabled = !busy) {
                if (externalUrl.isNotBlank() && !externalUrl.trim().startsWith("https://")) {
                    message = "Die Adresse für unterwegs muss mit https:// beginnen."
                    return@PrimaryButton
                }
                busy = true
                message = null
                scope.launch {
                    try {
                        val token = if (code.isNotBlank()) {
                            pairDevice(url, externalUrl, username, password, code, android.os.Build.MODEL ?: "Handy")
                        } else {
                            settings.hubToken
                        }
                        if (token.isBlank()) {
                            throw ApiException("Bitte den Kopplungscode aus Mein Konto eintragen.")
                        }
                        hubModules(url, externalUrl, token)
                        settings.hubUser = username.trim()
                        settings.useHub(url, externalUrl, password, token)
                        onSaved()
                    } catch (e: ApiException) {
                        message = e.message
                    } finally {
                        busy = false
                    }
                }
            }
        }
    }
}

@Composable
private fun ServiceTile(title: String, status: String, alert: Boolean, @DrawableRes icon: Int, onClick: () -> Unit) {
    val p = LocalPalette.current
    HubCard(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Image(
                painterResource(icon), contentDescription = null,
                modifier = Modifier.size(48.dp).clip(RoundedCornerShape(14.dp)),
            )
            Spacer(Modifier.size(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, color = p.text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Text(status, color = if (alert) p.blocked else p.muted, fontSize = 14.sp)
            }
            Icon(NavIcons.chevron, contentDescription = null, tint = p.muted)
        }
    }
}

@Composable
fun PiOverviewScreen(api: PiApi, onOpen: (String) -> Unit, onSetup: (() -> Unit)?, onBack: () -> Unit) {
    val p = LocalPalette.current
    var data by remember { mutableStateOf<PiOverview?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var confirmAll by remember { mutableStateOf<List<PiUpdateTarget>?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(reload) {
        while (true) {
            try {
                data = api.overview()
                error = null
            } catch (e: ApiException) {
                error = e.message
            }
            delay(OVERVIEW_REFRESH_MS)
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Pi-Wartung", onBack = onBack, trailing = {
            TextAction("Prüfen") {
                scope.launch {
                    try {
                        api.check()
                        reload++
                    } catch (e: ApiException) {
                        error = e.message
                    }
                }
            }
            onSetup?.let { TextAction("Zugang", it) }
        })
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
                    val online = o.pis.count { it.online == true }
                    StatTile("Pis", "${o.pis.size}", "$online online", Modifier.weight(1f))
                    StatTile("Images", "${o.images}", formatBytes(o.imagesBytes), Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile("Unraid frei", formatBytes(o.availBytes), "von ${formatBytes(o.totalBytes)}", Modifier.weight(1f))
                    StatTile(
                        "Probleme", "${o.problems}", "${o.attention.size - o.problems} Hinweise", Modifier.weight(1f),
                        color = if (o.problems > 0) p.blocked else p.allowed,
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatTile(
                        "Updates", piCount(o.updatePis), if (o.updatePis > 0) "haben Updates" else "alles aktuell",
                        Modifier.weight(1f), color = if (o.updatePis > 0) p.unknown else p.allowed,
                    )
                    Spacer(Modifier.weight(1f))
                }
                if (o.updateAll.isNotEmpty()) {
                    PrimaryButton("Alle aktualisieren (${o.updateAll.size})", Modifier.fillMaxWidth()) { confirmAll = o.updateAll }
                }
                notice?.let { Text(it, color = p.muted, fontSize = 14.sp) }
                if (o.attention.isNotEmpty()) {
                    SectionTitle("Braucht Aufmerksamkeit")
                    HubCard {
                        o.attention.forEach {
                            Text(
                                (if (it.serious) "Problem · " else "Hinweis · ") + it.pi + ": " + it.text,
                                color = if (it.serious) p.blocked else p.text, fontSize = 14.sp,
                            )
                        }
                    }
                }
                val groups = o.pis.groupBy { it.category.ifBlank { "Ohne Kategorie" } }
                groups.forEach { (category, rows) ->
                    SectionTitle(category)
                    rows.forEach { row -> PiRowCard(row) { onOpen(row.id) } }
                }
                if (o.errors.isNotEmpty()) {
                    SectionTitle("Letzte Fehler")
                    HubCard {
                        o.errors.forEach {
                            Text("${formatStamp(it.stamp)} · ${it.pi}", color = p.muted, fontSize = 12.sp)
                            Text(it.message, color = p.text, fontSize = 14.sp)
                            Spacer(Modifier.height(4.dp))
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
            }
        }
    }

    confirmAll?.let { list ->
        AlertDialog(
            onDismissRequest = { confirmAll = null },
            title = { Text("Alle aktualisieren?") },
            text = {
                Text(
                    list.joinToString("\n") { "${it.name}: ${it.updates} Updates" } +
                        "\n\nPis, die für Updates gesperrt sind oder nur einzeln aktualisiert werden, bleiben unberührt. " +
                        "Ein Neustart erfolgt nicht."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmAll = null
                    scope.launch {
                        try {
                            val (started, failed) = api.upgradeAll(list.map { it.id })
                            notice = (if (started.isEmpty()) "Kein Update gestartet." else "Update läuft: ${started.joinToString()}") +
                                failed.joinToString("") { "\n$it" }
                            reload++
                        } catch (e: ApiException) {
                            error = e.message
                        }
                    }
                }) { Text("Aktualisieren") }
            },
            dismissButton = { TextButton(onClick = { confirmAll = null }) { Text("Abbrechen") } },
        )
    }
}

@Composable
private fun PiRowCard(row: PiRow, onClick: () -> Unit) {
    val p = LocalPalette.current
    val state = piState(row)
    HubCard(onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(row.name, color = p.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    "${state.label} · ${row.images} Images · ${formatBytes(row.bytes)}",
                    color = state.color, fontSize = 13.sp,
                )
                val reach = when (row.online) {
                    true -> "Online" + (if (row.card.isNotBlank()) ", Karte ${row.card}" else "")
                    false -> "Offline"
                    null -> "Wird geprüft"
                }
                Text(
                    "$reach · Backup ${formatEpoch(row.okTime)}" + piUpdateLabel(row).let { if (it.isEmpty()) "" else " · $it" },
                    color = p.muted, fontSize = 12.sp,
                )
            }
            Icon(NavIcons.chevron, contentDescription = null, tint = p.muted)
        }
    }
}

@Composable
fun PiDetailScreen(api: PiApi, id: String, onBack: () -> Unit) {
    val p = LocalPalette.current
    var data by remember { mutableStateOf<PiDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf<String?>(null) }
    var showPackages by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }
    var newMode by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(reload) {
        while (true) {
            try {
                data = api.detail(id)
                error = null
            } catch (e: ApiException) {
                error = e.message
            }
            val active = data?.row?.running == true || data?.software?.state?.isNotEmpty() == true
            delay(if (active) RUNNING_REFRESH_MS else OVERVIEW_REFRESH_MS)
        }
    }

    fun act(action: suspend () -> Unit) {
        busy = true
        scope.launch {
            try {
                action()
                reload++
            } catch (e: ApiException) {
                error = e.message
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(data?.row?.name ?: "Pi", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            val d = data
            if (d == null) {
                if (error == null) Text("Wird geladen …", color = p.muted)
            } else {
                val state = piState(d.row)
                HubCard {
                    Text(state.label, color = state.color, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    Text(d.address, color = p.muted, fontSize = 13.sp)
                    if (d.row.status == "failed" && d.error.isNotBlank()) Text(d.error, color = p.blocked, fontSize = 14.sp)
                    if (d.warnings.isNotBlank()) Text(d.warnings, color = p.unknown, fontSize = 14.sp)
                }
                d.progress?.let { progress ->
                    HubCard {
                        Text(progress.step.ifBlank { "Startet …" }, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        LinearProgressIndicator(
                            progress = { progress.percent / 100f },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                            color = p.accent,
                            trackColor = p.line,
                        )
                        Text("${progress.percent} %  ${progress.detail}", color = p.muted, fontSize = 12.sp)
                    }
                }
                if (d.row.running) {
                    PrimaryButton(if (busy) "Bitte warten …" else "Sicherung abbrechen", Modifier.fillMaxWidth(), enabled = !busy) {
                        confirm = "cancel"
                    }
                } else {
                    PrimaryButton(if (busy) "Bitte warten …" else "Jetzt sichern", Modifier.fillMaxWidth(), enabled = !busy) {
                        confirm = "backup"
                    }
                }
                if (d.device.isNotEmpty()) {
                    SectionTitle("Zustand")
                    HubCard {
                        InfoRow("Modell", d.device["model"].orEmpty())
                        InfoRow("System", d.device["os"].orEmpty())
                        InfoRow("Karte", d.device["disk"].orEmpty())
                        InfoRow("Temperatur", d.device["temp"].orEmpty())
                        InfoRow("Arbeitsspeicher", d.device["mem"].orEmpty())
                        InfoRow("Laufzeit", d.device["uptime"].orEmpty())
                        InfoRow("Neustart nötig", d.device["reboot"].orEmpty())
                    }
                } else if (d.row.online == false) {
                    Text("Der Pi ist gerade nicht erreichbar.", color = p.blocked, fontSize = 14.sp)
                }
                d.software?.let { sw ->
                    val careful = d.row.updateMode == "careful"
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) { SectionTitle("Software") }
                        if (sw.state.isEmpty()) TextAction("Updates prüfen") { act { api.checkUpdates(id) } }
                    }
                    HubCard {
                        Text(
                            piSoftwareState(sw), fontSize = 16.sp, fontWeight = FontWeight.Bold,
                            color = if (sw.count > 0 || sw.state.isNotEmpty()) p.unknown else p.allowed,
                        )
                        InfoRow("System", d.device["os"].orEmpty().ifBlank { "–" })
                        InfoRow("Kernel", d.device["kernel"].orEmpty().ifBlank { "–" })
                        InfoRow("Pakete", if (sw.installed > 0) "${sw.installed} installiert" else "–")
                        InfoRow("Geprüft", formatEpoch(sw.checked))
                        InfoRow("Zuletzt apt", formatEpoch(sw.lastApt))
                        if (sw.lastStatus.isNotBlank()) {
                            InfoRow(
                                "Letztes Update", (if (sw.lastStatus == "ok") "erfolgreich, " else "fehlgeschlagen, ") +
                                    formatEpoch(sw.lastTime),
                            )
                        }
                        if (careful) {
                            Text(
                                "Für Updates gesperrt: ein Update kann die Software dieses Pi kaputt machen.",
                                color = p.blocked, fontSize = 14.sp,
                            )
                        }
                        if (sw.error.isNotBlank()) Text(sw.error, color = p.unknown, fontSize = 14.sp)
                        if (sw.lastStatus == "failed" && sw.lastError.isNotBlank()) Text(sw.lastError, color = p.blocked, fontSize = 14.sp)
                        if (sw.reboot) Text("Ein Neustart ist nötig.", color = p.unknown, fontSize = 14.sp)
                        sw.log.forEach { Text(it, color = p.muted, fontSize = 12.sp, maxLines = 1) }
                        if (sw.packages.isNotEmpty()) {
                            TextAction(if (showPackages) "Pakete ausblenden" else "Pakete anzeigen (${sw.packages.size})") {
                                showPackages = !showPackages
                            }
                            if (showPackages) {
                                sw.packages.forEach {
                                    Text(it.name, color = p.text, fontSize = 13.sp)
                                    Text("${it.old} → ${it.new}", color = p.muted, fontSize = 12.sp)
                                }
                            }
                        }
                    }
                    if (sw.count > 0 && sw.state.isEmpty() && !d.row.running) {
                        PrimaryButton(
                            if (busy) "Bitte warten …" else if (careful) "Trotzdem aktualisieren …" else "Jetzt aktualisieren",
                            Modifier.fillMaxWidth(), enabled = !busy,
                        ) {
                            typed = ""
                            confirm = if (careful) "upgrade-careful" else "upgrade"
                        }
                    }
                    SectionTitle("Updates bei diesem Pi")
                    HubCard {
                        PI_UPDATE_MODES.forEach { (value, label) ->
                            Row(
                                Modifier.fillMaxWidth().clickable(enabled = !busy) { if (value != d.row.updateMode) newMode = value }
                                    .heightIn(min = 40.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                RadioButton(
                                    selected = value == d.row.updateMode, enabled = !busy,
                                    onClick = { if (value != d.row.updateMode) newMode = value },
                                )
                                Text(label, color = p.text, fontSize = 14.sp)
                            }
                        }
                        Row(
                            Modifier.fillMaxWidth().clickable(enabled = !busy) {
                                act { api.setUpdateTile(id, !d.row.updateTile) }
                            }.heightIn(min = 48.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("In der Update-Kachel mitzählen", color = p.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                            Switch(
                                checked = d.row.updateTile, enabled = !busy,
                                onCheckedChange = { on -> act { api.setUpdateTile(id, on) } },
                            )
                        }
                    }
                }
                SectionTitle("Images")
                HubCard {
                    if (d.imagesList.isEmpty()) Text("Noch keine Images", color = p.muted, fontSize = 14.sp)
                    d.imagesList.forEach {
                        Text(it.file, color = p.text, fontSize = 14.sp)
                        Text("${formatEpoch(it.time)} · ${formatBytes(it.size)}", color = p.muted, fontSize = 12.sp)
                        Spacer(Modifier.height(4.dp))
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

    newMode?.let { mode ->
        val unlock = data?.row?.updateMode == "careful"
        AlertDialog(
            onDismissRequest = { newMode = null },
            title = { Text(if (unlock) "Sperre aufheben?" else "Einstellung ändern?") },
            text = {
                Text(
                    (if (unlock) "Dieser Pi ist für Updates gesperrt, weil ein Update seine Software kaputt machen kann. " else "") +
                        "Neu: ${PI_UPDATE_MODES.first { it.first == mode }.second}."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    newMode = null
                    act { api.setUpdateMode(id, mode) }
                }) { Text(if (unlock) "Sperre aufheben" else "Ändern") }
            },
            dismissButton = { TextButton(onClick = { newMode = null }) { Text("Abbrechen") } },
        )
    }

    if (confirm == "upgrade" || confirm == "upgrade-careful") {
        val careful = confirm == "upgrade-careful"
        val name = data?.row?.name.orEmpty()
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(if (careful) "Achtung: $name ist gesperrt" else "$name aktualisieren?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        if (careful) {
                            "Ein Update kann die Software dieses Pi kaputt machen. Wenn du es trotzdem willst, tippe zur " +
                                "Bestätigung den Namen „$name“ ein."
                        } else {
                            "${data?.software?.count ?: 0} Pakete werden installiert. Das dauert einige Minuten, ein Neustart erfolgt nicht."
                        }
                    )
                    if (careful) {
                        OutlinedTextField(
                            value = typed, onValueChange = { typed = it }, singleLine = true,
                            label = { Text("Name des Pi") }, modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(enabled = !careful || typed.trim() == name, onClick = {
                    confirm = null
                    val text = typed
                    act { api.upgrade(id, if (careful) text else "") }
                }) { Text(if (careful) "Trotzdem aktualisieren" else "Aktualisieren") }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Abbrechen") } },
        )
    }

    confirm?.takeIf { it == "backup" || it == "cancel" }?.let { action ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(if (action == "backup") "Sicherung starten?" else "Sicherung abbrechen?") },
            text = {
                Text(
                    if (action == "backup") {
                        "Die Sicherung läuft auf dem Unraid und belastet den Pi, bis sie fertig ist."
                    } else {
                        "Vorhandene Images bleiben erhalten. Der nächste Lauf macht beim Abgleich weiter."
                    }
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    act { if (action == "backup") api.backup(id) else api.cancel(id) }
                }) { Text("Ja") }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Nein") } },
        )
    }
}

/** Adresse und Passwort des Pi-Wartung-Dienstes; das Passwort wird in der PC-App festgelegt. */
@Composable
fun PiSetupScreen(settings: Settings, onSaved: () -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var url by remember { mutableStateOf(settings.piUrl.ifBlank { "http://" }) }
    var externalUrl by remember { mutableStateOf(settings.piExternalUrl) }
    var password by remember { mutableStateOf(settings.piPassword) }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Pi-Wartung einrichten", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "Der Dienst läuft auf dem Unraid, üblich ist Port 8092. Das Passwort legst du in der " +
                    "Pi-Wartung-App am PC unter Einstellungen → Handy-Zugang fest.",
                color = p.muted, fontSize = 14.sp,
            )
            OutlinedTextField(
                value = url, onValueChange = { url = it }, singleLine = true,
                label = { Text("Adresse im Heimnetz") }, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            OutlinedTextField(
                value = externalUrl, onValueChange = { externalUrl = it }, singleLine = true,
                label = { Text("Adresse für unterwegs (optional)") }, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            OutlinedTextField(
                value = password, onValueChange = { password = it }, singleLine = true,
                label = { Text("Passwort") }, modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            )
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            PrimaryButton(if (busy) "Prüfe …" else "Speichern und testen", Modifier.fillMaxWidth(), enabled = !busy) {
                if (externalUrl.isNotBlank() && !externalUrl.trim().startsWith("https://")) {
                    message = "Die Adresse für unterwegs muss mit https:// beginnen."
                    return@PrimaryButton
                }
                busy = true
                message = null
                scope.launch {
                    try {
                        PiApi(url, password, externalUrl).overview()
                        settings.piUrl = url
                        settings.piExternalUrl = externalUrl
                        settings.piPassword = password
                        onSaved()
                    } catch (e: ApiException) {
                        message = e.message
                    } finally {
                        busy = false
                    }
                }
            }
        }
    }
}
