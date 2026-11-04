package de.archonw.netmon

import android.content.Intent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel

private const val WG_REFRESH_MS = 30_000L

fun wireGuardTileText(o: WgOverview): String = when {
    !o.configured -> "Anmeldung fehlt"
    o.error.isNotBlank() -> o.error
    else -> {
        val online = o.clients.count { it.online }
        val n = o.clients.size
        if (online > 0) t(if (n == 1) "{0} Zugang, {1} verbunden" else "{0} Zugänge, {1} verbunden", n, online)
        else t(if (n == 1) "{0} Zugang" else "{0} Zugänge", n)
    }
}

private data class WgState(val label: String, val color: Color)

@Composable
private fun wgState(c: WgClient): WgState {
    val p = LocalPalette.current
    return when {
        !c.enabled -> WgState("Gesperrt", p.blocked)
        c.expired -> WgState("Abgelaufen", p.blocked)
        c.online -> WgState("Verbunden", p.allowed)
        c.handshake > 0 -> WgState("Zuletzt ${formatEpoch(c.handshake)}", p.muted)
        else -> WgState("Noch nie verbunden", p.unknown)
    }
}

@Composable
fun WireGuardScreen(api: WireGuardApi, onOpen: (Int) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var adding by remember { mutableStateOf(false) }
    var qr by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val (data, error) = rememberLoaded(reload, WG_REFRESH_MS) { api.overview() }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("WireGuard", onBack = onBack, trailing = {
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
                    Text("Es fehlt noch die Anmeldung für WireGuard-Easy.", color = p.text, fontSize = 15.sp)
                    Text("In Home-Net im Browser beim Modul WireGuard unter Einstellungen eintragen.", color = p.muted, fontSize = 13.sp)
                }
                data.error.isNotBlank() -> Text(data.error, color = p.blocked, fontSize = 14.sp)
                else -> {
                    if (data.clients.isEmpty()) {
                        HubCard {
                            Text("Noch keine Zugänge.", color = p.text, fontSize = 15.sp)
                            Text("Mit „Neu“ einen anlegen und den QR-Code mit der App WireGuard scannen.", color = p.muted, fontSize = 13.sp)
                        }
                    }
                    data.clients.forEach { c ->
                        val state = wgState(c)
                        HubCard(onClick = { onOpen(c.id) }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(c.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                                Text(state.label, color = state.color, fontSize = 13.sp)
                            }
                            Text(
                                listOfNotNull(
                                    c.address.ifBlank { null },
                                    if (c.rx + c.tx > 0) "↓ ${formatBytes(c.tx)} ↑ ${formatBytes(c.rx)}" else null,
                                ).joinToString(" · "),
                                color = p.muted, fontSize = 13.sp,
                            )
                        }
                    }
                    if (data.version.isNotBlank()) {
                        Text(
                            if (data.update) t("WireGuard-Easy {0} · Update auf {1} verfügbar", data.version, data.latest) else "WireGuard-Easy ${data.version}",
                            color = p.muted, fontSize = 12.sp,
                        )
                    }
                    Spacer(Modifier.height(16.dp))
                }
            }
        }
    }

    if (adding) {
        var name by remember { mutableStateOf("") }
        var created by remember { mutableStateOf<Int?>(null) }
        FormDialog(
            title = "Neuer Zugang", confirm = "Anlegen", onDismiss = { adding = false },
            onConfirm = { created = api.create(name) },
            onDone = {
                adding = false
                reload++
                created?.let { qr = it to name.trim() }
            },
        ) {
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, placeholder = { Text("z. B. Handy Anna") },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Text("Danach erscheint der QR-Code. Ihn auf dem Handy mit der App WireGuard scannen.", color = p.muted, fontSize = 12.sp)
        }
    }
    qr?.let { (id, name) -> QrDialog(api, id, name) { qr = null } }
}

