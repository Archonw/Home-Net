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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private const val NPM_REFRESH_MS = 120_000L

private val NPM_GROUPS = listOf(
    Triple("public", "Öffentlich", "Von überall erreichbar."),
    Triple("password", "Mit Passwort", "Von außen nur mit Passwort der Zugriffsliste."),
    Triple("restricted", "Nur bestimmte Adressen", "Von außen nur für die Adressen in der Zugriffsliste."),
    Triple("internal", "Nur intern", "Nur aus dem Heimnetz und über VPN."),
    Triple("off", "Abgeschaltet", ""),
)

fun proxyTileText(o: NpmOverview): String = when {
    !o.configured -> "Anmeldung fehlt"
    o.error.isNotBlank() -> o.error
    else -> buildString {
        append("${o.count("public") + o.count("password") + o.count("restricted")} öffentlich, ${o.count("internal")} intern")
        when {
            o.certsExpired > 0 -> append(", ${o.certsExpired} Zertifikat abgelaufen")
            o.certsSoon > 0 -> append(", ${o.certsSoon} Zertifikat läuft bald ab")
        }
    }
}

private fun formatDay(seconds: Long): String =
    if (seconds <= 0) "–" else Instant.ofEpochSecond(seconds).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("dd.MM.yyyy"))

private fun daysText(state: String, days: Int?): String = when {
    state == "expired" -> "abgelaufen"
    days == null -> "Ablauf unbekannt"
    days == 0 -> "läuft heute ab"
    days == 1 -> "noch 1 Tag"
    else -> "noch $days Tage"
}

@Composable
private fun certColor(state: String): Color {
    val p = LocalPalette.current
    return when (state) {
        "expired" -> p.blocked
        "soon" -> p.unknown
        else -> p.muted
    }
}

