#!/usr/bin/env python3
"""Preisalarm in Home-Net: beobachtet Preise von Produkten über lange Zeit und meldet gute Preise ans Handy.

Gut ist ein Preis, wenn er den Zielpreis erreicht, deutlich unter dem bisherigen Durchschnitt liegt oder ein neuer
Tiefstpreis ist. Dazu durchsucht das Modul einen Feed mit Schnäppchen (Standard: mydealz) nach Suchbegriffen.
Nur über den Zugang erreichbar (Socket-Datei). Ansehen darf jeder mit Zugriff, Produkte ändern nur Eigentümer und Admins.
"""
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import preise  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_PRODUCTS = 50
MAX_SOURCES = 8  # product pages per product; the cheapest counts
MAX_HISTORY = 2000  # measurements kept per product
DEFAULTS = {"interval_hours": 6, "feed": "https://www.mydealz.de/rss/neu", "feed_on": True}


def log(text):
    print(text, file=sys.stderr, flush=True)


def spool_event(kind, title, message, link="", priority="default"):
    if not EVENTS_DIR:
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=EVENTS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"type": kind, "title": title, "message": message, "priority": priority, "category": "",
                       "link": link}, f, ensure_ascii=False)
        os.rename(tmp, tmp[:-4] + ".json")
    except OSError as exc:
        log(f"Meldung nicht abgelegt: {exc}")


def euro(value):
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def clean_url(value, required=False):
    url = str(value or "").strip()
    if not url:
        if required:
            raise ValueError("Bitte eine Adresse angeben.")
        return ""
    if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        raise ValueError(f"Die Adresse „{url[:80]}“ sieht nicht richtig aus, z. B. https://geizhals.de/…")
    return url[:500]


def host(url):
    name = urllib.parse.urlsplit(url).hostname or url
    return name[4:] if name.startswith("www.") else name


def urls_of(item):
    """Product pages of a product; state from before several sources had only "url"."""
    if item.get("urls"):
        return list(item["urls"])
    return [item["url"]] if item.get("url") else []


def clean_price(value):
    if value in (None, ""):
        return None
    try:
        price = preise.to_number(value) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError("Der Zielpreis muss eine Zahl sein, z. B. 49,90.")
    if not 0 < price < 1_000_000:
        raise ValueError("Der Zielpreis muss größer als 0 sein.")
    return round(price, 2)


