package de.archonw.netmon

import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject

data class NcUser(
    val id: String,
    val name: String,
    val email: String,
    val enabled: Boolean,
    val groups: List<String>,
    val admin: Boolean,
    val used: Long,
    /** null: keine eigene Grenze (Standard oder unbegrenzt) */
    val quota: Long?,
    val quotaText: String,
    val lastLogin: Long,
    val backend: String,
    val canPassword: Boolean,
)

data class NcApp(val id: String, val version: String)

data class NcInfo(
    val freeBytes: Long?,
    val storageLow: Boolean,
    val files: Long,
    val shares: Long,
    val active5min: Int,
    val active1h: Int,
    val active24h: Int,
    val appsInstalled: Int,
    val appUpdates: List<NcApp>,
    val serverUpdate: String,
    val php: String,
    val database: String,
)

data class NcOverview(
    val configured: Boolean,
    val error: String,
    val canManage: Boolean,
    val version: String,
    val maintenance: Boolean,
    /** null: die App serverinfo ist in Nextcloud aus */
    val info: NcInfo?,
    val users: List<NcUser>,
    val groups: List<String>,
    val userCount: Int?,
    /** Das Konto, mit dem sich Home-Net anmeldet */
    val account: String,
)

/** Nextcloud-Verwaltung, nur über den gemeinsamen Zugang erreichbar. */
class NextcloudApi(private val http: PiApi) {

    suspend fun overview(): NcOverview {
        val o = JSONObject(http.raw("api/overview"))
        val status = o.optJSONObject("status")
        val info = o.optJSONObject("info")
        return NcOverview(
            configured = o.optBoolean("configured"),
            error = o.optString("error"),
            canManage = o.optBoolean("can_manage"),
            version = status?.optString("version").orEmpty(),
            maintenance = status?.optBoolean("maintenance") == true,
            info = info?.let { i ->
                val active = i.optJSONObject("active") ?: JSONObject()
                NcInfo(
                    freeBytes = if (i.isNull("free_bytes")) null else i.optDouble("free_bytes").toLong(),
                    storageLow = i.optBoolean("storage_low"),
                    files = i.optLong("files"),
                    shares = i.optLong("shares"),
                    active5min = active.optInt("5min"),
                    active1h = active.optInt("1h"),
                    active24h = active.optInt("24h"),
                    appsInstalled = i.optInt("apps_installed"),
                    appUpdates = i.optJSONArray("app_updates").objects().map { NcApp(it.optString("id"), it.optString("version")) },
                    serverUpdate = i.optString("server_update"),
                    php = i.optString("php"),
                    database = i.optString("database"),
                )
            },
            users = o.optJSONArray("users").objects().map {
                NcUser(
                    id = it.optString("id"),
                    name = it.optString("name"),
                    email = it.optString("email"),
                    enabled = it.optBoolean("enabled", true),
                    groups = it.optJSONArray("groups").strings(),
                    admin = it.optBoolean("admin"),
                    used = if (it.isNull("used")) 0L else it.optDouble("used").toLong(),
                    quota = if (it.isNull("quota")) null else it.optDouble("quota").toLong(),
                    quotaText = it.optString("quota_text"),
                    lastLogin = it.optLong("last_login"),
                    backend = it.optString("backend"),
                    canPassword = it.optBoolean("can_password"),
                )
            },
            groups = o.optJSONArray("groups").strings(),
            userCount = if (o.isNull("user_count")) null else o.optInt("user_count"),
            account = o.optString("account"),
        )
    }

    suspend fun create(id: String, name: String, email: String, password: String, quota: String, groups: List<String>) {
        val body = JSONObject()
            .put("id", id.trim()).put("name", name.trim()).put("email", email.trim())
            .put("password", password).put("quota", quota.trim()).put("groups", JSONArray(groups))
        http.postJson("api/users", body.toString())
    }

    /** Nur die Felder, die nicht null sind, werden geändert. */
    suspend fun update(id: String, name: String? = null, email: String? = null, quota: String? = null, password: String? = null) {
        val body = JSONObject()
        name?.let { body.put("name", it.trim()) }
        email?.let { body.put("email", it.trim()) }
        quota?.let { body.put("quota", it.trim()) }
        password?.takeIf { it.isNotEmpty() }?.let { body.put("password", it) }
        if (body.length() > 0) http.putJson("api/users/${enc(id)}", body.toString())
    }

    suspend fun setEnabled(id: String, enabled: Boolean) {
        http.putJson("api/users/${enc(id)}", JSONObject().put("enabled", enabled).toString())
    }

    suspend fun setGroup(id: String, group: String, member: Boolean) {
        http.putJson("api/users/${enc(id)}/groups", JSONObject().put("group", group).put("member", member).toString())
    }

    suspend fun welcome(id: String) { http.postJson("api/users/${enc(id)}/welcome", "{}") }

    /** [typed] muss genau der Benutzername sein, sonst lehnt der Dienst ab. */
    suspend fun delete(id: String, typed: String) { http.delete("api/users/${enc(id)}?confirm=${enc(typed)}") }

    suspend fun createGroup(name: String) { http.postJson("api/groups", JSONObject().put("name", name.trim()).toString()) }

    private fun enc(name: String): String = Uri.encode(name)

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
}
