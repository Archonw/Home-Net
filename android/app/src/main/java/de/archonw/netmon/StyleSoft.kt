package de.archonw.netmon

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.lazy.items
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
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Entwurf A „Soft“: weiche Kopfkarte mit Verlauf, abgerundete Karten, Filter-Pillen.

private val CardShape = RoundedCornerShape(16.dp)

@Composable
private fun SoftCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val p = LocalPalette.current
    Column(modifier.fillMaxWidth().clip(CardShape).background(p.card).padding(16.dp)) { content() }
}

@Composable
private fun Hero(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val p = LocalPalette.current
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp))
            .background(Brush.linearGradient(listOf(p.heroStart, p.heroEnd)))
            .padding(horizontal = 20.dp, vertical = 18.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

@Composable
private fun StatusPill(status: String) {
    val p = LocalPalette.current
    Text(
        statusLabel(status),
        color = p.statusColor(status),
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(p.statusBg(status))
            .padding(horizontal = 9.dp, vertical = 4.dp),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SoftList(ui: ListUi) {
    val p = LocalPalette.current
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 20.dp)) {
        Row(Modifier.fillMaxWidth().height(56.dp), verticalAlignment = Alignment.CenterVertically) {
            if (ui.searching) {
                Box(Modifier.weight(1f)) { SearchField(ui, RoundedCornerShape(14.dp)) }
                Spacer(Modifier.size(8.dp))
                IconTile(NavIcons.close, "Suche schließen", p.text, Color.Transparent, ui.onToggleSearch)
            } else {
                IconTile(NavIcons.search, "Suchen", p.text, Color.Transparent, ui.onToggleSearch)
                Text(
                    buildAnnotatedString {
                        append("Netz")
                        withStyle(SpanStyle(color = p.accentText, fontWeight = FontWeight.Bold)) { append("werk") }
                    },
                    color = p.text,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.weight(1f),
                )
                MoreMenu(ui, p.text, Color.Transparent)
            }
        }
        PullToRefreshBox(isRefreshing = ui.refreshing, onRefresh = ui.onRefresh, modifier = Modifier.weight(1f)) {
            LazyColumn(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                item {
                    Hero(Modifier.padding(top = 4.dp)) {
                        Text("Dein Netzwerk", color = p.heroText, fontSize = 16.sp)
                        Row(horizontalArrangement = Arrangement.spacedBy(26.dp)) {
                            HeroStat(ui.count(QuickFilter.ONLINE), "online von ${ui.all.orEmpty().size}")
                            HeroStat(ui.count(QuickFilter.UNKNOWN), "unbekannt")
                            HeroStat(ui.count(QuickFilter.BLOCKED), "gesperrt")
                        }
                    }
                }
                ui.error?.let { item { Text(it, color = p.danger, modifier = Modifier.padding(vertical = 4.dp)) } }
                ui.info?.let { item { Text(it, color = p.muted, modifier = Modifier.padding(vertical = 4.dp)) } }
                item {
                    Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("Geräte", color = p.text, fontSize = 19.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        SortButton(ui.sort, ui.onSort, p.muted)
                    }
                }
                item {
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        QuickFilter.entries.forEach { f ->
                            val on = f == ui.filter
                            Text(
                                f.label,
                                color = if (on) (if (p.dark) p.bg else Color.White) else p.text,
                                fontSize = 13.sp,
                                modifier = Modifier.clip(RoundedCornerShape(50))
                                    .background(if (on) p.accentText else Color.Transparent)
                                    .border(1.dp, if (on) p.accentText else p.line, RoundedCornerShape(50))
                                    .clickable { ui.onFilter(f) }
                                    .padding(horizontal = 14.dp, vertical = 8.dp),
                            )
                        }
                    }
                }
                if (ui.all == null && ui.error == null) item { Loading() }
                if (ui.all != null && ui.shown.isEmpty()) {
                    item { Text("Keine Geräte für diesen Filter.", color = p.muted, modifier = Modifier.padding(vertical = 16.dp)) }
                }
                items(ui.shown, key = { it.mac }) { d -> SoftRow(d) { ui.onOpen(d.mac) } }
                item { Spacer(Modifier.height(12.dp)) }
            }
        }
    }
}

