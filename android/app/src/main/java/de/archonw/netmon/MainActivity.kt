package de.archonw.netmon

import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

sealed interface Screen {
    data object Home : Screen
    data object List : Screen
    data class Detail(val mac: String) : Screen
    data object Setup : Screen
    data object PiOverview : Screen
    data class PiDetail(val id: String) : Screen
    data object PiSetup : Screen
    data object HubSetup : Screen
}

class MainActivity : FragmentActivity() {

    private lateinit var settings: Settings
    private var unlocked by mutableStateOf(false)
    private var authError by mutableStateOf<String?>(null)
    private var screen by mutableStateOf<Screen>(Screen.Home)
    private var backgroundSince = 0L
    private var themeMode by mutableStateOf(ThemeMode.SYSTEM)
    private var useBiometrics by mutableStateOf(false)
    private var appStyle by mutableStateOf(AppStyle.SOFT)
    private var accent by mutableStateOf(Accent.INDIGO)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        themeMode = settings.themeMode
        appStyle = settings.appStyle
        accent = settings.accent(appStyle)
        settings.migrateLockMode()
        useBiometrics = settings.useBiometrics
        forgetPasswordIfPaired()
        // Ohne Einrichtung gibt es noch kein Passwort: die erste Anmeldung ist die Einrichtung selbst
        if (!settings.isConfigured) {
            screen = Screen.HubSetup
            unlocked = true
        }
        handleLink(intent)

