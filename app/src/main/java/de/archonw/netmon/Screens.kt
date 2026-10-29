package de.archonw.netmon

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val Orange = Color(0xFFB26A00)

private fun statusLabel(status: String) = when (status) {
    "allowed" -> "erlaubt"
    "blocked" -> "gesperrt"
    else -> "unbekannt"
}

@Composable
private fun statusColor(status: String) = when (status) {
    "allowed" -> MainActivity.Green
    "blocked" -> MainActivity.Red
    else -> Orange
}

private fun formatTime(iso: String): String = runCatching {
    OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM.yy, HH:mm"))
}.getOrDefault("–")

private fun ifaceLabel(iface: String) = when (iface) {
    "802.11" -> "WLAN"
    "Ethernet" -> "LAN-Kabel"
    "" -> "–"
    else -> iface
}

@Composable
fun LockedScreen(error: String?, onUnlock: () -> Unit) {
    Scaffold { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("Netzwerkmonitor", fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            Text("Gesperrt", color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (error != null) {
                Spacer(Modifier.height(16.dp))
                Text(error, color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(24.dp))
            Button(onClick = onUnlock) { Text("Mit Fingerabdruck entsperren") }
        }
    }
}

@Composable
fun SetupScreen(
    settings: Settings,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
    onBack: () -> Unit,
    onSaved: () -> Unit,
) {
    var url by remember { mutableStateOf(settings.serverUrl.ifBlank { "http://192.168.178.207:8085" }) }
    var password by remember { mutableStateOf(settings.password) }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Scaffold { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(20.dp)) {
            if (settings.isConfigured) TextButton(onClick = onBack) { Text("‹ Zurück") }
            Text("Einstellungen", fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(16.dp))
            Text("Darstellung", fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            ChipRow(ThemeMode.entries, themeMode, { it.label }, horizontalPadding = 0, onSelect = onThemeChange)
            Spacer(Modifier.height(24.dp))
            Text("Verbindung", fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text("Adresse des Monitors") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Passwort (UI_PASSWORD)") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(20.dp))
            Button(
                enabled = !busy,
                onClick = {
                    busy = true
                    message = null
                    scope.launch {
                        try {
                            val count = Api(url, password).devices().size
                            settings.serverUrl = url
                            settings.password = password
                            message = "Verbunden, $count Geräte."
                            onSaved()
                        } catch (e: ApiException) {
                            message = e.message
                        } finally {
                            busy = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (busy) "Prüfe …" else "Speichern und testen") }
            message?.let {
                Spacer(Modifier.height(12.dp))
                Text(it)
            }
        }
    }
}

@Composable
fun ListScreen(api: Api, settings: Settings, onOpen: (String) -> Unit, onSettings: () -> Unit) {
    var devices by remember { mutableStateOf<List<Device>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var sort by remember { mutableStateOf(settings.sortOrder) }
    var onlineFilter by remember { mutableStateOf(settings.onlineFilter) }
    var statusFilter by remember { mutableStateOf(settings.statusFilter) }
    var sortMenu by remember { mutableStateOf(false) }

    LaunchedEffect(reload) {
        error = null
        try {
            devices = api.devices()
        } catch (e: ApiException) {
            error = e.message
        }
    }

    Scaffold { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            Row(
                Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Geräte", fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { reload++ }) { Text("Aktualisieren") }
                TextButton(onClick = onSettings) { Text("Einstellungen") }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(20.dp)) }
            val all = devices
            if (all == null && error == null) {
                Loading()
            } else if (all != null) {
                val list = arrange(all, sort, onlineFilter, statusFilter)
                val unknown = all.count { it.status == "unknown" }
                Text(
                    "${all.size} Geräte · ${all.count { it.online }} online" +
                        (if (unknown > 0) " · $unknown unbekannt" else "") +
                        (if (list.size != all.size) " · ${list.size} angezeigt" else ""),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
                )
                Box(Modifier.padding(horizontal = 12.dp)) {
                    TextButton(onClick = { sortMenu = true }) { Text("Sortieren: ${sort.label} ▾") }
                    DropdownMenu(expanded = sortMenu, onDismissRequest = { sortMenu = false }) {
                        SortOrder.entries.forEach { option ->
                            DropdownMenuItem(
                                text = { Text((if (option == sort) "✓ " else "") + option.label) },
                                onClick = {
                                    sort = option
                                    settings.sortOrder = option
                                    sortMenu = false
                                },
                            )
                        }
                    }
                }
                ChipRow(OnlineFilter.entries, onlineFilter, { it.label }) {
                    onlineFilter = it
                    settings.onlineFilter = it
                }
                ChipRow(StatusFilter.entries, statusFilter, { it.label }) {
                    statusFilter = it
                    settings.statusFilter = it
                }
                if (list.isEmpty()) {
                    Text(
                        "Keine Geräte für diesen Filter.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(20.dp),
                    )
                }
                LazyColumn(Modifier.fillMaxSize()) {
                    items(list, key = { it.mac }) { d ->
                        Row(
                            Modifier.fillMaxWidth().clickable { onOpen(d.mac) }
                                .padding(horizontal = 20.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(d.title, fontWeight = FontWeight.Medium)
                                Text(
                                    "${d.ip.ifBlank { "keine IP" }} · ${if (d.online) "online" else "offline"}",
                                    fontSize = 13.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(statusLabel(d.status), color = statusColor(d.status), fontSize = 13.sp)
                        }
                        HorizontalDivider()
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun <T> ChipRow(
    options: List<T>,
    selected: T,
    label: (T) -> String,
    horizontalPadding: Int = 20,
    onSelect: (T) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
            .padding(horizontal = horizontalPadding.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        options.forEach { option ->
            FilterChip(
                selected = option == selected,
                onClick = { onSelect(option) },
                label = { Text(label(option)) },
            )
        }
    }
}

@Composable
private fun Loading() {
    Column(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator()
    }
}

@Composable
fun DetailScreen(api: Api, mac: String, onBack: () -> Unit) {
    var device by remember(mac) { mutableStateOf<Device?>(null) }
    var message by remember(mac) { mutableStateOf<String?>(null) }
    var busy by remember(mac) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(mac) {
        try {
            device = api.device(mac)
        } catch (e: ApiException) {
            message = e.message
        }
    }

    fun act(allow: Boolean) {
        busy = true
        message = null
        scope.launch {
            try {
                device = if (allow) api.allow(mac) else api.block(mac)
                message = if (allow) "Gerät ist erlaubt." else "Gerät ist gesperrt."
            } catch (e: ApiException) {
                message = e.message
            } finally {
                busy = false
            }
        }
    }

    Scaffold { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(20.dp)) {
            TextButton(onClick = onBack) { Text("‹ Alle Geräte") }
            val d = device
            if (d == null) {
                val err = message
                if (err == null) Loading() else Text(err, color = MaterialTheme.colorScheme.error)
            } else {
            Text(statusLabel(d.status), color = statusColor(d.status), fontWeight = FontWeight.Medium)
            Text(d.title, fontSize = 26.sp, fontWeight = FontWeight.SemiBold)
            Text(
                if (d.online) "gerade online" else "offline",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(16.dp))
            Card(shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
                    listOf(
                        "Gerätename" to d.hostname.ifBlank { "–" },
                        "IP-Adresse" to d.ip.ifBlank { "–" },
                        "MAC-Adresse" to d.mac,
                        "Verbindung" to ifaceLabel(d.iface),
                        "Zuerst gesehen" to formatTime(d.firstSeen),
                        "Zuletzt gesehen" to formatTime(d.lastSeen),
                    ).forEachIndexed { i, (k, v) ->
                        if (i > 0) HorizontalDivider()
                        Row(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                            Text(k, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                            Text(v)
                        }
                    }
                }
            }
            if (d.randomMac) {
                Spacer(Modifier.height(12.dp))
                Text(
                    "Zufällige (private) MAC-Adresse: Das kann auch ein bekanntes Handy mit neuer Adresse sein.",
                    color = Orange,
                    fontSize = 14.sp,
                )
            }
            message?.let {
                Spacer(Modifier.height(16.dp))
                Text(it, modifier = Modifier.fillMaxWidth())
            }
            Spacer(Modifier.weight(1f))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = { act(true) },
                    enabled = !busy && d.status != "allowed",
                    colors = ButtonDefaults.buttonColors(containerColor = MainActivity.Green),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.weight(1f).height(60.dp),
                ) { Text("Erlauben", fontSize = 18.sp) }
                Button(
                    onClick = { act(false) },
                    enabled = !busy && d.status != "blocked",
                    colors = ButtonDefaults.buttonColors(containerColor = MainActivity.Red),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.weight(1f).height(60.dp),
                ) { Text("Sperren", fontSize = 18.sp) }
            }
            }
        }
    }
}