@Composable
fun WireGuardClientScreen(api: WireGuardApi, id: Int, onBack: () -> Unit) {
    val p = LocalPalette.current
    var reload by remember { mutableStateOf(0) }
    var confirm by remember { mutableStateOf<Triple<String, String, suspend () -> Unit>?>(null) }
    var showQr by remember { mutableStateOf(false) }
    val (data, error) = rememberLoaded(reload, WG_REFRESH_MS) { api.overview() }
    val client = data?.clients?.firstOrNull { it.id == id }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle(client?.name ?: "Zugang", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            (error ?: data?.error?.takeIf { it.isNotBlank() })?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            if (data != null && client == null && error == null) Text("Diesen Zugang gibt es nicht mehr.", color = p.muted)
            if (client != null) {
                val state = wgState(client)
                HubCard {
                    Text(state.label, color = state.color, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                    if (client.address.isNotBlank()) InfoRow("Adresse im VPN", client.address)
                    InfoRow("Angelegt", formatEpoch(client.created))
                    if (client.expires > 0) InfoRow("Gültig bis", formatEpoch(client.expires))
                    if (client.handshake > 0) InfoRow("Letzter Kontakt", formatEpoch(client.handshake))
                    if (client.rx + client.tx > 0) {
                        InfoRow("Empfangen", formatBytes(client.tx))
                        InfoRow("Gesendet", formatBytes(client.rx))
                    }
                }
                SectionTitle("Einrichten")
                HubCard {
                    Text("Auf dem Handy in der App WireGuard auf „+“ und „QR-Code scannen“ tippen.", color = p.muted, fontSize = 13.sp)
                    TextAction("QR-Code zeigen") { showQr = true }
                }
                SectionTitle("Verwalten")
                HubCard {
                    Row {
                        TextAction(if (client.enabled) "Sperren" else "Entsperren") {
                            confirm = if (client.enabled) {
                                Triple("${client.name} sperren? Das Gerät kommt dann nicht mehr ins Heimnetz.", "Sperren") { api.setEnabled(client.id, false) }
                            } else {
                                Triple("${client.name} entsperren?", "Entsperren") { api.setEnabled(client.id, true) }
                            }
                        }
                        TextAction("Löschen") {
                            confirm = Triple("${client.name} löschen? Der QR-Code gilt dann nicht mehr.", "Löschen") {
                                api.delete(client.id)
                                onBack()
                            }
                        }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    confirm?.let { (question, label, action) ->
        FormDialog(
            title = question, confirm = label, onDismiss = { confirm = null },
            onConfirm = action, onDone = { confirm = null; reload++ },
        ) {}
    }
    if (showQr && client != null) QrDialog(api, client.id, client.name) { showQr = false }
}

/** Holt die Konfiguration und zeigt sie als QR-Code. Sie enthält den geheimen Schlüssel und bleibt nur im Speicher. */
@Composable
private fun QrDialog(api: WireGuardApi, id: Int, name: String, onClose: () -> Unit) {
    val p = LocalPalette.current
    val context = LocalContext.current
    var config by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(id) {
        try { config = api.config(id) } catch (e: ApiException) { error = e.message }
    }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("QR-Code für $name") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when {
                    error != null -> Text(error!!, color = p.blocked, fontSize = 14.sp)
                    config == null -> Text("Wird geladen …", color = p.muted)
                    else -> {
                        QrCode(config!!, Modifier.fillMaxWidth())
                        Text("Mit der App WireGuard scannen. Der Code ist der Schlüssel ins Heimnetz, also nur dem eigenen Gerät zeigen.",
                            color = p.muted, fontSize = 12.sp)
                        TextAction("Als Text teilen") {
                            val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, config)
                            context.startActivity(Intent.createChooser(send, "Konfiguration teilen"))
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Fertig") } },
    )
}

@Composable
private fun QrCode(text: String, modifier: Modifier = Modifier) {
    val matrix = remember(text) {
        QRCodeWriter().encode(
            text, BarcodeFormat.QR_CODE, 0, 0,
            mapOf(EncodeHintType.MARGIN to 0, EncodeHintType.CHARACTER_SET to "UTF-8",
                EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M),
        )
    }
    // Weißer Rand und schwarze Punkte auch im dunklen Design, sonst erkennen manche Kameras den Code nicht
    Box(modifier.aspectRatio(1f).clip(RoundedCornerShape(12.dp)).background(Color.White).padding(14.dp)) {
        Canvas(Modifier.fillMaxSize()) {
            val n = matrix.width
            val cell = size.minDimension / n
            for (y in 0 until n) for (x in 0 until n) {
                if (matrix[x, y]) drawRect(Color.Black, Offset(x * cell, y * cell), Size(cell + 0.6f, cell + 0.6f))
            }
        }
    }
}
