package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

data class WgClient(
    val id: Int,
    val name: String,
    val enabled: Boolean,
    val address: String,
    val created: Long,
    val expires: Long,
    val expired: Boolean,
    /** letzter Kontakt, 0: noch nie */
    val handshake: Long,
    val online: Boolean,
    /** vom Gerät empfangen und ans Gerät geschickt, aus Sicht des Servers */
    val rx: Long,
    val tx: Long,
)

data class WgOverview(
    val configured: Boolean,
    val error: String,
    val clients: List<WgClient>,
    val version: String,
    val update: Boolean,
    val latest: String,
)

/** WireGuard-Verwaltung (WireGuard-Easy), nur über den gemeinsamen Zugang erreichbar. */
class WireGuardApi(private val http: PiApi) {

    suspend fun overview(): WgOverview {
        val o = JSONObject(http.raw("api/overview"))
        return WgOverview(
            configured = o.optBoolean("configured"),
            error = o.optString("error"),
            clients = o.optJSONArray("clients").objects().map {
                WgClient(
                    id = it.optInt("id"),
                    name = it.optString("name"),
                    enabled = it.optBoolean("enabled"),
                    address = it.optString("address"),
                    created = it.optLong("created"),
                    expires = it.optLong("expires"),
                    expired = it.optBoolean("expired"),
                    handshake = it.optLong("handshake"),
                    online = it.optBoolean("online"),
                    rx = it.optLong("rx"),
                    tx = it.optLong("tx"),
                )
            },
            version = o.optString("version"),
            update = o.optBoolean("update"),
            latest = o.optString("latest"),
        )
    }

    /** Legt einen Zugang an und gibt seine Nummer zurück. */
    suspend fun create(name: String): Int =
        JSONObject(http.postJson("api/clients", JSONObject().put("name", name.trim()).toString())).optInt("id")

    suspend fun setEnabled(id: Int, enabled: Boolean) {
        http.putJson("api/clients/$id", JSONObject().put("enabled", enabled).toString())
    }

    suspend fun delete(id: Int) { http.delete("api/clients/$id") }

    /** Die Konfiguration mit dem geheimen Schlüssel, für den QR-Code. */
    suspend fun config(id: Int): String = JSONObject(http.raw("api/clients/$id/config")).optString("config")

    private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
}
