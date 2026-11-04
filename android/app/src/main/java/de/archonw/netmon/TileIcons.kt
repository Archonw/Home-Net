package de.archonw.netmon

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Wie die Symbole der Module auf der Startseite aussehen; in den Einstellungen wählbar. */
enum class TileIconStyle(val label: String) {
    CLASSIC("Klassisch"),
    CALM("Ruhig"),
    TINTED("Getönt"),
    GRADIENT("Verlauf"),
}

val LocalTileIconStyle = staticCompositionLocalOf { TileIconStyle.CLASSIC }

/** Ein Modul: altes Bild, neues Strich-Symbol und seine Farben (hell, Verlauf oben, Verlauf unten). */
private class ModuleGlyph(@DrawableRes val classic: Int, val glyph: ImageVector, val light: Long, val top: Long, val bottom: Long)

private fun circle(cx: Float, cy: Float, r: Float) = "M${cx - r},${cy}a$r,$r 0 1,0 ${2 * r},0a$r,$r 0 1,0 ${-2 * r},0z"

private fun rect(x: Float, y: Float, w: Float, h: Float, r: Float) =
    "M${x + r},${y}h${w - 2 * r}a$r,$r 0 0,1 $r,${r}v${h - 2 * r}a$r,$r 0 0,1 ${-r},${r}h${-(w - 2 * r)}a$r,$r 0 0,1 ${-r},${-r}v${-(h - 2 * r)}a$r,$r 0 0,1 $r,${-r}z"

/** Strich-Symbol auf einem 24er-Raster, wie in der Vorschau. Die Farbe kommt beim Zeichnen über `tint`. */
private fun glyph(name: String, vararg paths: String): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        paths.forEach {
            addPath(
                pathData = addPathNodes(it),
                fill = null,
                stroke = SolidColor(Color.Black),
                strokeLineWidth = 1.8f,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
            )
        }
    }.build()

private val GLYPHS: Map<String, ModuleGlyph> by lazy {
    mapOf(
        "network" to ModuleGlyph(
            R.drawable.ic_tile_network,
            glyph("network", circle(12f, 5f, 2.3f), circle(5f, 18.5f, 2.3f), circle(19f, 18.5f, 2.3f),
                "M10.9,7.1L6.1,16.4M13.1,7.1l4.8,9.3M7.3,18.5h9.4"),
            0xFF34D399, 0xFF10B981, 0xFF047857,
        ),
        "pihole" to ModuleGlyph(
            R.drawable.ic_tile_pihole,
            glyph("pihole", "M4,5h16l-6.2,7.4v5.8L10.2,20v-7.6z"),
            0xFFF87171, 0xFFEF4444, 0xFFB91C1C,
        ),
        "unraid" to ModuleGlyph(
            R.drawable.ic_tile_unraid,
            glyph("unraid", rect(4f, 4f, 16f, 7f, 2f), rect(4f, 13f, 16f, 7f, 2f),
                "M8,7.5h0.01M8,16.5h0.01M12,7.5h4M12,16.5h4"),
            0xFFFB923C, 0xFFF97316, 0xFFC2410C,
        ),
        "pocketid" to ModuleGlyph(
            R.drawable.ic_tile_pocketid,
            glyph("pocketid", circle(8f, 15f, 4f), "M10.9,12.1l8.1,-8.1M16,7l2.2,2.2M13.8,9.2l1.8,1.8"),
            0xFFA78BFA, 0xFF8B5CF6, 0xFF6D28D9,
        ),
        "pc" to ModuleGlyph(
            R.drawable.ic_tile_pc,
            glyph("pc", rect(3f, 4f, 18f, 12f, 2f), "M8.5,20h7M12,16v4M12,7v5.2M9.6,9.8L12,12.2l2.4,-2.4"),
            0xFF60A5FA, 0xFF3B82F6, 0xFF1D4ED8,
        ),
        "pi" to ModuleGlyph(
            R.drawable.ic_tile_pi,
            glyph("pi", rect(7f, 7f, 10f, 10f, 1.6f), rect(10f, 10f, 4f, 4f, 0.6f),
                "M9.5,3.5V7M14.5,3.5V7M9.5,17v3.5M14.5,17v3.5M3.5,9.5H7M3.5,14.5H7M17,9.5h3.5M17,14.5h3.5"),
            0xFFF472B6, 0xFFEC4899, 0xFFBE185D,
        ),
        "wireguard" to ModuleGlyph(
            R.drawable.ic_tile_wireguard,
            glyph("wireguard", "M12,3L19,6v5c0,4.6 -3,8.2 -7,10c-4,-1.8 -7,-5.4 -7,-10V6z",
                rect(9.3f, 11f, 5.4f, 4.6f, 1f), "M10.6,11V9.8a1.4,1.4 0 0,1 2.8,0V11"),
            0xFF22D3EE, 0xFF06B6D4, 0xFF0E7490,
        ),
        "proxy" to ModuleGlyph(
            R.drawable.ic_tile_proxy,
            glyph("proxy", circle(12f, 12f, 9f),
                "M3,12h18M12,3c2.4,2.5 3.7,5.6 3.7,9s-1.3,6.5 -3.7,9c-2.4,-2.5 -3.7,-5.6 -3.7,-9S9.6,5.5 12,3z"),
            0xFFFACC15, 0xFFEAB308, 0xFFA16207,
        ),
    )
}

