package de.archonw.netmon

import org.json.JSONArray
import org.json.JSONObject

/** Eine Produktseite eines Produkts beim letzten Prüfen. [price] null: gesperrt oder kein Preis, dann steht [error] dabei. */
data class PaSource(val url: String, val host: String, val price: Double?, val error: String)

/** Ein beobachtetes Produkt mit Preisverlauf. [good]: warum der aktuelle Preis gut ist (leer: nicht besonders). */
data class PaProduct(
    val id: String,
    val name: String,
    val url: String,
    /** Alle Produktseiten; der günstigste Preis zählt. Ältere Dienste kennen nur [url]. */
    val urls: List<String>,
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
    val sources: List<PaSource> = emptyList(),
    /** Seite mit dem günstigsten Preis, leer: noch nicht geprüft */
    val source: String = "",
    val sourceHost: String = "",
)

data class PaOverview(val products: List<PaProduct>, val lastRun: Long?, val manager: Boolean)

/** Preisalarm, nur über den gemeinsamen Zugang erreichbar. Ändern dürfen nur Eigentümer und Admins. */
class PreisalarmApi(private val http: PiApi) {

    suspend fun overview(): PaOverview = parse(http.raw("api/overview"))

    /** Prüft alle Produkte sofort und liefert den neuen Stand. */
    suspend fun check(): PaOverview = parse(http.postJson("api/check", "{}"))

    /** [target] und [terms] wie eingetippt; der Dienst prüft sie und antwortet mit einer Fehlermeldung. */
    suspend fun save(id: String?, name: String, urls: String, target: String, belowPct: String, terms: String) {
        // "url" mit der ersten Seite für Dienste vor v1.3.0, die nur eine Seite kennen
        val first = urls.lines().map { it.trim() }.firstOrNull { it.isNotEmpty() }.orEmpty()
        val body = JSONObject().put("name", name).put("urls", urls).put("url", first).put("target", target)
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
            urls = o.optJSONArray("urls").strings().ifEmpty { listOfNotNull(o.optString("url").ifBlank { null }) },
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
            sources = o.optJSONArray("sources").objects().map {
                PaSource(it.optString("url"), it.optString("host"), it.number("price"), it.optString("error"))
            },
            source = o.optString("source"),
            sourceHost = o.optString("source_host"),
        )

        private fun JSONObject.number(key: String) = if (isNull(key) || !has(key)) null else optDouble(key)
        private fun JSONArray?.strings() = if (this == null) emptyList() else (0 until length()).map { getString(it) }
        private fun JSONArray?.objects() = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
    }
}
