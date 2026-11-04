package de.archonw.netmon

import android.content.Context
import androidx.compose.material3.LocalTextStyle
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import org.json.JSONObject
import java.util.Locale
import androidx.compose.material3.Text as MaterialText

/**
 * Übersetzung der Oberfläche. Deutsch ist die Grundlage: Die Wörterbücher unter `i18n/en/` im Repo ordnen jedem
 * deutschen Text den englischen zu, Platzhalter `{0}`, `{1}` stehen für wechselnde Teile ("Sicherung von {0} läuft").
 * Ist das Handy nicht auf Deutsch gestellt, zeigt die App Englisch. Übersetzt wird beim Anzeigen, darum gelten die
 * Wörterbücher auch für Texte, die vom Server kommen (Fehlermeldungen, Benachrichtigungen).
 */
object I18n {
    private class Template(val regex: Regex, val target: String)

    @Volatile private var exact: Map<String, String> = emptyMap()
    @Volatile private var templates: List<Template> = emptyList()
    @Volatile var active = false
        private set
    private val cache = object : LinkedHashMap<String, String>(256, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?) = size > 2000
    }
    private var loaded = false

    @Synchronized
    fun init(context: Context) {
        if (loaded) return
        loaded = true
        active = Locale.getDefault().language != "de"
        if (!active) return
        val words = HashMap<String, String>()
        val assets = context.assets
        for (name in assets.list("i18n/en").orEmpty().filter { it.endsWith(".json") }.sorted()) {
            runCatching {
                val json = JSONObject(assets.open("i18n/en/$name").bufferedReader().use { it.readText() })
                for (key in json.keys()) words[key] = json.getString(key)
            }
        }
        load(words)
    }

    /** Für Tests und für den Aufbau aus einer Karte. */
    fun load(words: Map<String, String>, on: Boolean = true) {
        active = on
        exact = words.filterKeys { !it.contains("{0}") }
        templates = words.filterKeys { it.contains("{0}") }
            .entries.sortedByDescending { it.key.length }
            .map { (source, target) -> Template(pattern(source), target) }
        synchronized(cache) { cache.clear() }
    }

    private fun pattern(source: String): Regex {
        val out = StringBuilder("^")
        var rest = source
        val placeholder = Regex("\\{(\\d+)\\}")
        val seen = HashSet<String>()
        while (true) {
            val m = placeholder.find(rest) ?: break
            out.append(Regex.escape(rest.substring(0, m.range.first)))
            val n = m.groupValues[1]
            out.append(if (seen.add(n)) "(?<p$n>.+?)" else "\\k<p$n>")
            rest = rest.substring(m.range.last + 1)
        }
        out.append(Regex.escape(rest)).append("$")
        return Regex(out.toString(), RegexOption.DOT_MATCHES_ALL)
    }

    fun translate(text: String): String {
        if (!active || text.isBlank()) return text
        synchronized(cache) { cache[text]?.let { return it } }
        val result = lookup(text, 0)
        synchronized(cache) { cache[text] = result }
        return result
    }

    private fun lookup(text: String, depth: Int): String {
        exact[text]?.let { return it }
        val trimmed = text.trim()
        if (trimmed != text && trimmed.isNotEmpty()) {
            exact[trimmed]?.let { return text.replace(trimmed, it) }
        }
        if (depth > 1) return text
        for (template in templates) {
            val m = template.regex.matchEntire(text) ?: continue
            var out = template.target
            for (i in 0..9) {
                val group = runCatching { m.groups["p$i"]?.value }.getOrNull() ?: continue
                out = out.replace("{$i}", lookup(group, depth + 1))
            }
            return out
        }
        // Mehrzeilige Meldungen (z. B. "Name: …\nVerbindung: …") Zeile für Zeile
        if ('\n' in text) return text.split('\n').joinToString("\n") { lookup(it, depth) }
        return text
    }
}

/** Ein Text der Oberfläche in der Sprache des Handys; `{0}` usw. werden durch [args] ersetzt. */
fun t(text: String, vararg args: Any?): String {
    var out = I18n.translate(text)
    args.forEachIndexed { i, a -> out = out.replace("{$i}", a.toString()) }
    return out
}

/** Wie Text aus Material 3, aber übersetzt. Ersetzt in diesem Paket den Import von androidx.compose.material3.Text. */
@Composable
fun Text(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textDecoration: TextDecoration? = null,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: (TextLayoutResult) -> Unit = {},
    style: TextStyle = LocalTextStyle.current,
) = MaterialText(
    text = I18n.translate(text), modifier = modifier, color = color, fontSize = fontSize, fontStyle = fontStyle,
    fontWeight = fontWeight, fontFamily = fontFamily, letterSpacing = letterSpacing,
    textDecoration = textDecoration, textAlign = textAlign, lineHeight = lineHeight, overflow = overflow,
    softWrap = softWrap, maxLines = maxLines, minLines = minLines, onTextLayout = onTextLayout, style = style,
)

@Composable
fun Text(
    text: AnnotatedString,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textDecoration: TextDecoration? = null,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: (TextLayoutResult) -> Unit = {},
    style: TextStyle = LocalTextStyle.current,
) = MaterialText(
    text = text, modifier = modifier, color = color, fontSize = fontSize, fontStyle = fontStyle,
    fontWeight = fontWeight, fontFamily = fontFamily, letterSpacing = letterSpacing,
    textDecoration = textDecoration, textAlign = textAlign, lineHeight = lineHeight, overflow = overflow,
    softWrap = softWrap, maxLines = maxLines, minLines = minLines, onTextLayout = onTextLayout, style = style,
)