@Composable
fun ProxyScreen(api: ProxyApi, onOpen: (String) -> Unit, onCerts: () -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var setup by remember { mutableStateOf(false) }
    val (data, error) = rememberLoaded(reload, NPM_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Proxy Manager", onBack = onBack, trailing = {
            if (data?.configured == true && data.error.isBlank()) TextAction("Zertifikate", onCerts)
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            when {
                data == null -> if (error == null) Text("Wird geladen …", color = p.muted)
                !data.configured || data.error.isNotBlank() -> HubCard {
                    if (data.error.isNotBlank()) Text(data.error, color = p.blocked, fontSize = 14.sp)
                    else Text("Home-Net braucht noch einen Benutzer im Proxy Manager.", color = p.text, fontSize = 15.sp)
                    Text("Am besten im Proxy Manager einen eigenen Benutzer „Home-Net“ anlegen, der alles nur ansehen darf.", color = p.muted, fontSize = 13.sp)
                    TextAction("Anmeldung eintragen") { setup = true }
                }
                else -> {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        StatTile("Öffentlich", "${data.count("public") + data.count("password") + data.count("restricted")}",
                            if (data.count("password") > 0) "${data.count("password")} mit Passwort" else "von überall", Modifier.weight(1f))
                        StatTile("Nur intern", "${data.count("internal")}", "Heimnetz und VPN", Modifier.weight(1f))
                    }
                    if (data.certsExpired > 0 || data.certsSoon > 0) {
                        HubCard(onClick = onCerts) {
                            val parts = listOfNotNull(
                                if (data.certsExpired > 0) "${data.certsExpired} Zertifikat${if (data.certsExpired > 1) "e" else ""} abgelaufen" else null,
                                if (data.certsSoon > 0) "${data.certsSoon} ${if (data.certsSoon > 1) "laufen" else "läuft"} bald ab" else null,
                            )
                            Text(parts.joinToString(", "), color = if (data.certsExpired > 0) p.blocked else p.unknown, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                    NPM_GROUPS.forEach { (kind, label, hint) ->
                        val rows = data.hosts.filter { it.exposure == kind }
                        if (rows.isNotEmpty()) {
                            SectionTitle(label)
                            if (hint.isNotBlank()) Text(hint, color = p.muted, fontSize = 12.sp)
                            rows.forEach { h -> HostCard(h) { onOpen(h.key) } }
                        }
                    }
                    if (data.streams.isNotEmpty()) {
                        SectionTitle("Weitergeleitete Ports")
                        HubCard {
                            data.streams.forEach { s ->
                                val proto = listOfNotNull(if (s.tcp) "TCP" else null, if (s.udp) "UDP" else null).joinToString("/")
                                InfoRow("Port ${s.port} $proto", s.target + if (s.enabled) "" else " (aus)")
                            }
                        }
                    }
                    if (data.hosts.isEmpty()) Text("Im Proxy Manager sind noch keine Adressen eingetragen.", color = p.muted)
                    Text("Ob der Router die Ports weiterleitet, sieht Home-Net nicht.", color = p.muted, fontSize = 12.sp)
                    Spacer(Modifier.height(16.dp))
                }
            }
        }
    }

    if (setup) ProxySetupDialog(api, onDone = { setup = false; reload++ }, onDismiss = { setup = false })
}

@Composable
private fun HostCard(h: NpmHost, onClick: () -> Unit) {
    val p = LocalPalette.current
    HubCard(onClick = onClick) {
        Text(h.domains.joinToString(", "), color = if (h.enabled) p.text else p.muted, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
        val kind = when (h.kind) { "redirect" -> "Weiterleitung · "; "404" -> "404-Seite · "; else -> "" }
        Text(kind + h.target, color = p.muted, fontSize = 13.sp)
        if (h.enabled) {
            val ssl = h.ssl
            if (ssl != null) Text("Zertifikat ${daysText(ssl.state, ssl.days)}", color = certColor(ssl.state), fontSize = 13.sp)
            else Text("ohne https", color = p.muted, fontSize = 13.sp)
            h.problems.forEach { Text(it, color = p.unknown, fontSize = 13.sp) }
        }
    }
}

@Composable
fun ProxyHostScreen(api: ProxyApi, key: String, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var confirm by remember { mutableStateOf<Pair<String, suspend () -> Unit>?>(null) }
    val (data, error) = rememberLoaded(reload, NPM_REFRESH_MS) { api.overview() }
    val host = data?.hosts?.firstOrNull { it.key == key }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(host?.title ?: "Adresse", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            (error ?: data?.error?.takeIf { it.isNotBlank() })?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (data != null && host == null && error == null) Text("Diese Adresse gibt es nicht mehr.", color = p.muted)
            if (host != null) {
                HubCard {
                    Text(host.exposureLabel, color = if (host.exposure == "internal" || host.exposure == "off") p.text else p.unknown,
                        fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                    if (host.note.isNotBlank()) Text(host.note, color = p.muted, fontSize = 13.sp)
                }
                HubCard {
                    if (host.domains.size > 1) InfoRow("Adressen", host.domains.joinToString("\n"))
                    InfoRow(if (host.kind == "redirect") "Leitet um auf" else "Ziel", host.target.ifBlank { "–" })
                    if (host.targetState.isNotBlank()) InfoRow("Ziel antwortet", when (host.targetState) {
                        "up" -> "ja"; "down" -> "nein"; else -> "nicht prüfbar"
                    })
                    InfoRow("Zugriffsliste", host.accessList.ifBlank { "keine" })
                    val ssl = host.ssl
                    InfoRow("Zertifikat", if (ssl == null) "keins" else "${ssl.name}\n${daysText(ssl.state, ssl.days)} (bis ${formatDay(ssl.expires)})")
                    if (ssl != null) InfoRow("Nur https", if (host.sslForced) "ja" else "nein")
                }
                if (host.dns.isNotEmpty()) {
                    SectionTitle("Im Internet eingetragen")
                    HubCard {
                        host.dns.forEach { d ->
                            InfoRow(d.domain, when (d.state) {
                                "ok" -> d.ips.joinToString("\n")
                                "wildcard" -> "Platzhalter, nicht prüfbar"
                                "error" -> "nicht prüfbar"
                                else -> NPM_DNS_TEXT[d.state] ?: d.state
                            })
                        }
                    }
                }
                host.problems.forEach { Text(it, color = p.unknown, fontSize = 14.sp) }
                Row {
                    val switch: suspend () -> Unit = { api.setEnabled(host, !host.enabled) }
                    TextAction(if (host.enabled) "Abschalten" else "Einschalten") {
                        confirm = Pair(
                            if (host.enabled) "${host.title} abschalten? Die Adresse ist dann auch im Heimnetz nicht mehr erreichbar."
                            else "${host.title} wieder einschalten?",
                            switch,
                        )
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }
    confirm?.let { (question, action) ->
        FormDialog(title = question, confirm = "Ja", onDismiss = { confirm = null }, onConfirm = action, onDone = { confirm = null; reload++ }) {}
    }
}

@Composable
fun ProxyCertsScreen(api: ProxyApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var confirm by remember { mutableStateOf<NpmCert?>(null) }
    val (data, error) = rememberLoaded(reload, NPM_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Zertifikate", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            (error ?: data?.error?.takeIf { it.isNotBlank() })?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (data == null && error == null) Text("Wird geladen …", color = p.muted)
            if (data != null) {
                val (used, unused) = data.certs.partition { !it.unused }
                used.forEach { c -> CertCard(c) { confirm = c } }
                if (unused.isNotEmpty()) {
                    SectionTitle("Nicht mehr genutzt")
                    Text("Keine Adresse verwendet diese Zertifikate. Sie lassen sich im Proxy Manager löschen.", color = p.muted, fontSize = 12.sp)
                    unused.forEach { c -> CertCard(c, null) }
                }
                if (data.certs.isEmpty()) Text("Keine Zertifikate.", color = p.muted)
                Spacer(Modifier.height(16.dp))
            }
        }
    }
    confirm?.let { c ->
        FormDialog(
            title = "${c.name} jetzt erneuern?", confirm = "Erneuern", onDismiss = { confirm = null },
            onConfirm = { api.renew(c.id) }, onDone = { confirm = null; reload++ },
        ) {
            Text("Let's Encrypt prüft dabei die Adresse von außen. Das kann bis zu einer Minute dauern.", color = LocalPalette.current.muted, fontSize = 13.sp)
        }
    }
}

@Composable
private fun CertCard(c: NpmCert, onRenew: (() -> Unit)?) {
    val p = LocalPalette.current
    HubCard {
        Row(Modifier.fillMaxWidth()) {
            Text(c.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
            Text(daysText(c.state, c.days), color = if (c.unused) p.muted else certColor(c.state), fontSize = 13.sp)
        }
        Text(
            "bis ${formatDay(c.expires)} · " + if (c.provider == "letsencrypt") "Let's Encrypt, erneuert sich selbst" else "eigenes Zertifikat",
            color = p.muted, fontSize = 13.sp,
        )
        if (c.hosts.isNotEmpty()) Text("Für ${c.hosts.joinToString(", ")}", color = p.muted, fontSize = 13.sp)
        if (onRenew != null && c.provider == "letsencrypt" && c.state != "ok") TextAction("Jetzt erneuern", onRenew)
    }
}

@Composable
private fun ProxySetupDialog(api: ProxyApi, onDone: () -> Unit, onDismiss: () -> Unit) {
    var url by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var passwordSet by remember { mutableStateOf(false) }
    val (current, _) = rememberLoaded(0, Long.MAX_VALUE) { api.settings() }
    var filled by remember { mutableStateOf(false) }
    if (current != null && !filled) {
        url = current.url
        email = current.email
        passwordSet = current.passwordSet
        filled = true
    }
    FormDialog(
        title = "Anmeldung am Proxy Manager", confirm = "Speichern", onDismiss = onDismiss,
        onConfirm = {
            api.saveSettings(url, email, password)
            val o = api.overview()
            require(o.error.isBlank()) { o.error }
        },
        onDone = onDone,
    ) {
        OutlinedTextField(url, { url = it }, label = { Text("Adresse der Verwaltung (Port 81)") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
        OutlinedTextField(email, { email = it }, label = { Text("E-Mail des Benutzers") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text(if (passwordSet) "Passwort (gespeichert)" else "Passwort") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth())
        Text("Das Passwort bleibt auf deinem Server.", color = LocalPalette.current.muted, fontSize = 12.sp)
    }
}
