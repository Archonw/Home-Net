package de.archonw.netmon

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings as AndroidSettings
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

/**
 * Lädt eine neue App-Version vom Monitor und übergibt sie dem Android-Paketinstaller.
 *
 * Android fragt beim ersten Mal nach „Bestätigen“. Hat die App sich selbst schon einmal
 * aktualisiert, kann Android ab Version 12 spätere Updates ohne Rückfrage einspielen.
 */
object Updater {
    /** Darf die App Pakete installieren? Sonst muss der Nutzer es einmal in den Einstellungen erlauben. */
    fun canInstall(context: Context) = context.packageManager.canRequestPackageInstalls()

    fun openInstallPermission(context: Context) {
        context.startActivity(
            Intent(AndroidSettings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    suspend fun install(context: Context, release: AppRelease) = withContext(Dispatchers.IO) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            try {
                val conn = URL(release.url).openConnection() as HttpURLConnection
                conn.connectTimeout = 8000
                conn.readTimeout = 60000
                try {
                    if (conn.responseCode != 200) throw ApiException("Download fehlgeschlagen (${conn.responseCode})")
                    conn.inputStream.use { input ->
                        session.openWrite("netzwerkmonitor.apk", 0, conn.contentLengthLong).use { out ->
                            input.copyTo(out)
                            session.fsync(out)
                        }
                    }
                } finally {
                    conn.disconnect()
                }
                val intent = Intent(context, InstallReceiver::class.java)
                val pending = PendingIntent.getBroadcast(
                    context,
                    sessionId,
                    intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
                )
                session.commit(pending.intentSender)
            } catch (e: Exception) {
                session.abandon()
                throw e
            }
        }
    }
}

/** Ergebnis der Installation; bei Bedarf zeigt Android hier den Bestätigungsdialog. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS -> Unit // Android ersetzt die App und beendet sie dabei
            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unbekannter Fehler"
                Toast.makeText(context, "Update fehlgeschlagen: $message", Toast.LENGTH_LONG).show()
            }
        }
    }
}