/** Symbol eines Moduls (Schlüssel wie in [HOME_TILES]) im gewählten Stil. */
@Composable
fun ModuleIcon(key: String, size: Dp = 48.dp, style: TileIconStyle = LocalTileIconStyle.current) {
    val m = GLYPHS[key] ?: return
    val shape = RoundedCornerShape(size * 0.29f)
    if (style == TileIconStyle.CLASSIC) {
        Image(painterResource(m.classic), contentDescription = null, modifier = Modifier.size(size).clip(shape))
        return
    }
    val p = LocalPalette.current
    val light = Color(m.light)
    val (background, tint) = when (style) {
        TileIconStyle.CALM -> Brush.linearGradient(listOf(p.accentSoft, p.accentSoft)) to p.accentText
        TileIconStyle.TINTED -> {
            val color = if (p.dark) light else Color(m.bottom)
            Brush.linearGradient(listOf(color.copy(alpha = 0.15f), color.copy(alpha = 0.15f))) to color
        }
        else -> Brush.linearGradient(listOf(Color(m.top), Color(m.bottom))) to Color.White
    }
    var box = Modifier.size(size).clip(shape).background(background)
    if (style == TileIconStyle.TINTED) {
        val edge = (if (p.dark) light else Color(m.bottom)).copy(alpha = 0.22f)
        box = box.border(1.dp, edge, shape)
    }
    Box(box, contentAlignment = Alignment.Center) {
        Icon(m.glyph, contentDescription = null, tint = tint, modifier = Modifier.size(size * 0.54f))
    }
}

/** Auswahl des Symbolstils für die Einstellungen: je Stil ein Beispiel-Symbol mit Namen. */
@Composable
fun TileIconPicker(selected: TileIconStyle, onSelect: (TileIconStyle) -> Unit) {
    val p = LocalPalette.current
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        TileIconStyle.entries.forEach { style ->
            val on = style == selected
            Column(
                Modifier.clip(RoundedCornerShape(12.dp)).clickable { onSelect(style) }
                    .border(2.dp, if (on) p.accent else Color.Transparent, RoundedCornerShape(12.dp))
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                    ModuleIcon("network", 26.dp, style)
                    ModuleIcon("pihole", 26.dp, style)
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    style.label, color = if (on) p.text else p.muted, fontSize = 12.sp,
                    fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                )
            }
        }
    }
}
