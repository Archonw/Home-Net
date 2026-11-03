package de.archonw.netmon

import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject

data class PidUser(
    val id: String,
    val username: String,
    val name: String,
    val firstName: String,
    val lastName: String,
    val email: String,
    val admin: Boolean,
    val disabled: Boolean,
    val groups: List<String>,
    /** null: unbekannt */
    val passkeys: Int?,
)

/** Eine App ist eine Gruppe in Pocket ID, die mindestens einen Client freischaltet. */
data class PidApp(val group: String, val label: String, val groupName: String)

data class PidOverview(
    val configured: Boolean,
    val error: String,
    val users: List<PidUser>,
    val apps: List<PidApp>,
    val openApps: List<String>,
)

data class PidInvite(val link: String, val expires: Long, val sent: Boolean)

/** Pocket-ID-Verwaltung, nur über den gemeinsamen Zugang erreichbar. */
class PocketIdApi(private val http: PiApi) {

    suspend fun overview(): PidOverview {
        val o = JSONObject(http.raw("api/overview"))
        val apps = o.optJSONObject("apps") ?: JSONObject()
        return PidOverview(
            configured = o.optBoolean("configured"),
            error = o.optString("error"),
            users = o.optJSONArray("users").objects().map {
                PidUser(
                    id = it.optString("id"),
                    username = it.optString("username"),
                    name = it.optString("name"),
                    firstName = it.optString("first_name"),
                    lastName = it.optString("last_name"),
                    email = it.optString("email"),
                    admin = it.optBoolean("admin"),
                    disabled = it.optBoolean("disabled"),
                    groups = it.optJSONArray("groups").strings(),
                    passkeys = if (it.isNull("passkeys")) null else it.optInt("passkeys"),
                )
            },
            apps = apps.optJSONArray("switches").objects().map {
                PidApp(it.optString("group"), it.optString("label"), it.optString("group_name"))
            },
            openApps = apps.optJSONArray("open").strings(),
        )
    }

    suspend fun create(first: String, last: String, username: String, email: String, groups: List<String>): PidInvite? {
        val body = JSONObject()
            .put("first_name", first.trim()).put("last_name", last.trim())
            .put("username", username.trim()).put("email", email.trim())
            .put("groups", JSONArray(groups))
        return JSONObject(http.postJson("api/users", body.toString())).optJSONObject("invite")?.let(::invite)
    }

    suspend fun update(id: String, first: String, last: String, email: String) {
        http.putJson("api/users/${enc(id)}", JSONObject().put("first_name", first.trim()).put("last_name", last.trim()).put("email", email.trim()).toString())
    }

    suspend fun setDisabled(id: String, disabled: Boolean) {
        http.putJson("api/users/${enc(id)}", JSONObject().put("disabled", disabled).toString())
    }

    suspend fun setApp(id: String, group: String, on: Boolean) {
        http.putJson("api/users/${enc(id)}/apps", JSONObject().put("group", group).put("on", on).toString())
    }

    suspend fun delete(id: String) { http.delete("api/users/${enc(id)}") }

    suspend fun invite(id: String, byEmail: Boolean = false): PidInvite =
        invite(JSONObject(http.postJson("api/users/${enc(id)}/invite", JSONObject().put("email", byEmail).toString())))

    private fun invite(o: JSONObject) = PidInvite(o.optString("link"), o.optLong("expires"), o.optBoolean("sent"))

    private fun enc(name: String): String = Uri.encode(name)

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
}