        setContent {
            val dark = when (themeMode) {
                ThemeMode.SYSTEM -> isSystemInDarkTheme()
                ThemeMode.LIGHT -> false
                ThemeMode.DARK -> true
            }
            LaunchedEffect(dark) {
                // Statusleisten-Symbole passend zum gewählten Modus einfärben
                val bars = if (dark) {
                    SystemBarStyle.dark(android.graphics.Color.TRANSPARENT)
                } else {
                    SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
                }
                enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
            }
            val p = palette(appStyle, dark, accent)
            MaterialTheme(colorScheme = p.colorScheme()) {
                CompositionLocalProvider(LocalPalette provides p) {
                    if (!unlocked) {
                        LockedScreen(
                            useBiometrics = useBiometrics,
                            error = authError,
                            onBiometric = ::authenticate,
                            onPassword = ::unlockWithPassword,
                        )
                    } else {
                        Content(p)
                    }
                }
            }
        }
        if (!unlocked) authenticate()
    }

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Content(p: Palette) {
        BackHandler(enabled = screen != Screen.Home && settings.isConfigured) {
            screen = when (screen) {
                is Screen.Detail -> Screen.List
                is Screen.PiDetail, Screen.PiSetup -> if (settings.isPiConfigured) Screen.PiOverview else Screen.Home
                else -> Screen.Home
            }
        }
        // Bei offener Tastatur schrumpft der Inhalt, damit sich alle Felder darüber scrollen lassen
        Column(Modifier.fillMaxSize().background(p.bg).imePadding()) {
            Box(Modifier.weight(1f)) {
                when (val s = screen) {
                    Screen.Setup -> SettingsScreen(
                        settings = settings,
                        themeMode = themeMode,
                        onThemeChange = {
                            themeMode = it
                            settings.themeMode = it
                        },
                        style = appStyle,
                        onStyleChange = {
                            appStyle = it
                            settings.appStyle = it
                            accent = settings.accent(it)
                        },
                        accent = accent,
                        onAccentChange = {
                            accent = it
                            settings.setAccent(appStyle, it)
                        },
                        useBiometrics = useBiometrics,
                        onLockModeChange = ::setLockMode,
                        onSaved = { screen = Screen.Home },
                    )
                    Screen.Home -> HomeScreen(
                        settings = settings,
                        api = settings.api(),
                        onNetwork = { screen = Screen.List },
                        onPi = { screen = if (settings.isPiConfigured) Screen.PiOverview else Screen.PiSetup },
                        onHub = { screen = Screen.HubSetup },
                    )
                    Screen.HubSetup -> HubSetupScreen(
                        settings = settings,
                        onSaved = {
                            forgetPasswordIfPaired()
                            screen = Screen.Home
                        },
                        onBack = if (settings.isConfigured) ({ screen = Screen.Home }) else null,
                    )
                    Screen.PiOverview -> PiOverviewScreen(
                        api = settings.piApi(),
                        onOpen = { screen = Screen.PiDetail(it) },
                        onSetup = { screen = Screen.PiSetup },
                        onBack = { screen = Screen.Home },
                    )
                    is Screen.PiDetail -> PiDetailScreen(
                        api = settings.piApi(),
                        id = s.id,
                        onBack = { screen = Screen.PiOverview },
                    )
                    Screen.PiSetup -> PiSetupScreen(
                        settings = settings,
                        onSaved = { screen = Screen.PiOverview },
                        onBack = { screen = if (settings.isPiConfigured) Screen.PiOverview else Screen.Home },
                    )
                    Screen.List -> ListScreen(
                        api = settings.api(),
                        settings = settings,
                        style = appStyle,
                        onOpen = { screen = Screen.Detail(it) },
                    )
                    is Screen.Detail -> DetailScreen(
                        api = settings.api(),
                        mac = s.mac,
                        style = appStyle,
                        onBack = { screen = Screen.List },
                    )
                }
            }
            if (settings.isConfigured && !WindowInsets.isImeVisible) {
                NavBar(
                    style = appStyle,
                    settingsActive = screen == Screen.Setup,
                    onHome = { screen = Screen.Home },
                    onSettings = { screen = Screen.Setup },
                )
            } else {
                Spacer(Modifier.navigationBarsPadding())
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (handleLink(intent)) {
            // Jede Benachrichtigung verlangt eine neue Bestätigung per Fingerabdruck
            unlocked = false
            authenticate()
        }
    }

    override fun onStop() {
        super.onStop()
        backgroundSince = SystemClock.elapsedRealtime()
    }

    override fun onStart() {
        super.onStart()
        if (unlocked && backgroundSince > 0 &&
            SystemClock.elapsedRealtime() - backgroundSince > LOCK_AFTER_MS
        ) {
            unlocked = false
            authenticate()
        }
    }

    /** netmon://device/<MAC> → Detailseite dieses Geräts. */
    private fun handleLink(intent: Intent?): Boolean {
        val data = intent?.data ?: return false
        if (data.scheme != "netmon" || data.host != "device") return false
        val mac = data.lastPathSegment ?: return false
        if (settings.isConfigured) screen = Screen.Detail(mac)
        return true
    }

    private fun unlockWithPassword(entered: String) {
        if (entered.isNotEmpty() && entered == settings.password) {
            authError = null
            unlocked = true
        } else {
            authError = "Passwort falsch"
        }
    }

    /** Ein gekoppeltes Gerät meldet sich mit seinem Schlüssel an und braucht kein gespeichertes Passwort. */
    private fun forgetPasswordIfPaired() {
        if (settings.hubToken.isBlank() || !biometricsAvailable()) return
        settings.password = ""
        settings.piPassword = ""
        setLockMode(true)
    }

    private fun biometricsAvailable() =
        BiometricManager.from(this).canAuthenticate(BIOMETRIC_STRONG or DEVICE_CREDENTIAL) ==
            BiometricManager.BIOMETRIC_SUCCESS

    /** Liefert eine Fehlermeldung, wenn der Fingerabdruck nicht genutzt werden kann. */
    private fun setLockMode(biometrics: Boolean): String? {
        if (biometrics && !biometricsAvailable()) {
            return "Auf diesem Gerät ist kein Fingerabdruck und keine Displaysperre eingerichtet."
        }
        useBiometrics = biometrics
        settings.useBiometrics = biometrics
        return null
    }

    private fun authenticate() {
        authError = null
        // Im Passwort-Modus zeigt der Sperrbildschirm das Eingabefeld
        if (!useBiometrics) return
        val authenticators = BIOMETRIC_STRONG or DEVICE_CREDENTIAL
        if (!biometricsAvailable()) {
            authError = "Auf diesem Gerät ist keine Displaysperre eingerichtet. " +
                "Bitte mit dem Passwort entsperren."
            return
        }
        val prompt = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    authError = null
                    unlocked = true
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    authError = errString.toString()
                }
            },
        )
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("Home-Net entsperren")
                .setSubtitle("Bestätige mit deinem Fingerabdruck")
                .setAllowedAuthenticators(authenticators)
                .build(),
        )
    }

    companion object {
        private const val LOCK_AFTER_MS = 60_000L
    }
}