@Composable
private fun HeroStat(value: Int, label: String) {
    val p = LocalPalette.current
    Column {
        Text("$value", color = p.heroText, fontSize = 32.sp, fontWeight = FontWeight.Bold)
        Text(label, color = p.heroText.copy(alpha = 0.85f), fontSize = 13.sp)
    }
}

@Composable
private fun SoftRow(d: Device, onClick: () -> Unit) {
    val p = LocalPalette.current
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(p.card).clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(40.dp).clip(CircleShape).background(p.accentSoft),
            contentAlignment = Alignment.Center,
        ) {
            Text(d.title.take(1).uppercase(), color = p.accentText, fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }
        Spacer(Modifier.size(12.dp))
        Column(Modifier.weight(1f)) {
            Text(d.title, color = p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                "${d.ip.ifBlank { "keine IP" }} · ${if (d.online) "online" else "offline"}",
                color = p.muted,
                fontSize = 12.5.sp,
            )
        }
        Spacer(Modifier.size(8.dp))
        StatusPill(d.status)
    }
}

@Composable
private fun ActionButton(
    label: String,
    background: Color,
    foreground: Color,
    border: Color,
    enabled: Boolean,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    Box(
        modifier.height(56.dp).clip(CardShape).background(background).border(1.5.dp, border, CardShape)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(label, color = foreground, fontSize = 17.sp, fontWeight = FontWeight.SemiBold) }
}

