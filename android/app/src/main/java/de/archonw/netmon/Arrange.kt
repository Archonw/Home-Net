package de.archonw.netmon

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Gespeicherte Reihenfolge: bekannte Einträge in der gespeicherten Folge, neue hinten angehängt. */
fun normalizedOrder(saved: List<String>, all: List<String>): List<String> =
    saved.filter { it in all }.distinct() + all.filter { it !in saved }

fun <T> List<T>.moved(index: Int, by: Int): List<T> {
    val target = index + by
    if (index !in indices || target !in indices) return this
    return toMutableList().also { it.add(target, it.removeAt(index)) }
}

@Composable
internal fun ArrangeHint() {
    Text("Mit ▲ und ▼ verschieben. Die Anordnung merkt sich dieses Handy.", color = LocalPalette.current.muted, fontSize = 13.sp)
}

/** Liste zum Umsortieren mit Pfeilen nach oben und unten. */
@Composable
internal fun OrderList(items: List<String>, labels: Map<String, String>, onChange: (List<String>) -> Unit) {
    val p = LocalPalette.current
    HubCard {
        items.forEachIndexed { i, item ->
            Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(labels[item] ?: item, color = p.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
                TextButton(enabled = i > 0, onClick = { onChange(items.moved(i, -1)) }) { Text("▲", fontSize = 16.sp) }
                TextButton(enabled = i < items.size - 1, onClick = { onChange(items.moved(i, 1)) }) { Text("▼", fontSize = 16.sp) }
            }
        }
    }
}
