package de.archonw.netmon

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.text.NumberFormat
import java.util.Locale

private const val PA_REFRESH_MS = 60_000L

fun euro(value: Double): String = NumberFormat.getCurrencyInstance(Locale.GERMANY).format(value)

private fun plain(value: Double): String =
    NumberFormat.getNumberInstance(Locale.GERMANY).apply { maximumFractionDigits = 2; isGroupingUsed = false }.format(value)

fun preisalarmTileText(o: PaOverview): String {
    val good = o.products.count { it.good.isNotEmpty() }
    return when {
        o.products.isEmpty() -> "Noch kein Produkt eingetragen"
        good > 0 -> t("{0} von {1} gerade günstig", good, o.products.size)
        o.products.size == 1 -> "1 Produkt im Blick"
        else -> t("{0} Produkte im Blick", o.products.size)
    }
}

fun preisalarmTileAlert(o: PaOverview): Boolean = o.products.any { it.good.isNotEmpty() }

/** Gründe des Dienstes („Zielpreis erreicht“) für die Anzeige: erster Buchstabe groß, Übersetzung je Teil. */
fun goodText(good: List<String>): String =
    good.joinToString(", ") { t(it) }.replaceFirstChar { it.uppercase() }

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PreisalarmScreen(api: PreisalarmApi, onBack: () -> Unit) {
    val p = LocalPalette.current
    val scope = rememberCoroutineScope()
    val uriHandler = LocalUriHandler.current
    var reload by remember { mutableStateOf(0) }
    var checking by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var fresh by remember { mutableStateOf<PaOverview?>(null) }
    var editing by remember { mutableStateOf<PaProduct?>(null) }
    var adding by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<PaProduct?>(null) }
    val (loaded, error) = rememberLoaded(reload, PA_REFRESH_MS) { api.overview().also { fresh = null } }
    val data = fresh ?: loaded

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Preisalarm", onBack = onBack, trailing = {
            if (data?.manager == true) TextAction("Hinzufügen") { adding = true }
        })
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            error?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            when {
                data == null -> if (error == null) Text("Wird geladen …", color = p.muted)
                data.products.isEmpty() -> HubCard {
                    Text("Noch kein Produkt eingetragen.", color = p.text, fontSize = 15.sp)
                    Text(
                        if (data.manager) "Mit „Hinzufügen“ oben rechts ein Produkt eintragen, am besten mit seiner Seite bei geizhals.de."
                        else "Produkte eintragen können Eigentümer und Admins von Home-Net.",
                        color = p.muted, fontSize = 13.sp,
                    )
                }
                else -> {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            t("Zuletzt geprüft: {0}", data.lastRun?.let { formatEpoch(it) } ?: "–"),
                            color = p.muted, fontSize = 13.sp, modifier = Modifier.weight(1f),
                        )
                        if (data.manager) {
                            Pill(if (checking) "Wird geprüft …" else "Jetzt prüfen") {
                                if (checking) return@Pill
                                checking = true
                                message = null
                                scope.launch {
                                    try {
                                        fresh = api.check()
                                    } catch (e: ApiException) {
                                        message = e.message
                                    } finally {
                                        checking = false
                                    }
                                }
                            }
                        }
                    }
                    data.products.forEach { item ->
                        HubCard(onClick = if (data.manager) ({ editing = item }) else null) {
                            Row(verticalAlignment = Alignment.Top) {
                                Text(item.name, color = p.text, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f))
                                if (item.url.isNotEmpty()) {
                                    Text(
                                        item.price?.let { euro(it) } ?: "–", color = p.text, fontWeight = FontWeight.Bold, fontSize = 20.sp,
                                        modifier = Modifier.padding(start = 10.dp),
                                    )
                                }
                            }
                            if (item.good.isNotEmpty()) {
                                Text(goodText(item.good), color = p.allowed, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            }
                            val facts = listOfNotNull(
                                item.target?.let { t("Ziel {0}", euro(it)) },
                                item.average?.let { "Ø ${euro(it)}" },
                                item.lowest?.let { t("Tiefst {0}", euro(it)) },
                                if (item.count > 0) t("{0} Messungen", item.count) else null,
                            )
                            if (facts.isNotEmpty()) Text(facts.joinToString(" · "), color = p.muted, fontSize = 13.sp)
                            if (item.url.isEmpty()) Text("Nur Deal-Suche", color = p.muted, fontSize = 13.sp)
                            PriceLine(item.history, item.target)
                            if (item.error.isNotEmpty()) Text(item.error, color = p.blocked, fontSize = 13.sp)
                            if (item.terms.isNotEmpty() || item.url.isNotEmpty()) {
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                    item.terms.forEach { Chip(it) }
                                    if (item.url.isNotEmpty()) {
                                        Pill(item.url.substringAfter("://").substringBefore('/').removePrefix("www."), small = true) {
                                            runCatching { uriHandler.openUri(item.url) }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (!data.manager) Text("Produkte ändern können Eigentümer und Admins von Home-Net.", color = p.muted, fontSize = 12.sp)
                }
            }
        }
    }

    if (adding || editing != null) {
        val item = editing
        var name by remember(item) { mutableStateOf(item?.name.orEmpty()) }
        var url by remember(item) { mutableStateOf(item?.url.orEmpty()) }
        var target by remember(item) { mutableStateOf(item?.target?.let { plain(it) }.orEmpty()) }
        var below by remember(item) { mutableStateOf((item?.belowPct ?: 15).toString()) }
        var terms by remember(item) { mutableStateOf(item?.terms?.joinToString("\n").orEmpty()) }
        val close = { adding = false; editing = null }
        FormDialog(
            title = if (item == null) "Produkt hinzufügen" else "Produkt bearbeiten",
            confirm = "Speichern",
            onDismiss = close,
            onConfirm = { api.save(item?.id, name, url, target, below, terms) },
            onDone = { close(); reload++ },
        ) {
            val muted = LocalPalette.current.muted
            OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                url, { url = it }, label = { Text("Produktseite") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth(),
            )
            Text("Am besten die Seite des Produkts bei geizhals.de, dort steht der günstigste Preis aller Shops. Eine Shopseite geht auch. idealo lässt keine automatische Abfrage zu.", color = muted, fontSize = 12.sp)
            OutlinedTextField(
                target, { target = it }, label = { Text("Zielpreis in €") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth(),
            )
            Text("Leer lassen für nur Durchschnitt und Tiefstpreis", color = muted, fontSize = 12.sp)
            OutlinedTextField(
                below, { below = it.filter(Char::isDigit).take(2) }, label = { Text("Alarm ab % unter Durchschnitt") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth(),
            )
            Text("0 schaltet das aus", color = muted, fontSize = 12.sp)
            OutlinedTextField(terms, { terms = it }, label = { Text("Suchbegriffe für Deals") }, minLines = 2, modifier = Modifier.fillMaxWidth())
            Text("Einer je Zeile, z. B. „Einhell 4,0 Ah“. Ein Deal passt, wenn alle Wörter eines Begriffs im Titel stehen.", color = muted, fontSize = 12.sp)
            if (item != null) {
                Pill("Entfernen", danger = true) { removing = item; close() }
            }
        }
    }

    removing?.let { item ->
        FormDialog(
            title = "Produkt und seinen Preisverlauf entfernen?", confirm = "Entfernen",
            onDismiss = { removing = null },
            onConfirm = { api.remove(item.id) },
            onDone = { removing = null; reload++ },
        ) {
            Text(item.name, color = LocalPalette.current.text, fontSize = 14.sp)
        }
    }
}

/** Preisverlauf als Linie, der Zielpreis gestrichelt. */
@Composable
private fun PriceLine(history: List<Pair<Long, Double>>, target: Double?) {
    if (history.size < 2) return
    val p = LocalPalette.current
    val prices = history.map { it.second } + listOfNotNull(target)
    val lo = prices.min()
    val span = (prices.max() - lo).takeIf { it > 0 } ?: 1.0
    Canvas(Modifier.fillMaxWidth().height(48.dp).padding(vertical = 4.dp)) {
        fun y(v: Double) = (size.height - (v - lo) / span * size.height).toFloat()
        val step = size.width / (history.size - 1)
        val path = Path()
        history.forEachIndexed { n, (_, v) -> if (n == 0) path.moveTo(0f, y(v)) else path.lineTo(n * step, y(v)) }
        drawPath(path, p.accent, style = Stroke(width = 2.dp.toPx()))
        target?.let {
            drawLine(
                p.muted, Offset(0f, y(it)), Offset(size.width, y(it)), strokeWidth = 1.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
            )
        }
    }
}

@Composable
private fun Chip(label: String) {
    val p = LocalPalette.current
    Text(
        label, color = p.accentText, fontSize = 12.sp,
        modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(p.accentSoft).padding(horizontal = 10.dp, vertical = 3.dp),
    )
}

@Composable
private fun Pill(label: String, small: Boolean = false, danger: Boolean = false, onClick: () -> Unit) {
    val p = LocalPalette.current
    Text(
        label, color = if (danger) p.danger else p.accentText, fontWeight = FontWeight.SemiBold, fontSize = if (small) 12.sp else 14.sp,
        modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(p.accentSoft).clickable(onClick = onClick)
            .padding(horizontal = if (small) 10.dp else 14.dp, vertical = if (small) 3.dp else 7.dp),
    )
}
