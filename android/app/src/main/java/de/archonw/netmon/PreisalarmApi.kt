package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

/** Ein beobachtetes Produkt mit Preisverlauf. [good]: warum der aktuelle Preis gut ist (leer: nicht besonders). */
data class PaProduct(
    val id: String,
    val name: String,
    val url: String,
    val terms: List<String>,
    /** null: kein Zielpreis */
    val target: Double?,
    val belowPct: Int,
    val error: String,
    /** Zeit (Sekunden) und Preis, älteste zuerst */
    val history: List<Pair<Long, Double>>,
    val price: Double?,
    val checked: Long?,
    val count: Int,
    val average: Double?,
    val lowest: Double?,
    val good: List<String>,
)

data class PaOverview(val products: List<PaProduct>, val lastRun: Long?, val manager: Boolean)

/** Preisalarm, nur über den gemeinsamen Zugang erreichbar. Ändern dürfen nur Eigentümer und Admins. */
class PreisalarmApi(private val http: PiApi) {

    suspend fun overview(): PaOverview = parse(http.raw("api/overview"))

    /** Prüft alle Produkte sofort und liefert den neuen Stand. */
    suspend fun check(): PaOverview = parse(http.postJson("api/check", "{}"))

    /** [target] und [terms] wie eingetippt; der Dienst prüft sie und antwortet mit einer Fehlermeldung. */
    suspend fun save(id: String?, name: String, url: String, target: String, belowPct: String, terms: String) {
        val body = JSONObject().put("name", name).put("url", url).put("target", target)
            .put("below_pct", belowPct.trim().ifBlank { "0" }).put("terms", terms).toString()
        if (id == null) http.postJson("api/products", body) else http.putJson("api/products/$id", body)
    }

    suspend fun remove(id: String) {
        http.delete("api/products/$id")
    }

    companion object {
        fun parse(raw: String): PaOverview {
            val o = JSONObject(raw)
            return PaOverview(
                products = o.optJSONArray("products").objects().map { product(it) },
                lastRun = if (o.isNull("last_run") || !o.has("last_run")) null else o.optLong("last_run"),
                manager = o.optBoolean("manager"),
            )
        }

        private fun product(o: JSONObject) = PaProduct(
            id = o.optString("id"),
            name = o.optString("name"),
            url = o.optString("url"),
            terms = o.optJSONArray("terms").strings(),
            target = o.number("target"),
            belowPct = o.optInt("below_pct", 15),
            error = o.optString("error"),
            history = o.optJSONArray("history").let { a ->
                if (a == null) emptyList() else (0 until a.length()).mapNotNull { n ->
                    a.optJSONArray(n)?.let { it.optLong(0) to it.optDouble(1) }
                }
            },
            price = o.number("price"),
            checked = if (o.isNull("checked") || !o.has("checked")) null else o.optLong("checked"),
            count = o.optInt("count"),
            average = o.number("average"),
            lowest = o.number("lowest"),
            good = o.optJSONArray("good").strings(),
        )

        private fun JSONObject.number(key: String) = if (isNull(key) || !has(key)) null else optDouble(key)
        private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
        private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
    }
}