@Composable
fun SoftDetail(ui: DetailUi) {
    val p = LocalPalette.current
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 20.dp, vertical = 8.dp)) {
        TextButton(onClick = ui.onBack) {
            Icon(NavIcons.back, contentDescription = null, tint = p.accentText)
            Spacer(Modifier.size(4.dp))
            Text("Alle Geräte", color = p.accentText, fontSize = 15.sp)
        }
        val d = ui.device
        if (d == null) {
            if (ui.error == null) Loading() else Text(ui.error, color = p.danger)
            return@Column
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Hero(Modifier.padding(top = 4.dp)) {
                StatusPill(d.status)
                Text(d.title, color = p.heroText, fontSize = 25.sp, fontWeight = FontWeight.Bold)
                Text(
                    "${if (d.online) "gerade online" else "offline"} · ${ifaceLabel(d.iface)}",
                    color = p.heroText.copy(alpha = 0.85f),
                    fontSize = 14.sp,
                )
            }
            SoftCard(Modifier.padding(0.dp)) {
                SelectionContainer {
                    Column {
                        listOf(
                            Triple("Gerätename", d.hostname.ifBlank { "–" }, false),
                            Triple("IP-Adresse", d.ip.ifBlank { "–" }, d.ip.isNotBlank()),
                            Triple("MAC-Adresse", d.mac, true),
                            Triple("Verbindung", ifaceLabel(d.iface), false),
                            Triple("Zuerst gesehen", formatTime(d.firstSeen), false),
                            Triple("Zuletzt gesehen", formatTime(d.lastSeen), false),
                        ).forEachIndexed { i, (k, v, copyable) ->
                            if (i > 0) HorizontalDivider(color = p.line)
                            Row(Modifier.fillMaxWidth().height(48.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(k, color = p.muted, fontSize = 14.sp, modifier = Modifier.weight(1f))
                                Text(v, color = p.text, fontSize = 14.5.sp)
                                if (copyable) {
                                    Text(
                                        "Kopieren",
                                        color = p.accentText,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.padding(start = 10.dp).clip(RoundedCornerShape(8.dp))
                                            .clickable { ui.onCopy(k, v) }.padding(horizontal = 6.dp, vertical = 8.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            }
            if (d.randomMac) {
                Text("Zufällige (private) MAC-Adresse: Das kann auch ein bekanntes Handy mit neuer Adresse sein.", color = p.unknown, fontSize = 13.sp)
            }
            ui.message?.let { Text(it, color = p.muted, fontSize = 14.sp) }
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            val allowed = d.status == "allowed"
            val blocked = d.status == "blocked"
            ActionButton(
                "Erlauben",
                background = if (allowed) p.line else p.accent,
                foreground = if (allowed) p.muted else p.onAccent,
                border = Color.Transparent,
                enabled = !ui.busy && !allowed,
                modifier = Modifier.weight(1f),
                onClick = ui.onAllow,
            )
            ActionButton(
                "Sperren",
                background = if (blocked) p.danger else Color.Transparent,
                foreground = if (blocked) (if (p.dark) p.bg else Color.White) else p.danger,
                border = p.danger,
                enabled = !ui.busy && !blocked,
                modifier = Modifier.weight(1f),
                onClick = ui.onBlock,
            )
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun <T> SoftSegmented(options: List<T>, selected: T, label: (T) -> String, onSelect: (T) -> Unit) {
    val p = LocalPalette.current
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(p.bg).padding(4.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        options.forEach { option ->
            val on = option == selected
            Box(
                Modifier.weight(1f).height(40.dp).clip(RoundedCornerShape(9.dp))
                    .background(if (on) p.card else Color.Transparent)
                    .border(1.dp, if (on) p.line else Color.Transparent, RoundedCornerShape(9.dp))
                    .clickable { onSelect(option) },
                contentAlignment = Alignment.Center,
            ) { Text(label(option), color = if (on) p.text else p.muted, fontSize = 14.sp, fontWeight = FontWeight.Medium) }
        }
    }
}

@Composable
private fun CardTitle(text: String) {
    Text(text, color = LocalPalette.current.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(12.dp))
}

@Composable
fun SoftSettings(ui: SettingsUi) {
    val p = LocalPalette.current
    Column(
        Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            if (ui.configured) "Einstellungen" else "Verbindung einrichten",
            color = p.text,
            fontSize = 20.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
        )
        SoftCard {
            CardTitle("Design")
            SoftSegmented(AppStyle.entries, ui.style, { it.label }, ui.onStyle)
        }
        SoftCard {
            CardTitle("Darstellung")
            SoftSegmented(listOf(ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.SYSTEM), ui.themeMode, { it.label }, ui.onTheme)
        }
        SoftCard {
            CardTitle("Akzentfarbe")
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                ui.style.accents.forEach { a ->
                    val on = a == ui.accent
                    Column(
                        Modifier.clip(RoundedCornerShape(12.dp)).clickable { ui.onAccent(a) }.padding(4.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Box(
                            Modifier.size(52.dp).border(2.5.dp, if (on) a.color else Color.Transparent, CircleShape).padding(5.dp)
                                .clip(CircleShape).background(a.color),
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(a.label, color = if (on) p.text else p.muted, fontSize = 12.sp)
                    }
                }
            }
        }
        if (ui.configured) {
            SoftCard {
                CardTitle("Entsperren mit")
                SoftSegmented(listOf(false, true), ui.useBiometrics, { if (it) "Fingerabdruck" else "Passwort" }, ui.onLockMode)
            }
        }
        SoftCard {
            CardTitle("Verbindung")
            OutlinedTextField(
                value = ui.url,
                onValueChange = ui.onUrl,
                label = { Text("Adresse im Heimnetz") },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
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
                shape = RoundedCornerShape(12.dp),
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
                shape = RoundedCornerShape(12.dp),
                colors = fieldColors(),
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            PrimaryButton(if (ui.busy) "Prüfe …" else "Speichern und testen", Modifier.fillMaxWidth(), enabled = !ui.busy, onClick = ui.onSave)
        }
        ui.message?.let { Text(it, color = p.muted, fontSize = 14.sp) }
        Spacer(Modifier.height(8.dp))
    }
}
