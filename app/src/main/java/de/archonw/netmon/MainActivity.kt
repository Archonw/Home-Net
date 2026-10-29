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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

sealed interface Screen {
    data object List : Screen
    data class Detail(val mac: String) : Screen
    data object Setup : Screen
}

class MainActivity : FragmentActivity() {

    private lateinit var settings: Settings
    private var unlocked by mutableStateOf(false)
    private var authError by mutableStateOf<String?>(null)
    private var screen by mutableStateOf<Screen>(Screen.List)
    private var backgroundSince = 0L
    private var themeMode by mutableStateOf(ThemeMode.SYSTEM)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        themeMode = settings.themeMode
        if (!settings.isConfigured) screen = Screen.Setup
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
            val colors = if (dark) {
                darkColorScheme(primary = Green, error = Red)
            } else {
                lightColorScheme(primary = Green, error = Red)
            }
            MaterialTheme(colorScheme = colors) {
                if (!unlocked) {
                    LockedScreen(error = authError, onUnlock = ::authenticate)
                } else {
                    BackHandler(enabled = screen != Screen.List && settings.isConfigured) {
                        screen = Screen.List
                    }
                    when (val s = screen) {
                        Screen.Setup -> SetupScreen(
                            settings = settings,
                            themeMode = themeMode,
                            onThemeChange = {
                                themeMode = it
                                settings.themeMode = it
                            },
                            onBack = { screen = Screen.List },
                            onSaved = { screen = Screen.List },
                        )
                        Screen.List -> ListScreen(
                            api = settings.api(),
                            settings = settings,
                            onOpen = { screen = Screen.Detail(it) },
                            onSettings = { screen = Screen.Setup },
                        )
                        is Screen.Detail -> DetailScreen(
                            api = settings.api(),
                            mac = s.mac,
                            onBack = { screen = Screen.List },
                        )
                    }
                }
            }
        }
        authenticate()
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

    private fun authenticate() {
        val authenticators = BIOMETRIC_STRONG or DEVICE_CREDENTIAL
        val can = BiometricManager.from(this).canAuthenticate(authenticators)
        if (can != BiometricManager.BIOMETRIC_SUCCESS) {
            authError = "Auf diesem Gerät ist keine Displaysperre eingerichtet. " +
                "Bitte Fingerabdruck oder PIN in den Android-Einstellungen aktivieren."
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
                .setTitle("Netzwerkmonitor entsperren")
                .setSubtitle("Bestätige mit deinem Fingerabdruck")
                .setAllowedAuthenticators(authenticators)
                .build(),
        )
    }

    companion object {
        private const val LOCK_AFTER_MS = 60_000L
        val Green = Color(0xFF1A7F37)
        val Red = Color(0xFFC62828)
    }
}
