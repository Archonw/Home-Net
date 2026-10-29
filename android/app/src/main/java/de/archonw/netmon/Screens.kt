package de.archonw.netmon

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

fun statusLabel(status: String) = when (status) {
    "allowed" -> "erlaubt"
    "blocked" -> "gesperrt"
    else -> "unbekannt"
}

fun formatTime(iso: String): String = runCatching {
    OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM.yy, HH:mm"))
}.getOrDefault("–")

fun ifaceLabel(iface: String) = when (iface) {
    "802.11" -> "WLAN"
    "Ethernet" -> "LAN-Kabel"
    "" -> "–"
    else -> iface
}

/** Alles, was eine Geräteliste anzeigen und auslösen kann – unabhängig vom Design. */
class ListUi(
    val all: List<Device>?,
    val shown: List<Device>,
    val error: String?,
    val info: String?,
    val refreshing: Boolean,
    val onRefresh: () -> Unit,
    val sort: SortOrder,
    val onSort: (SortOrder) -> Unit,
    val filter: QuickFilter,
    val onFilter: (QuickFilter) -> Unit,
    val query: String,
    val onQuery: (String) -> Unit,
    val searching: Boolean,
    val onToggleSearch: () -> Unit,
    val canRemoveOffline: Boolean,
    val onRemoveOffline: () -> Unit,
    val onOpen: (String) -> Unit,
) {
    fun count(f: QuickFilter) = all.orEmpty().let { arrange(it, sort, f).size }
}

class DetailUi(
    val device: Device?,
    val error: String?,
    val message: String?,
    val busy: Boolean,
    val onAllow: () -> Unit,
    val onBlock: () -> Unit,
    val onBack: () -> Unit,
    val onCopy: (label: String, value: String) -> Unit,
)

class SettingsUi(
    val configured: Boolean,
    val themeMode: ThemeMode,
    val onTheme: (ThemeMode) -> Unit,
    val style: AppStyle,
    val onStyle: (AppStyle) -> Unit,
    val accent: Accent,
    val onAccent: (Accent) -> Unit,
    val useBiometrics: Boolean,
    val onLockMode: (Boolean) -> Unit,
    val url: String,
    val onUrl: (String) -> Unit,
    val password: String,
    val onPassword: (String) -> Unit,
    val busy: Boolean,
    val message: String?,
    val onSave: () -> Unit,
)

@Composable
fun ListScreen(api: Api, settings: Settings, style: AppStyle, onOpen: (String) -> Unit) {
    var devices by remember { mutableStateOf<List<Device>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var info by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableStateOf(0) }
    var refreshing by remember { mutableStateOf(false) }
    var sort by remember { mutableStateOf(settings.sortOrder) }
    var filter by remember { mutableStateOf(settings.quickFilter) }
    var query by remember { mutableStateOf("") }
    var searching by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(reload) {
        error = null
        try {
            devices = api.devices()
        } catch (e: ApiException) {
            error = e.message
        } finally {
            refreshing = false
        }
    }

    val all = devices
    val offline = all.orEmpty().count { !it.online && it.status != "blocked" }
    val ui = ListUi(
        all = all,
        shown = all?.let { arrange(it, sort, filter, query) }.orEmpty(),
        error = error,
        info = info,
        refreshing = refreshing,
        onRefresh = {
            refreshing = true
            info = null
            reload++
        },
        sort = sort,
        onSort = {
            sort = it
            settings.sortOrder = it
        },
        filter = filter,
        onFilter = {
            filter = it
            settings.quickFilter = it
        },
        query = query,
        onQuery = { query = it },
        searching = searching,
        onToggleSearch = {
            searching = !searching
            if (!searching) query = ""
        },
        canRemoveOffline = offline > 0,
        onRemoveOffline = { confirmRemove = true },
        onOpen = onOpen,
    )

    if (confirmRemove) {
        AlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("Offline-Geräte entfernen?") },
            text = {
                Text(
                    "$offline Geräte, die gerade offline sind, werden aus der Liste gelöscht. " +
                        "Kommen sie wieder ins Netz, gelten sie als neu und du musst sie erneut bestätigen.\n\n" +
                        "Gesperrte Geräte bleiben in der Liste, damit ihre Sperre erhalten bleibt.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmRemove = false
                    scope.launch {
                        try {
                            info = "${api.removeOffline()} Geräte entfernt."
                            reload++
                        } catch (e: ApiException) {
                            error = e.message
                        }
                    }
                }) { Text("Entfernen") }
            },
            dismissButton = { TextButton(onClick = { confirmRemove = false }) { Text("Abbrechen") } },
        )
    }

    when (style) {
        AppStyle.SOFT -> SoftList(ui)
        AppStyle.TILES -> TilesList(ui)
    }
}

