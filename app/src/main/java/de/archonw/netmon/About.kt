package de.archonw.netmon

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

const val SOURCE_URL = "https://github.com/Archonw/Home-Net"

/** Bibliotheken, die in der App stecken; alle unter der Apache-Lizenz 2.0. */
private val LIBRARIES = listOf(
    "AndroidX (Activity, Biometric, Fragment, Lifecycle, Security Crypto) – The Android Open Source Project",
    "Jetpack Compose und Material 3 – The Android Open Source Project",
    "Kotlin Standardbibliothek und kotlinx.coroutines – JetBrains s.r.o. und Mitwirkende",
)

/** Version, Datenschutzhinweis und Lizenzen; steht in beiden Designs unten in den Einstellungen. */
@Composable
fun AboutContent() {
    val p = LocalPalette.current
    val context = LocalContext.current
    val uriHandler = LocalUriHandler.current
    val version = remember {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
    }
    var showLicenses by remember { mutableStateOf(false) }

    Text("Home-Net $version", color = p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    Spacer(Modifier.height(8.dp))
    Text(
        "Datenschutz: Die App spricht nur mit deinem eigenen Monitor. Adresse und Passwort liegen " +
            "verschlüsselt auf diesem Gerät. Es gibt keine Werbung, kein Tracking und keine Weitergabe " +
            "von Daten an Dritte.",
        color = p.muted,
        fontSize = 13.sp,
    )
    Spacer(Modifier.height(8.dp))
    Text(
        "Home-Net ist freie Software unter der Apache-Lizenz 2.0.",
        color = p.muted,
        fontSize = 13.sp,
    )
    TextButton(onClick = { uriHandler.openUri(SOURCE_URL) }) { Text("Quellcode auf GitHub") }
    TextButton(onClick = { showLicenses = true }) { Text("Open-Source-Lizenzen") }

    if (showLicenses) {
        val license = remember {
            context.resources.openRawResource(R.raw.apache_license_2_0).bufferedReader().use { it.readText() }
        }
        AlertDialog(
            onDismissRequest = { showLicenses = false },
            title = { Text("Open-Source-Lizenzen") },
            text = {
                Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState())) {
                    Text("Home-Net steht unter der Apache-Lizenz 2.0 ($SOURCE_URL).")
                    Spacer(Modifier.height(8.dp))
                    Text("Die App verwendet außerdem folgende Bibliotheken unter derselben Lizenz:")
                    Spacer(Modifier.height(8.dp))
                    LIBRARIES.forEach { Text("• $it", fontSize = 13.sp) }
                    Spacer(Modifier.height(12.dp))
                    Text(license, fontFamily = FontFamily.Monospace, fontSize = 10.sp)
                }
            },
            confirmButton = { TextButton(onClick = { showLicenses = false }) { Text("Schließen") } },
        )
    }
}
