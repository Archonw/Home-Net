package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

data class NpmCertRef(val id: Int, val name: String, val expires: Long, val days: Int?, val state: String, val provider: String)

data class NpmDns(val domain: String, val state: String, val ips: List<String>)

data class NpmHost(
    val id: Int,
    /** proxy, redirect oder 404 */
    val kind: String,
    val domains: List<String>,
    val target: String,
    val enabled: Boolean,
    /** public, password, restricted, internal, off */
    val exposure: String,
    val exposureLabel: String,
    val note: String,
    val accessList: String,
    val ssl: NpmCertRef?,
    val sslForced: Boolean,
    val nginxError: String,
    val dns: List<NpmDns>,
    /** up, down, unknown oder leer */
    val targetState: String,
) {
    val key get() = "$kind-$id"
    val title get() = domains.firstOrNull() ?: "?"
    val problems: List<String>
        get() = buildList {
            dns.forEach { d -> NPM_DNS_TEXT[d.state]?.let { add("${d.domain}: $it") } }
            if (targetState == "down") add("Das Ziel antwortet nicht.")
            if (nginxError.isNotBlank()) add("Fehler in der Konfiguration: $nginxError")
        }
}

val NPM_DNS_TEXT = mapOf(
    "missing" to "im Internet nicht eingetragen",
    "private" to "zeigt im Internet auf eine interne Adresse",
    "ipv6" to "im Internet nur per IPv6 eingetragen",
)

data class NpmCert(
    val id: Int,
    val name: String,
    val domains: List<String>,
    val provider: String,
    val expires: Long,
    val days: Int?,
    val state: String,
    val hosts: List<String>,
    val unused: Boolean,
)

data class NpmStream(val port: Int, val target: String, val tcp: Boolean, val udp: Boolean, val enabled: Boolean)

data class NpmOverview(
    val configured: Boolean,
    val error: String,
    val version: String,
    val hosts: List<NpmHost>,
    val certs: List<NpmCert>,
    val streams: List<NpmStream>,
) {
    fun count(exposure: String) = hosts.count { it.exposure == exposure }
    val certsSoon get() = certs.count { !it.unused && it.state == "soon" }
    val certsExpired get() = certs.count { !it.unused && it.state == "expired" }
    val problems get() = hosts.count { it.enabled && it.problems.isNotEmpty() } + certsExpired
}

data class NpmSettings(val url: String, val email: String, val passwordSet: Boolean)

/** Übersicht über Nginx Proxy Manager, nur über den gemeinsamen Zugang erreichbar. */
class ProxyApi(private val http: PiApi) {

    suspend fun overview(): NpmOverview {
        val o = JSONObject(http.raw("api/overview"))
        return NpmOverview(
            configured = o.optBoolean("configured"),
            error = o.optString("error"),
            version = o.optString("version"),
            hosts = o.optJSONArray("hosts").objects().map { h ->
                NpmHost(
                    id = h.optInt("id"),
                    kind = h.optString("kind"),
                    domains = h.optJSONArray("domains").strings(),
                    target = h.optString("target"),
                    enabled = h.optBoolean("enabled"),
                    exposure = h.optString("exposure"),
                    exposureLabel = h.optString("exposure_label"),
                    note = h.optString("note"),
                    accessList = h.optString("access_list"),
                    ssl = h.optJSONObject("ssl")?.let { c ->
                        NpmCertRef(c.optInt("id"), c.optString("name"), c.optLong("expires"), c.days(), c.optString("state"), c.optString("provider"))
                    },
                    sslForced = h.optBoolean("ssl_forced"),
                    nginxError = h.optString("nginx_error"),
                    dns = h.optJSONArray("dns").objects().map { NpmDns(it.optString("domain"), it.optString("state"), it.optJSONArray("ips").strings()) },
                    targetState = h.optString("target_state"),
                )
            },
            certs = o.optJSONArray("certs").objects().map { c ->
                NpmCert(
                    id = c.optInt("id"),
                    name = c.optString("name"),
                    domains = c.optJSONArray("domains").strings(),
                    provider = c.optString("provider"),
                    expires = c.optLong("expires"),
                    days = c.days(),
                    state = c.optString("state"),
                    hosts = c.optJSONArray("hosts").strings(),
                    unused = c.optBoolean("unused"),
                )
            },
            streams = o.optJSONArray("streams").objects().map {
                NpmStream(it.optInt("port"), it.optString("target"), it.optBoolean("tcp"), it.optBoolean("udp"), it.optBoolean("enabled"))
            },
        )
    }

    suspend fun settings(): NpmSettings {
        val o = JSONObject(http.raw("api/settings"))
        return NpmSettings(o.optString("url"), o.optString("email"), o.optBoolean("password_set"))
    }

    suspend fun saveSettings(url: String, email: String, password: String) {
        val body = JSONObject().put("url", url.trim()).put("email", email.trim())
        if (password.isNotEmpty()) body.put("password", password)
        http.putJson("api/settings", body.toString())
    }

    suspend fun renew(certId: Int) { http.postJson("api/certs/$certId/renew", "{}") }

    suspend fun setEnabled(host: NpmHost, on: Boolean) {
        http.putJson("api/hosts/${host.kind}/${host.id}", JSONObject().put("enabled", on).toString())
    }

    private fun JSONObject.days(): Int? = if (isNull("days")) null else optInt("days")

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
}
