package de.archonw.netmon

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/** Eine Meldung vom Zugang. [link] ist z. B. device/<MAC> oder pi/<id>. */
data class HubEvent(
    val id: Long,
    val time: Long,
    val title: String,
    val message: String,
    val high: Boolean,
    val link: String,
) {
    companion object {
        fun from(o: JSONObject) = HubEvent(
            id = o.getLong("id"),
            time = o.optLong("time"),
            title = o.optString("title"),
            message = o.optString("message"),
            high = o.optString("priority") == "high",
            link = o.optString("link").takeIf { LINK.matches(it) }.orEmpty(),
        )
    }
}

private val LINK = Regex("(device|pi)/[A-Za-z0-9:._-]+")

/** Liest den Datenstrom des Zugangs zeilenweise; Kommentare halten nur die Verbindung offen. */
class EventLines {
    private var inStart = false

    sealed interface Item
    data class Latest(val id: Long) : Item
    data class Message(val event: HubEvent) : Item

    fun feed(line: String): Item? {
        if (line == "event: start") {
            inStart = true
            return null
        }
        if (!line.startsWith("data: ")) return null
        val data = line.removePrefix("data: ")
        if (inStart) {
            inStart = false
            return data.toLongOrNull()?.let(::Latest)
        }
        return runCatching { Message(HubEvent.from(JSONObject(data))) }.getOrNull()
    }
}

/** Hält im Hintergrund die Verbindung zum Zugang und zeigt ankommende Meldungen an. */
class NotificationService : Service() {
    @Volatile private var running = false
    @Volatile private var connection: HttpURLConnection? = null
    private var worker: Thread? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannels(this)
        val notification = NotificationCompat.Builder(this, CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Home-Net")
            .setContentText("Bereit für Meldungen")
            .setOngoing(true)
            .setShowWhen(false)
            .setContentIntent(openIntent(this, ""))
            .build()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(SERVICE_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(SERVICE_ID, notification)
            }
        } catch (_: IllegalStateException) {
            // Android erlaubt den Start gerade nicht; beim nächsten Öffnen der App geht es weiter
            stopSelf()
            return START_NOT_STICKY
        }
        if (worker == null) {
            running = true
            worker = thread(name = "home-net-meldungen") { loop() }
            watchNetwork()
        } else {
            // Adresse oder Schlüssel können sich geändert haben
            reconnect()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        networkCallback?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        reconnect()
        super.onDestroy()
    }

    private fun reconnect() {
        connection?.disconnect()
        worker?.interrupt()
    }

    private fun watchNetwork() {
        val callback = object : ConnectivityManager.NetworkCallback() {
            private var last: Network? = null

            override fun onAvailable(network: Network) {
                if (last != null && last != network) reconnect()
                last = network
            }
        }
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(callback)
        networkCallback = callback
    }

    private enum class Outcome { CONNECTED, FAILED, TOO_MANY, REFUSED }

    private fun loop() {
        val settings = Settings(this)
        var wait = MIN_WAIT_MS
        while (running) {
            when (listen(settings)) {
                // Der Schlüssel gilt nicht mehr: Weitere Versuche würden die Adresse nur sperren
                Outcome.REFUSED -> {
                    stopSelf()
                    return
                }
                Outcome.CONNECTED -> wait = MIN_WAIT_MS
                Outcome.TOO_MANY -> wait = MAX_WAIT_MS
                Outcome.FAILED -> Unit
            }
            try {
                Thread.sleep(wait)
            } catch (_: InterruptedException) {
                wait = MIN_WAIT_MS
                continue
            }
            wait = (wait * 2).coerceAtMost(MAX_WAIT_MS)
        }
    }

    private fun listen(settings: Settings): Outcome {
        val token = settings.hubToken
        if (token.isBlank()) return Outcome.REFUSED
        for (base in listOf(settings.hubUrl, settings.hubExternalUrl).filter { it.isNotBlank() }.distinct()) {
            if (!running) return Outcome.FAILED
            try {
                val outcome = listenAt(base, token, settings)
                if (outcome != Outcome.FAILED) return outcome
            } catch (_: IOException) {
                // nächste Adresse versuchen
            }
        }
        return Outcome.FAILED
    }

