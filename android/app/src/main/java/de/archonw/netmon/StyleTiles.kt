package de.archonw.netmon

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Entwurf B „Kacheln“: Zählkacheln als Filter, flache Liste, Festbreitenschrift für IP und MAC.

private val TileShape = RoundedCornerShape(14.dp)

@Composable
private fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        color = LocalPalette.current.muted,
        fontSize = 12.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 1.sp,
        modifier = modifier.padding(start = 4.dp, top = 8.dp, bottom = 2.dp),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TilesList(ui: ListUi) {
    val p = LocalPalette.current
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().height(60.dp), verticalAlignment = Alignment.CenterVertically) {
            if (ui.searching) {
                Box(Modifier.weight(1f)) { SearchField(ui, TileShape) }
                Spacer(Modifier.size(8.dp))
                IconTile(NavIcons.close, "Suche schließen", p.text, p.card, ui.onToggleSearch)
            } else {
                Text(
                    "Geräte",
                    color = p.text,
                    fontSize = 30.sp,
                    fontWeight = FontWeight.ExtraBold,
                    modifier = Modifier.weight(1f).padding(start = 4.dp),
                )
                IconTile(NavIcons.search, "Suchen", p.text, p.card, ui.onToggleSearch)
                Spacer(Modifier.size(8.dp))
                MoreMenu(ui, p.text, p.card)
            }
        }
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = ui.onRefresh, modifier = Modifier.weight(1f)) {
            LazyColumn(Modifier.fillMaxSize()) {
                item {
                  Column {
                    Text(
                        "${ui.all.orEmpty().size} Geräte",
                        color = p.muted,
                        fontSize = 13.sp,
                        modifier = Modifier.padding(start = 4.dp, bottom = 12.dp),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf(
                            Triple(QuickFilter.ONLINE, "Online", p.online),
                            Triple(QuickFilter.UNKNOWN, "Unbekannt", p.unknown),
                            Triple(QuickFilter.BLOCKED, "Gesperrt", p.blocked),
                        ).forEach { (f, label, color) ->
                            val on = ui.filter == f
                            Column(
                                Modifier.weight(1f).clip(TileShape)
                                    .background(if (on) lerp(color, p.bg, if (p.dark) 0.8f else 0.88f) else p.card)
                                    .border(2.dp, if (on) color else Color.Transparent, TileShape)
                                    .clickable { ui.onFilter(if (on) QuickFilter.ALL else f) }
                                    .padding(12.dp),
                            ) {
                                Text("${ui.count(f)}", color = color, fontFamily = Mono, fontSize = 28.sp, fontWeight = FontWeight.SemiBold)
                                Spacer(Modifier.height(4.dp))
                                Text(label, color = p.text, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                  }
                }
                ui.error?.let { item { Text(it, color = p.danger, modifier = Modifier.padding(top = 12.dp)) } }
                ui.info?.let { item { Text(it, color = p.muted, modifier = Modifier.padding(top = 12.dp)) } }
                item {
                    Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        SectionLabel(if (ui.filter == QuickFilter.ALL) "Alle Geräte" else ui.filter.label, Modifier.weight(1f))
                        SortButton(ui.sort, ui.onSort, p.accentText)
                    }
                }
                if (ui.all == null && ui.error == null) item { Loading() }
                if (ui.all != null && ui.shown.isEmpty()) {
                    item { Text("Keine Geräte für diesen Filter.", color = p.muted, modifier = Modifier.padding(16.dp)) }
                }
                itemsIndexed(ui.shown, key = { _, d -> d.mac }) { i, d ->
                    val first = i == 0
                    val last = i == ui.shown.lastIndex
                    val shape = RoundedCornerShape(
                        topStart = if (first) 16.dp else 0.dp,
                        topEnd = if (first) 16.dp else 0.dp,
                        bottomStart = if (last) 16.dp else 0.dp,
                        bottomEnd = if (last) 16.dp else 0.dp,
                    )
                    Column(Modifier.clip(shape).background(p.card)) {
                        if (!first) HorizontalDivider(color = p.line)
                        TilesRow(d) { ui.onOpen(d.mac) }
                    }
                }
                item { Spacer(Modifier.height(16.dp)) }
            }
        }
    }
}

@Composable
private fun TilesRow(d: Device, onClick: () -> Unit) {
    val p = LocalPalette.current
    val color = p.statusColor(d.status)
    Row(
        Modifier.fillMaxWidth()
            .background(if (d.status == "unknown") p.unknownBg else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Gefüllter Punkt = online, Ring = offline; die Farbe zeigt den Status
        Box(
            Modifier.size(12.dp).clip(CircleShape)
                .background(if (d.online) color else Color.Transparent)
                .border(2.dp, color, CircleShape),
        )
        Spacer(Modifier.size(12.dp))
        Column(Modifier.weight(1f)) {
            Text(d.title, color = p.text, fontSize = 15.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                "${d.ip.ifBlank { "keine IP" }} · ${ifaceLabel(d.iface)}",
                color = p.muted,
                fontFamily = Mono,
                fontSize = 12.sp,
            )
        }
        if (d.status != "allowed") {
            Text(statusLabel(d.status), color = color, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.size(6.dp))
        }
        Icon(NavIcons.chevron, contentDescription = null, tint = p.muted, modifier = Modifier.size(20.dp))
    }
}

@Composable
private fun BigButton(label: String, background: Color, foreground: Color, enabled: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier.height(56.dp).clip(TileShape).background(background).clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(label, color = foreground, fontSize = 17.sp, fontWeight = FontWeight.ExtraBold) }
}

@Composable
fun TilesDetail(ui: DetailUi) {
    val p = LocalPalette.current
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Row(Modifier.height(56.dp), verticalAlignment = Alignment.CenterVertically) {
            IconTile(NavIcons.back, "Zurück zu allen Geräten", p.text, p.card, ui.onBack)
            Spacer(Modifier.size(10.dp))
            Text("Gerät", color = p.muted, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }
        val d = ui.device
        if (d == null) {
            if (ui.error == null) Loading() else Text(ui.error, color = p.danger)
            return@Column
        }
        val color = p.statusColor(d.status)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(p.statusBg(d.status))
                    .border(1.5.dp, lerp(color, p.bg, 0.55f), RoundedCornerShape(18.dp)).padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(statusLabel(d.status).uppercase(), color = color, fontSize = 12.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 1.5.sp)
                Text(d.title, color = p.text, fontSize = 26.sp, fontWeight = FontWeight.ExtraBold)
                Text("${if (d.online) "gerade online" else "offline"} · ${ifaceLabel(d.iface)}", color = p.muted, fontSize = 14.sp)
            }
            SelectionContainer {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Fact("IP-Adresse", d.ip.ifBlank { "–" }, mono = true, size = 18, copy = d.ip.isNotBlank(), onCopy = ui.onCopy)
                    Fact("MAC-Adresse", d.mac, mono = true, size = 16, copy = true, onCopy = ui.onCopy)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Fact("Verbindung", ifaceLabel(d.iface), Modifier.weight(1f))
                        Fact("Gerätename", d.hostname.ifBlank { "–" }, Modifier.weight(1f))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Fact("Zuerst", formatTime(d.firstSeen), Modifier.weight(1f))
                        Fact("Zuletzt", formatTime(d.lastSeen), Modifier.weight(1f))
                    }
                }
            }
            if (d.randomMac) {
                Text("Zufällige MAC-Adresse: Das kann auch ein bekanntes Handy mit neuer Adresse sein.", color = p.muted, fontSize = 13.sp)
            }
            ui.message?.let { Text(it, color = p.muted, fontSize = 14.sp) }
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            val allowed = d.status == "allowed"
            val blocked = d.status == "blocked"
            BigButton(
                "Sperren",
                background = if (blocked) p.card else p.danger,
                foreground = if (blocked) p.muted else Color.White,
                enabled = !ui.busy && !blocked,
                modifier = Modifier.weight(1f),
                onClick = ui.onBlock,
            )
            BigButton(
                "Erlauben",
                background = if (allowed) p.card else p.accent,
                foreground = if (allowed) p.muted else p.onAccent,
                enabled = !ui.busy && !allowed,
                modifier = Modifier.weight(1f),
                onClick = ui.onAllow,
            )
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun Fact(
    label: String,
    value: String,
    modifier: Modifier = Modifier.fillMaxWidth(),
    mono: Boolean = false,
    size: Int = 15,
    copy: Boolean = false,
    onCopy: (String, String) -> Unit = { _, _ -> },
) {
    val p = LocalPalette.current
    Column(modifier.clip(TileShape).background(p.card).padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label.uppercase(), color = p.muted, fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.8.sp, modifier = Modifier.weight(1f))
            if (copy) {
                Text(
                    "Kopieren",
                    color = p.accentText,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable { onCopy(label, value) }
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(value, color = p.text, fontFamily = if (mono) Mono else null, fontSize = size.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun <T> TilesSegmented(options: List<T>, selected: T, label: (T) -> String, onSelect: (T) -> Unit) {
    val p = LocalPalette.current
    Row(
        Modifier.fillMaxWidth().clip(TileShape).background(p.card).padding(5.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        options.forEach { option ->
            val on = option == selected
            Box(
                Modifier.weight(1f).height(42.dp).clip(RoundedCornerShape(10.dp))
                    .background(if (on) p.accent else Color.Transparent)
                    .clickable { onSelect(option) },
                contentAlignment = Alignment.Center,
            ) { Text(label(option), color = if (on) p.onAccent else p.text, fontSize = 14.sp, fontWeight = FontWeight.Bold) }
        }
    }
}

@Composable
fun TilesSettings(ui: SettingsUi) {
    val p = LocalPalette.current
    Column(
        Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            if (ui.configured) "Einstellungen" else "Einrichten",
            color = p.text,
            fontSize = 30.sp,
            fontWeight = FontWeight.ExtraBold,
            modifier = Modifier.padding(start = 4.dp, top = 6.dp, bottom = 6.dp),
        )
        SectionLabel("Design")
        TilesSegmented(AppStyle.entries, ui.style, { it.label }, ui.onStyle)
        SectionLabel("Darstellung")
        TilesSegmented(listOf(ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.SYSTEM), ui.themeMode, { it.label }, ui.onTheme)
        SectionLabel("Akzentfarbe")
        Row(
            Modifier.fillMaxWidth().clip(TileShape).background(p.card).padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            ui.style.accents.forEach { a ->
                val on = a == ui.accent
                Column(
                    Modifier.clip(RoundedCornerShape(12.dp)).clickable { ui.onAccent(a) }.padding(4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Box(
                        Modifier.size(52.dp).clip(RoundedCornerShape(14.dp)).background(a.color),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (on) Icon(NavIcons.check, contentDescription = "ausgewählt", tint = Color.White)
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(a.label, color = p.text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        Row(
            Modifier.fillMaxWidth().clip(TileShape).background(p.card).clickable(onClick = ui.onAccess).padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Zugang", color = p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text("Server, Konto und Kopplung", color = p.muted, fontSize = 13.sp)
            }
            Icon(NavIcons.chevron, contentDescription = null, tint = p.muted)
        }
        if (ui.configured && !ui.managedByHub) {
            SectionLabel("Entsperren mit")
            Column(Modifier.fillMaxWidth().clip(TileShape).background(p.card)) {
                listOf(false to "Passwort", true to "Fingerabdruck").forEachIndexed { i, (bio, label) ->
                    if (i > 0) HorizontalDivider(color = p.line)
                    val on = ui.useBiometrics == bio
                    Row(
                        Modifier.fillMaxWidth().height(54.dp).clickable { ui.onLockMode(bio) }.padding(horizontal = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(label, color = p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Box(
                            Modifier.size(22.dp).border(2.dp, if (on) p.accentText else p.muted, CircleShape).padding(5.dp)
                                .clip(CircleShape).background(if (on) p.accentText else Color.Transparent),
                        )
                    }
                }
            }
        }
        if (!ui.managedByHub) {
            SectionLabel("Verbindung")
            Column(Modifier.fillMaxWidth().clip(TileShape).background(p.card).padding(12.dp)) {
                OutlinedTextField(
                    value = ui.url,
                    onValueChange = ui.onUrl,
                    label = { Text("Adresse im Heimnetz") },
                    singleLine = true,
                    shape = RoundedCornerShape(10.dp),
                    colors = fieldColors(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = ui.externalUrl,
                    onValueChange = ui.onExternalUrl,
                    label = { Text("Adresse unterwegs (optional, https://…)") },
                    singleLine = true,
                    shape = RoundedCornerShape(10.dp),
                    colors = fieldColors(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = ui.password,
                    onValueChange = ui.onPassword,
                    label = { Text("Passwort der Weboberfläche") },
                    singleLine = true,
                    shape = RoundedCornerShape(10.dp),
                    colors = fieldColors(),
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                PrimaryButton(if (ui.busy) "Prüfe …" else "Speichern und testen", Modifier.fillMaxWidth(), enabled = !ui.busy, onClick = ui.onSave)
            }
        }
        ui.message?.let { Text(it, color = p.muted, fontSize = 14.sp, modifier = Modifier.padding(4.dp)) }
        SectionLabel("Über die App")
        Column(Modifier.fillMaxWidth().clip(TileShape).background(p.card).padding(12.dp)) {
            AboutContent()
        }
        Spacer(Modifier.height(8.dp))
    }
}
