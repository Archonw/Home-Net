import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import preise  # noqa: E402
import server  # noqa: E402

GEIZHALS = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Einhell PXC Starter Kit",
 "offers":{"@type":"AggregateOffer","lowPrice":"64.99","highPrice":"89.00","priceCurrency":"EUR","offerCount":27}}
</script></head><body>ab € 64,99</body></html>"""
SHOP = '<div><meta itemprop="price" content="1.299,00"><span>1.299,00 €</span></div>'
GRAPH = '<script type="application/ld+json">{"@graph":[{"@type":"Product","offers":[{"price":79.5},{"price":"72,10"}]}]}</script>'
FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Einhell PXC Starter Kit 2x 4,0 Ah + Twincharger für 49,99 €</title><link>https://example.de/deal/1</link><guid>d1</guid></item>
<item><title>Kaffeemaschine für 20 €</title><link>https://example.de/deal/2</link><guid>d2</guid></item>
</channel></rss>"""


class Parsing(unittest.TestCase):
    def test_prices_from_structured_data(self):
        self.assertEqual(preise.parse_price(GEIZHALS), 64.99)
        self.assertEqual(preise.parse_price(SHOP), 1299.0)
        self.assertEqual(preise.parse_price(GRAPH), 72.1)
        with self.assertRaises(preise.PriceError):
            preise.parse_price("<html>kein Preis</html>")

    def test_numbers(self):
        for text, value in (("1.234,56", 1234.56), ("1,234.56", 1234.56), ("89,90 €", 89.9), ("12", 12.0)):
            self.assertEqual(preise.to_number(text), value)

    def test_feed_and_terms(self):
        items = preise.parse_feed(FEED)
        self.assertEqual([i["id"] for i in items], ["d1", "d2"])
        self.assertTrue(preise.matches(items[0]["title"], ["einhell 4,0 ah"]))
        self.assertFalse(preise.matches(items[1]["title"], ["einhell 4,0 ah"]))


class Rules(unittest.TestCase):
    hist = [[i, p] for i, p in enumerate([80, 82, 78, 81, 79])]

    def test_target_average_and_low(self):
        self.assertEqual(preise.reasons(49, [], target=50), ["Zielpreis erreicht"])
        self.assertEqual(preise.reasons(66, self.hist[:4]), [])  # too few measurements for the average
        self.assertEqual(preise.reasons(66, self.hist), ["deutlich unter Durchschnitt", "neuer Tiefstpreis"])
        self.assertEqual(preise.reasons(77, self.hist), ["neuer Tiefstpreis"])
        self.assertEqual(preise.reasons(77, self.hist, below_pct=0), ["neuer Tiefstpreis"])
        self.assertEqual(preise.reasons(80, self.hist, target=50), [])

    def test_report_once_then_only_lower(self):
        self.assertEqual(preise.should_report(49, ["x"], None), (True, 49))
        self.assertEqual(preise.should_report(49, ["x"], 49), (False, 49))
        self.assertEqual(preise.should_report(45, ["x"], 49), (True, 45))
        self.assertEqual(preise.should_report(60, [], 45), (False, None))


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = Path(self.tmp.name, "events")
        self.events.mkdir()
        server.EVENTS_DIR = str(self.events)
        self.pages = {"https://geizhals.example.de/kit": GEIZHALS, server.DEFAULTS["feed"]: FEED}
        store = server.Store(self.tmp.name)
        handler = type("Api", (server.ApiHandler,), {"store": store,
                                                     "checker": server.Checker(store, fetch=self.pages.__getitem__)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, method="GET", body=None, role="owner"):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"X-Home-Net-Role": role, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def events_sent(self):
        return [json.loads(p.read_text()) for p in sorted(self.events.glob("*.json"))]

    def test_products_checks_and_reports(self):
        self.assertEqual(self.call("/api/tile")[1]["text"], "Noch kein Produkt eingetragen")
        code, p = self.call("/api/products", "POST", {"name": "Einhell Kit", "url": "https://geizhals.example.de/kit",
                                                      "target": "65,00", "terms": "Einhell 4,0 Ah"})
        self.assertEqual((code, p["target"], p["terms"]), (201, 65.0, ["Einhell 4,0 Ah"]))
        # Users only look
        self.assertEqual(self.call("/api/products", "POST", {"name": "x", "url": "https://a.de"}, role="user")[0], 403)
        self.assertEqual(self.call("/api/check", "POST", role="user")[0], 403)

        data = self.call("/api/check", "POST")[1]
        item = data["products"][0]
        self.assertEqual((item["price"], item["count"], item["good"], item["error"]), (64.99, 1, ["Zielpreis erreicht"], ""))
        events = self.events_sent()
        self.assertEqual([e["type"] for e in events], ["preisalarm_preis"])  # first read of the feed reports nothing
        self.assertIn("Zielpreis erreicht", events[0]["message"])
        self.assertEqual(events[0]["link"], "https://geizhals.example.de/kit")

        self.call("/api/check", "POST")  # same price: no second report
        self.assertEqual(len(self.events_sent()), 1)

        self.pages[server.DEFAULTS["feed"]] = FEED.replace("d1", "d3")
        self.call("/api/check", "POST")
        self.assertEqual([e["type"] for e in self.events_sent()].count("preisalarm_deal"), 1)

        tile = self.call("/api/tile")[1]
        self.assertEqual(tile, {"text": "1 von 1 gerade günstig", "alert": True})
        self.call(f"/api/products/{p['id']}", "PUT", {"target": ""})
        self.assertEqual(self.call("/api/tile")[1], {"text": "1 Produkt im Blick", "alert": False})

    def test_errors_are_shown_and_validation(self):
        self.pages["https://shop.example.de/x"] = "<html></html>"
        pid = self.call("/api/products", "POST", {"name": "X", "url": "https://shop.example.de/x"})[1]["id"]
        item = self.call("/api/check", "POST")[1]["products"][0]
        self.assertIn("kein lesbarer Preis", item["error"])
        self.assertEqual(self.call("/api/products", "POST", {"name": "Y"})[0], 400)
        self.assertEqual(self.call("/api/products", "POST", {"name": "Y", "url": "ftp://x"})[0], 400)
        self.assertEqual(self.call(f"/api/products/{pid}", "PUT", {"target": "abc"})[0], 400)
        self.assertEqual(self.call("/api/settings", "PUT", {"interval_hours": 0})[0], 400)
        self.assertEqual(self.call(f"/api/products/{pid}", "DELETE")[0], 200)
        self.assertEqual(self.call(f"/api/products/{pid}", "DELETE")[0], 404)

    def test_state_survives_restart(self):
        self.call("/api/products", "POST", {"name": "Einhell Kit", "url": "https://geizhals.example.de/kit"})
        self.call("/api/check", "POST")
        again = server.Store(self.tmp.name)
        self.assertEqual(again.data["products"][0]["history"][0][1], 64.99)


if __name__ == "__main__":
    unittest.main()
