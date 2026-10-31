package de.archonw.netmon

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.font.FontFamily

/** Die beiden Designs der App, wählbar in den Einstellungen. */
enum class AppStyle(val label: String) {
    SOFT("Soft"),
    TILES("Kacheln"),
}

enum class Accent(val label: String, val color: Color) {
    INDIGO("Indigo", Color(0xFF4F5BD5)),
    PETROL("Petrol", Color(0xFF0B7A6C)),
    BEERE("Beere", Color(0xFFC2366B)),
    BERNSTEIN("Bernstein", Color(0xFFB35A0A)),
    GRUEN("Grün", Color(0xFF1A7F37)),
    BLAU("Blau", Color(0xFF2F5FD0)),
    VIOLETT("Violett", Color(0xFF6D4AE0)),
    ORANGE("Orange", Color(0xFFC25E0A)),
}

val AppStyle.accents: List<Accent>
    get() = when (this) {
        AppStyle.SOFT -> listOf(Accent.INDIGO, Accent.PETROL, Accent.BEERE, Accent.BERNSTEIN)
        AppStyle.TILES -> listOf(Accent.GRUEN, Accent.BLAU, Accent.VIOLETT, Accent.ORANGE)
    }

/** Alle Farben eines Designs in einem Modus (hell/dunkel) mit einer Akzentfarbe. */
data class Palette(
    val style: AppStyle,
    val dark: Boolean,
    val bg: Color,
    val card: Color,
    val text: Color,
    val muted: Color,
    val line: Color,
    val accent: Color,
    val accentText: Color,
    val accentSoft: Color,
    val onAccent: Color,
    val heroStart: Color,
    val heroEnd: Color,
    val heroText: Color,
    val allowed: Color,
    val unknown: Color,
    val blocked: Color,
    val allowedBg: Color,
    val unknownBg: Color,
    val blockedBg: Color,
    val danger: Color,
) {
    fun statusColor(status: String) = when (status) {
        "allowed" -> allowed
        "blocked" -> blocked
        else -> unknown
    }

    fun statusBg(status: String) = when (status) {
        "allowed" -> allowedBg
        "blocked" -> blockedBg
        else -> unknownBg
    }

    /** Farbe der Zahl/Markierung „online“ in Entwurf B. */
    val online: Color get() = accentText

    fun colorScheme() = if (dark) {
        darkColorScheme(
            primary = accent, onPrimary = onAccent, background = bg, onBackground = text,
            surface = card, onSurface = text, surfaceVariant = card, onSurfaceVariant = muted,
            surfaceContainer = card, surfaceContainerHigh = card, outline = line, error = danger,
        )
    } else {
        lightColorScheme(
            primary = accent, onPrimary = onAccent, background = bg, onBackground = text,
            surface = card, onSurface = text, surfaceVariant = card, onSurfaceVariant = muted,
            surfaceContainer = card, surfaceContainerHigh = card, outline = line, error = danger,
        )
    }
}

val Mono = FontFamily.Monospace

private fun mix(a: Color, b: Color, toB: Float) = lerp(a, b, toB)

fun palette(style: AppStyle, dark: Boolean, accent: Accent): Palette {
    val a = accent.color
    return when (style) {
        AppStyle.SOFT -> if (dark) {
            val bg = Color(0xFF0E1120)
            Palette(
                style, true, bg = bg, card = Color(0xFF191D31), text = Color(0xFFE9EBF7),
                muted = Color(0xFFA3A8C6), line = Color(0xFF2A2F48),
                accent = a, accentText = mix(a, Color.White, 0.45f), accentSoft = mix(a, bg, 0.7f),
                onAccent = Color.White,
                heroStart = mix(Color(0xFFC2366B), bg, 0.55f), heroEnd = mix(a, bg, 0.45f), heroText = Color.White,
                allowed = Color(0xFF7EDCA4), unknown = Color(0xFFF5B05A), blocked = Color(0xFFFF8A80),
                allowedBg = Color(0xFF12301F), unknownBg = Color(0xFF3A2610), blockedBg = Color(0xFF3A1715),
                danger = Color(0xFFFF8A80),
            )
        } else {
            Palette(
                style, false, bg = Color(0xFFF4F5FA), card = Color.White, text = Color(0xFF1B1F4B),
                muted = Color(0xFF5F6485), line = Color(0xFFE6E8F2),
                accent = a, accentText = a, accentSoft = mix(a, Color.White, 0.88f), onAccent = Color.White,
                heroStart = Color(0xFFF9D3DF), heroEnd = mix(a, Color.White, 0.55f), heroText = Color(0xFF1B1F4B),
                allowed = Color(0xFF146C3A), unknown = Color(0xFF8A4B00), blocked = Color(0xFFB42318),
                allowedBg = Color(0xFFE3F4EA), unknownBg = Color(0xFFFDEFD9), blockedBg = Color(0xFFFDE4E1),
                danger = Color(0xFFC62828),
            )
        }
        AppStyle.TILES -> if (dark) {
            val bg = Color(0xFF0B0C0E)
            val unknown = Color(0xFFF5A524)
            val blocked = Color(0xFFFF6B60)
            val accentText = mix(a, Color.White, 0.4f)
            Palette(
                style, true, bg = bg, card = Color(0xFF17191D), text = Color(0xFFF2F3F5),
                muted = Color(0xFF9EA3AD), line = Color(0xFF2A2D33),
                accent = a, accentText = accentText, accentSoft = mix(a, bg, 0.7f), onAccent = Color.White,
                heroStart = bg, heroEnd = bg, heroText = Color(0xFFF2F3F5),
                allowed = accentText, unknown = unknown, blocked = blocked,
                allowedBg = mix(accentText, bg, 0.85f), unknownBg = mix(unknown, bg, 0.85f),
                blockedBg = mix(blocked, bg, 0.85f), danger = Color(0xFFD93025),
            )
        } else {
            val bg = Color.White
            val unknown = Color(0xFFA65800)
            val blocked = Color(0xFFC62828)
            Palette(
                style, false, bg = bg, card = Color(0xFFF2F3F5), text = Color(0xFF111317),
                muted = Color(0xFF5A5F69), line = Color(0xFFE1E3E8),
                accent = a, accentText = a, accentSoft = mix(a, bg, 0.85f), onAccent = Color.White,
                heroStart = bg, heroEnd = bg, heroText = Color(0xFF111317),
                allowed = a, unknown = unknown, blocked = blocked,
                allowedBg = mix(a, bg, 0.9f), unknownBg = mix(unknown, bg, 0.9f),
                blockedBg = mix(blocked, bg, 0.9f), danger = blocked,
            )
        }
    }
}

val LocalPalette = staticCompositionLocalOf { palette(AppStyle.SOFT, false, Accent.INDIGO) }