    private fun listenAt(base: String, token: String, settings: Settings): Outcome {
        val after = settings.lastEventId
        val path = if (after > 0) "/auth/stream?after=$after" else "/auth/stream"
        val conn = URL(base.trimEnd('/') + path).openConnection() as HttpURLConnection
        connection = conn
        try {
            conn.connectTimeout = CONNECT_TIMEOUT_MS
            conn.readTimeout = READ_TIMEOUT_MS
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.setRequestProperty("Accept", "text/event-stream")
            when (conn.responseCode) {
                200 -> Unit
                401, 403 -> return Outcome.REFUSED
                429 -> return Outcome.TOO_MANY
                else -> return Outcome.FAILED
            }
            val parser = EventLines()
            try {
                conn.inputStream.bufferedReader().useLines { lines ->
                    for (line in lines) {
                        when (val item = parser.feed(line)) {
                            is EventLines.Latest -> {
                                // Neuer Server oder neu aufgesetzt: Zähler mitnehmen
                                if (after == 0L || item.id < after) settings.lastEventId = item.id
                            }
                            is EventLines.Message -> {
                                show(this, item.event)
                                settings.lastEventId = item.event.id
                            }
                            null -> Unit
                        }
                    }
                }
            } catch (_: IOException) {
                // Verbindung abgerissen, gleich neu aufbauen
            }
            return Outcome.CONNECTED
        } finally {
            conn.disconnect()
            connection = null
        }
    }