class Store:
    """Products, their price history and what was reported, in state.json (one file, written atomically)."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "state.json")
        self.lock = threading.RLock()
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.data = {"products": [], "settings": dict(DEFAULTS), "seen": [], "last_run": None, **data}
        self.data["settings"] = {**DEFAULTS, **self.data["settings"]}

    def save(self):
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)

    def product(self, pid):
        for p in self.data["products"]:
            if p["id"] == pid:
                return p
        raise LookupError(pid)

    @staticmethod
    def clean(body, old=None):
        old = old or {}
        name = str(body.get("name", old.get("name", ""))).strip()[:120]
        if not name:
            raise ValueError("Bitte einen Namen angeben.")
        if "urls" in body:
            raw = body["urls"]
        elif "url" in body:
            raw = [body["url"]]
        else:
            raw = urls_of(old)
        if isinstance(raw, str):
            raw = re.split(r"[\s;]+", raw)
        if not isinstance(raw, list):
            raise ValueError("Die Produktseiten fehlen.")
        urls = []
        for value in raw:
            url = clean_url(value)
            if url and url not in urls:
                urls.append(url)
        if len(urls) > MAX_SOURCES:
            raise ValueError(f"Bitte bis zu {MAX_SOURCES} Produktseiten angeben.")
        terms = body.get("terms", old.get("terms", []))
        if isinstance(terms, str):
            terms = [t for t in re.split(r"[\n;]", terms)]
        terms = [str(t).strip()[:80] for t in terms if str(t).strip()][:10]
        if not urls and not terms:
            raise ValueError("Bitte eine Produktseite oder mindestens einen Suchbegriff angeben.")
        try:
            below = int(body.get("below_pct", old.get("below_pct", 15)) or 0)
        except (TypeError, ValueError):
            raise ValueError("„Unter Durchschnitt“ muss eine ganze Zahl sein.")
        if not 0 <= below <= 90:
            raise ValueError("„Unter Durchschnitt“ muss zwischen 0 und 90 Prozent liegen.")
        return {"name": name, "urls": urls, "url": urls[0] if urls else "", "terms": terms, "target": clean_price(body.get("target", old.get("target"))),
                "below_pct": below}

    def add(self, body):
        with self.lock:
            if len(self.data["products"]) >= MAX_PRODUCTS:
                raise ValueError(f"Es gehen bis zu {MAX_PRODUCTS} Produkte.")
            item = dict(self.clean(body), id=uuid.uuid4().hex[:12], history=[], error="", reported=None)
            self.data["products"].append(item)
            self.save()
            return item

    def change(self, pid, body):
        with self.lock:
            item = self.product(pid)
            new = self.clean(body, item)
            if not set(new["urls"]) & set(urls_of(item)):  # all pages new: old prices do not compare
                item.update(history=[], reported=None, error="", sources=[])
            else:
                kept = set(new["urls"])
                item["sources"] = [x for x in item.get("sources", []) if x.get("url") in kept]
            if new["target"] != item.get("target"):
                item["reported"] = None
            item.update(new)
            self.save()
            return item

    def remove(self, pid):
        with self.lock:
            self.data["products"].remove(self.product(pid))
            self.save()

    def settings(self, body=None):
        with self.lock:
            s = self.data["settings"]
            if body:
                if "interval_hours" in body:
                    try:
                        hours = int(body["interval_hours"])
                    except (TypeError, ValueError):
                        raise ValueError("Bitte alle 1 bis 48 Stunden angeben.")
                    if not 1 <= hours <= 48:
                        raise ValueError("Bitte alle 1 bis 48 Stunden angeben.")
                    s["interval_hours"] = hours
                if "feed" in body:
                    s["feed"] = clean_url(body["feed"])
                if "feed_on" in body:
                    s["feed_on"] = bool(body["feed_on"])
                self.save()
            return dict(s)


def view(item):
    out = {k: item.get(k) for k in ("id", "name", "url", "terms", "target", "below_pct", "error")}
    out["urls"] = urls_of(item)
    out["url"] = out["urls"][0] if out["urls"] else ""
    out["sources"] = [dict(s, host=host(s["url"])) for s in item.get("sources", [])]
    out["source"] = item.get("source", "")
    out["source_host"] = host(out["source"]) if out["source"] else ""
    history = item.get("history", [])
    out["history"] = history[-200:]
    out["price"] = history[-1][1] if history else None
    out["checked"] = history[-1][0] if history else None
    out.update(preise.summary(history))
    out["good"] = preise.reasons(out["price"], history[:-1], item.get("target"), item.get("below_pct", 15)) \
        if history else []
    return out


class Checker:
    """Runs the checks: every product page, then the deal feed. Reports each good price once."""

    def __init__(self, store, fetch=preise.fetch):
        self.store = store
        self.fetch = fetch
        self.running = threading.Lock()

    def check_product(self, item, now):
        urls = urls_of(item)
        if not urls:
            return
        sources = []
        for url in urls:
            try:
                sources.append({"url": url, "price": preise.parse_price(self.fetch(url)), "error": ""})
            except preise.PriceError as exc:
                sources.append({"url": url, "price": None, "error": str(exc)})
        item["sources"] = sources
        found = [s for s in sources if s["price"] is not None]
        if not found:
            item["error"] = sources[0]["error"] if len(sources) == 1 else "Keine der Produktseiten hat einen Preis geliefert."
            return
        item["error"] = ""
        best = min(found, key=lambda s: s["price"])
        price = best["price"]
        item["source"] = best["url"]
        history = item.setdefault("history", [])
        good = preise.reasons(price, history, item.get("target"), item.get("below_pct", 15))
        history.append([int(now), price])
        del history[:-MAX_HISTORY]
        report, item["reported"] = preise.should_report(price, good, item.get("reported"))
        if report:
            info = preise.summary(history[:-1])
            text = f"{euro(price)} bei {host(best['url'])}: " + ", ".join(good) + "."
            if info["average"]:
                text += f" Durchschnitt {euro(info['average'])}, bisher günstigster {euro(info['lowest'])}."
            spool_event("preisalarm_preis", f"Guter Preis: {item['name']}", text, best["url"], "high")

    def check_feed(self):
        s = self.store.data["settings"]
        products = [p for p in self.store.data["products"] if p.get("terms")]
        if not (s.get("feed_on") and s.get("feed") and products):
            return
        try:
            entries = preise.parse_feed(self.fetch(s["feed"]))
        except preise.PriceError as exc:
            log(f"Feed: {exc}")
            return
        seen = set(self.store.data.get("seen", []))
        first = not seen  # the first read only notes what is there, so old deals are not reported
        for entry in entries:
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            if first:
                continue
            for p in products:
                if preise.matches(entry["title"], p["terms"]):
                    spool_event("preisalarm_deal", f"Deal zu {p['name']}", entry["title"][:300], entry["link"])
                    break
        self.store.data["seen"] = list(seen)[-1000:]

    def run(self, now=None):
        if not self.running.acquire(blocking=False):
            raise ValueError("Die Prüfung läuft gerade schon.")
        try:
            now = now or time.time()
            for item in list(self.store.data["products"]):
                with self.store.lock:
                    self.check_product(item, now)
            with self.store.lock:
                self.check_feed()
                self.store.data["last_run"] = int(now)
                self.store.save()
        finally:
            self.running.release()


def loop(checker):
    time.sleep(30)
    while True:
        try:
            last = checker.store.data.get("last_run") or 0
            every = checker.store.data["settings"]["interval_hours"] * 3600
            if time.time() - last >= every:
                checker.run()
        except Exception as exc:  # noqa: BLE001 – the loop must keep running
            log(f"Prüfung: {exc}")
        time.sleep(60)


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store = None
    checker = None

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{fmt % args}\n")

    def send_json(self, code, payload):
        self.send_body(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_body(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 64 * 1024)) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def manager(self):
        return self.headers.get("X-Home-Net-Role", "owner") in ("owner", "admin")

    def require_manager(self):
        if not self.manager():
            raise PermissionError("Produkte ändern dürfen nur Eigentümer und Admins von Home-Net.")

    def route(self):
        split = urllib.parse.urlsplit(self.path)
        body = self.read_body() if self.command in ("POST", "PUT", "DELETE") else {}
        try:
            self.dispatch(self.command, split.path, body)
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"detail": str(exc)})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def overview(self):
        d = self.store.data
        return {"products": [view(p) for p in d["products"]], "settings": dict(d["settings"]),
                "last_run": d.get("last_run"), "manager": self.manager()}

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/overview":
            return self.send_json(200, self.overview())
        if method == "GET" and path == "/api/tile":
            items = [view(p) for p in self.store.data["products"]]
            if not items:
                return self.send_json(200, {"text": "Noch kein Produkt eingetragen", "alert": False})
            good = sum(1 for i in items if i["good"])
            if good:
                return self.send_json(200, {"text": f"{good} von {len(items)} gerade günstig", "alert": True})
            text = "1 Produkt im Blick" if len(items) == 1 else f"{len(items)} Produkte im Blick"
            return self.send_json(200, {"text": text, "alert": False})
        if path == "/api/products" and method == "POST":
            self.require_manager()
            return self.send_json(201, view(self.store.add(body)))
        match = re.fullmatch(r"/api/products/([0-9a-f]{12})", path)
        if match and method == "PUT":
            self.require_manager()
            return self.send_json(200, view(self.store.change(match.group(1), body)))
        if match and method == "DELETE":
            self.require_manager()
            self.store.remove(match.group(1))
            return self.send_json(200, {"ok": True})
        if path == "/api/settings" and method == "PUT":
            self.require_manager()
            self.store.settings(body)
            return self.send_json(200, self.overview())
        if path == "/api/check" and method == "POST":
            self.require_manager()
            self.checker.run()
            return self.send_json(200, self.overview())
        raise LookupError


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.store = Store(DATA)
    ApiHandler.checker = Checker(ApiHandler.store)
    threading.Thread(target=loop, args=(ApiHandler.checker,), daemon=True).start()
    if API_PORT:
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", API_PORT), ApiHandler).serve_forever,
                         daemon=True).start()
    if LISTEN_SOCKET:
        try:
            os.unlink(LISTEN_SOCKET)
        except OSError:
            pass
        server = UnixHTTPServer(LISTEN_SOCKET, ApiHandler)
        os.chmod(LISTEN_SOCKET, 0o666)
        server.serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