@Composable
fun DetailScreen(api: Api, mac: String, style: AppStyle, onBack: () -> Unit) {
    var device by remember(mac) { mutableStateOf<Device?>(null) }
    var error by remember(mac) { mutableStateOf<String?>(null) }
    var message by remember(mac) { mutableStateOf<String?>(null) }
    var busy by remember(mac) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current

    LaunchedEffect(mac) {
        try {
            device = api.device(mac)
        } catch (e: ApiException) {
            error = e.message
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

    val ui = DetailUi(
        device = device,
        error = error,
        message = message,
        busy = busy,
        onAllow = { act(true) },
        onBlock = { act(false) },
        onBack = onBack,
        onCopy = { label, value ->
            clipboard.setText(AnnotatedString(value))
            message = "$label kopiert."
        },
    )
    when (style) {
        AppStyle.SOFT -> SoftDetail(ui)
        AppStyle.TILES -> TilesDetail(ui)
    }
}

@Composable
fun SettingsScreen(
    settings: Settings,
    themeMode: ThemeMode,
    onThemeChange: (ThemeMode) -> Unit,
    style: AppStyle,
    onStyleChange: (AppStyle) -> Unit,
    accent: Accent,
    onAccentChange: (Accent) -> Unit,
    useBiometrics: Boolean,
    onLockModeChange: (Boolean) -> String?,
    onSaved: () -> Unit,
) {
    var url by remember { mutableStateOf(settings.serverUrl.ifBlank { "http://192.168.178.207:8085" }) }
    var password by remember { mutableStateOf(settings.password) }
    var message by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    val ui = SettingsUi(
        configured = settings.isConfigured,
        themeMode = themeMode,
        onTheme = onThemeChange,
        style = style,
        onStyle = onStyleChange,
        accent = accent,
        onAccent = onAccentChange,
        useBiometrics = useBiometrics,
        onLockMode = { message = onLockModeChange(it) },
        url = url,
        onUrl = { url = it },
        password = password,
        onPassword = { password = it },
        busy = busy,
        message = message,
        onSave = {
            busy = true
            message = null
            scope.launch {
                try {
                    val count = Api(url, password).devices().size
                    val first = !settings.isConfigured
                    settings.serverUrl = url
                    settings.password = password
                    message = "Verbunden, $count Geräte."
                    if (first) onSaved()
                } catch (e: ApiException) {
                    message = e.message
                } finally {
                    busy = false
                }
            }
        },
    )
    when (style) {
        AppStyle.SOFT -> SoftSettings(ui)
        AppStyle.TILES -> TilesSettings(ui)
    }
}

/** Untere Leiste mit „Geräte“ und „Einstellungen“ in der Form des gewählten Designs. */
@Composable
fun NavBar(style: AppStyle, settingsActive: Boolean, onList: () -> Unit, onSettings: () -> Unit) {
    val p = LocalPalette.current
    Row(
        Modifier.fillMaxWidth().background(if (style == AppStyle.SOFT) p.card else p.bg)
            .navigationBarsPadding().padding(horizontal = 24.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceAround,
    ) {
        NavItem("Geräte", NavIcons.devices, !settingsActive, onList)
        NavItem("Einstellungen", NavIcons.settings, settingsActive, onSettings)
    }
}

@Composable
private fun NavItem(label: String, icon: ImageVector, active: Boolean, onClick: () -> Unit) {
    val p = LocalPalette.current
    val color = if (active) (if (p.style == AppStyle.SOFT) p.accentText else p.text) else p.muted
    Column(
        Modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier.size(width = 60.dp, height = 32.dp).clip(RoundedCornerShape(16.dp))
                .background(if (active && p.style == AppStyle.TILES) p.accentSoft else Color.Transparent),
            contentAlignment = Alignment.Center,
        ) { Icon(icon, contentDescription = null, tint = color) }
        Text(label, color = color, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** Sortier-Knopf mit Menü; beide Designs nutzen ihn. */
@Composable
fun SortButton(sort: SortOrder, onSort: (SortOrder) -> Unit, color: Color) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }) {
            Text("${sort.label} ▾", color = color, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            SortOrder.entries.forEach { option ->
                DropdownMenuItem(
                    text = { Text((if (option == sort) "✓ " else "") + option.label) },
                    onClick = {
                        onSort(option)
                        open = false
                    },
                )
            }
        }
    }
}

/** Menü hinter den drei Punkten: Aktualisieren, Offline entfernen. */
@Composable
fun MoreMenu(ui: ListUi, tint: Color, background: Color) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconTile(NavIcons.more, "Weitere Aktionen", tint, background) { open = true }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Aktualisieren") }, onClick = {
                open = false
                ui.onRefresh()
            })
            DropdownMenuItem(
                text = { Text("Offline-Geräte entfernen") },
                enabled = ui.canRemoveOffline,
                onClick = {
                    open = false
                    ui.onRemoveOffline()
                },
            )
        }
    }
}