    companion object {
        private const val SERVICE_ID = 1
        private const val CHANNEL_SERVICE = "home_net_verbindung"
        private const val CHANNEL_HIGH = "home_net_wichtig"
        private const val CHANNEL_NORMAL = "home_net_meldungen"
        private const val MIN_WAIT_MS = 5_000L
        private const val MAX_WAIT_MS = 15 * 60_000L
        private const val CONNECT_TIMEOUT_MS = 4_000
        // Der Zugang meldet sich alle 45 Sekunden; bleibt das aus, ist die Verbindung tot
        private const val READ_TIMEOUT_MS = 130_000

        /** Bringt den Dienst in den Zustand, den die Einstellungen verlangen. */
        fun sync(context: Context, settings: Settings) {
            val intent = Intent(context, NotificationService::class.java)
            if (settings.isHubConfigured && settings.hubToken.isNotBlank() && settings.pushEnabled) {
                runCatching { ContextCompat.startForegroundService(context, intent) }
            } else {
                context.stopService(intent)
            }
        }

        private fun createChannels(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannels(
                listOf(
                    NotificationChannel(CHANNEL_HIGH, "Wichtige Meldungen", NotificationManager.IMPORTANCE_HIGH),
                    NotificationChannel(CHANNEL_NORMAL, "Meldungen", NotificationManager.IMPORTANCE_DEFAULT),
                    NotificationChannel(CHANNEL_SERVICE, "Verbindung zum Server", NotificationManager.IMPORTANCE_MIN),
                ),
            )
        }

        private fun openIntent(context: Context, link: String, code: Int = 0): PendingIntent {
            val intent = if (link.isEmpty()) {
                Intent(context, MainActivity::class.java)
            } else {
                Intent(Intent.ACTION_VIEW, Uri.parse("netmon://$link")).setPackage(context.packageName)
            }
            return PendingIntent.getActivity(
                context, code, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }

        private fun show(context: Context, event: HubEvent) {
            val builder = NotificationCompat.Builder(context, if (event.high) CHANNEL_HIGH else CHANNEL_NORMAL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(event.title)
                .setContentText(event.message)
                .setStyle(NotificationCompat.BigTextStyle().bigText(event.message))
                .setWhen(event.time * 1000)
                .setShowWhen(true)
                .setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setContentIntent(openIntent(context, event.link, event.id.toInt()))
            context.getSystemService(NotificationManager::class.java).notify(event.id.toInt() + SERVICE_ID, builder.build())
        }
    }
}

/** Startet den Empfang nach einem Neustart des Handys und nach einem App-Update wieder. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            NotificationService.sync(context, Settings(context))
        }
    }
}

private data class NotifyType(val key: String, val group: String, val label: String, val enabled: Boolean)

private class NotifyState(val types: List<NotifyType>, val recent: List<HubEvent>) {
    companion object {
        fun parse(body: String): NotifyState {
            val o = JSONObject(body)
            val types = o.getJSONArray("types")
            val recent = o.getJSONArray("recent")
            return NotifyState(
                (0 until types.length()).map { i ->
                    val t = types.getJSONObject(i)
                    NotifyType(t.getString("key"), t.optString("group"), t.optString("label"), t.optBoolean("enabled"))
                },
                (0 until recent.length()).map { HubEvent.from(recent.getJSONObject(it)) },
            )
        }
    }
}

/** Ob dieses Handy Meldungen bekommt und welche das Konto haben möchte. */
@Composable
fun NotificationsScreen(settings: Settings, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val p = LocalPalette.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = remember { PiApi(settings.hubUrl, settings.hubSecret, settings.hubExternalUrl) }
    var state by remember { mutableStateOf<NotifyState?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var enabled by remember { mutableStateOf(settings.pushEnabled) }
    // Wird nach der Rückkehr aus den Android-Dialogen erhöht und liest deren Ergebnis neu
    var returned by remember { mutableIntStateOf(0) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { returned++ }
    val batteryLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { returned++ }
    val allowed = remember(returned) {
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
    }
    val unrestricted = remember(returned) {
        context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName)
    }

    LaunchedEffect(Unit) {
        try {
            state = NotifyState.parse(api.raw("auth/notifications"))
        } catch (e: ApiException) {
            message = e.message
        }
    }

    Column(Modifier.fillMaxSize().background(p.bg)) {
        PageTitle("Benachrichtigungen", onBack = onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            HubCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Auf diesem Handy empfangen", color = p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        Text(
                            "Die App hält dafür im Hintergrund die Verbindung. Den festen Eintrag in der " +
                                "Leiste kannst du in den Android-Einstellungen ausblenden.",
                            color = p.muted, fontSize = 13.sp,
                        )
                    }
                    Switch(checked = enabled, onCheckedChange = {
                        enabled = it
                        settings.pushEnabled = it
                        NotificationService.sync(context, settings)
                    })
                }
                if (enabled && !allowed) {
                    TextButton(onClick = { permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) }) {
                        Text("Benachrichtigungen erlauben", color = p.blocked)
                    }
                }
                if (enabled && !unrestricted) {
                    TextButton(onClick = {
                        batteryLauncher.launch(
                            Intent(
                                AndroidSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:${context.packageName}"),
                            ),
                        )
                    }) { Text("Von der Akku-Optimierung ausnehmen, damit nichts verspätet kommt", color = p.accentText) }
                }
            }
            message?.let { Text(it, color = p.blocked, fontSize = 14.sp) }
            state?.let { current ->
                current.types.groupBy { it.group }.forEach { (group, types) ->
                    SectionTitle(group)
                    HubCard {
                        types.forEach { type ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text(type.label, color = p.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
                                Switch(checked = type.enabled, onCheckedChange = { on ->
                                    message = null
                                    scope.launch {
                                        try {
                                            val body = JSONObject().put("enabled", JSONObject().put(type.key, on)).toString()
                                            state = NotifyState.parse(api.postJson("auth/notifications", body))
                                        } catch (e: ApiException) {
                                            message = e.message
                                        }
                                    }
                                })
                            }
                        }
                    }
                }
                if (current.recent.isNotEmpty()) {
                    SectionTitle("Zuletzt")
                    current.recent.forEach { event ->
                        HubCard(onClick = if (event.link.isEmpty()) null else ({ onOpen(event.link) })) {
                            Text(event.title, color = if (event.high) p.blocked else p.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                            if (event.message.isNotBlank()) Text(event.message, color = p.text, fontSize = 14.sp)
                            Text(formatEventTime(event.time), color = p.muted, fontSize = 12.sp)
                        }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

private fun formatEventTime(seconds: Long): String =
    SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.GERMANY).format(Date(seconds * 1000))