@Composable
fun IconTile(icon: ImageVector, description: String, tint: Color, background: Color, onClick: () -> Unit) {
    Box(
        Modifier.size(44.dp).clip(RoundedCornerShape(12.dp)).background(background).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Icon(icon, contentDescription = description, tint = tint) }
}

@Composable
fun SearchField(ui: ListUi, shape: RoundedCornerShape) {
    val p = LocalPalette.current
    OutlinedTextField(
        value = ui.query,
        onValueChange = ui.onQuery,
        placeholder = { Text("Name, IP oder MAC", color = p.muted) },
        singleLine = true,
        shape = shape,
        colors = fieldColors(),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun fieldColors() = LocalPalette.current.let { p ->
    OutlinedTextFieldDefaults.colors(
        focusedTextColor = p.text,
        unfocusedTextColor = p.text,
        focusedBorderColor = p.accentText,
        unfocusedBorderColor = p.line,
        focusedLabelColor = p.accentText,
        unfocusedLabelColor = p.muted,
        cursorColor = p.accentText,
        focusedContainerColor = p.card,
        unfocusedContainerColor = p.card,
    )
}

@Composable
fun Loading() {
    Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = LocalPalette.current.accentText)
    }
}

@Composable
fun LockedScreen(
    useBiometrics: Boolean,
    error: String?,
    onBiometric: () -> Unit,
    onPassword: (String) -> Unit,
) {
    val p = LocalPalette.current
    var password by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().background(p.bg).statusBarsPadding().navigationBarsPadding().imePadding()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Netzwerkmonitor", color = p.text, fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(6.dp))
        Text("Gesperrt", color = p.muted)
        if (error != null) {
            Spacer(Modifier.height(16.dp))
            Text(error, color = p.danger)
        }
        Spacer(Modifier.height(24.dp))
        // Passwort geht immer, z. B. auch wenn der Fingerabdruck nicht klappt
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Passwort") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { onPassword(password) }),
            shape = RoundedCornerShape(14.dp),
            colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        PrimaryButton("Entsperren", Modifier.fillMaxWidth()) { onPassword(password) }
        if (useBiometrics) {
            Spacer(Modifier.height(8.dp))
            TextButton(onClick = onBiometric) { Text("Mit Fingerabdruck entsperren", color = p.accentText) }
        }
    }
}

@Composable
fun PrimaryButton(label: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    val p = LocalPalette.current
    Box(
        modifier.height(52.dp).clip(RoundedCornerShape(14.dp))
            .background(if (enabled) p.accent else p.line)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = if (enabled) p.onAccent else p.muted, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}
