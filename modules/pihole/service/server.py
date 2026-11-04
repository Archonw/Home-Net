#!/usr/bin/env python3
"""Pi-hole in Home-Net: Werbefilter kurz pausieren und Statistiken ansehen, für alle eingetragenen Pi-holes zugleich.

Nur über den Zugang erreichbar (Socket-Datei). Ansehen und Pausieren darf jeder, dem der Zugang dieses Modul freigibt;
die Pi-holes und ihre Passwörter eintragen dürfen nur Eigentümer und Admins von Home-Net.
"""
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pihole import Piholes, PiholeError  # noqa: E402
from updater import Updater  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
CHECK_EVERY = 60  # seconds between checks of the Pi-holes
OFFLINE_AFTER = 3  # failed checks in a row before "not reachable" is reported
UPDATE_EVERY = 6 * 3600
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_INSTANCES = 8
MAX_PAUSE = 24 * 3600


def log(text):
    print(text, file=sys.stderr, flush=True)


def clean_url(value):
    url = str(value or "").strip().rstrip("/")
    if url.endswith("/admin"):
        url = url[:-6]
    if url and not re.match(r"https?://", url):
        url = "http://" + url
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+(/[^\s]*)?", url):
        raise ValueError(f"Die Adresse „{value}“ sieht nicht richtig aus, z. B. http://192.168.1.2")
    return url


class Settings:
    """The Pi-holes with their passwords, kept in settings.json (readable only by the service)."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = {"instances": [], **json.load(f)}
        except (OSError, ValueError):
            self.data = {"instances": []}

    def get(self):
        return self.data

    def public(self):
        return {"instances": [{"name": i.get("name", ""), "url": i["url"], "password_set": bool(i.get("password")),
                               "ssh_user": i.get("ssh_user") or "root", "ssh_port": int(i.get("ssh_port") or 22)}
                              for i in self.data["instances"]]}

    def update(self, changes):
        items = changes.get("instances")
        if not isinstance(items, list) or len(items) > MAX_INSTANCES:
            raise ValueError(f"Bitte bis zu {MAX_INSTANCES} Pi-holes angeben.")
        with self.lock:
            old = {i["url"]: i for i in self.data["instances"]}
            out, seen = [], set()
            for n, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError("Die Angaben zu einem Pi-hole fehlen.")
                url = clean_url(item.get("url"))
                if url in seen:
                    raise ValueError(f"{url} steht doppelt in der Liste.")
                seen.add(url)
                name = str(item.get("name") or "").strip()[:40] or f"Pi-hole {n + 1}"
                password = item.get("password")
                if password is None:  # not touched: keep the one already saved
                    password = old.get(url, {}).get("password", "")
                password = str(password)
                if len(password) > 256 or "\n" in password:
                    raise ValueError("Das Passwort sieht nicht richtig aus.")
                ssh_user = str(item.get("ssh_user") or old.get(url, {}).get("ssh_user") or "root").strip()
                if not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", ssh_user):
                    raise ValueError(f"Der SSH-Benutzer „{ssh_user}“ sieht nicht richtig aus.")
                try:
                    ssh_port = int(item.get("ssh_port") or old.get(url, {}).get("ssh_port") or 22)
                except (TypeError, ValueError):
                    raise ValueError("Der SSH-Port muss eine Zahl sein.")
                if not 1 <= ssh_port <= 65535:
                    raise ValueError("Der SSH-Port muss zwischen 1 und 65535 liegen.")
                out.append({"name": name, "url": url, "password": password, "ssh_user": ssh_user, "ssh_port": ssh_port})
            data = {**self.data, "instances": out}
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.data = data


# --- Meldungen an die App ------------------------------------------------------------------------

def spool_event(kind, title, message, priority="default"):
    if not EVENTS_DIR:
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=EVENTS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"type": kind, "title": title, "message": message, "priority": priority, "category": "",
                       "link": ""}, f, ensure_ascii=False)
        os.rename(tmp, tmp[:-4] + ".json")
    except OSError as exc:
        log(f"Meldung nicht abgelegt: {exc}")


class Watcher:
    """Checks the Pi-holes regularly: reports one that stays unreachable, its return, and new Pi-hole versions.

    Every report is made once; what was reported is kept in watch.json, so a restart repeats nothing.
    """

    def __init__(self, piholes, folder):
        self.piholes = piholes
        self.path = os.path.join(folder, "watch.json")
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.down = set(data.get("down", []))  # urls reported as unreachable
        self.updates = set(data.get("updates", []))  # "url|part|version" already reported
        self.fails = {}
        self.last_update_check = 0

    def save(self):
        try:
            with open(self.path + ".tmp", "w") as f:
                json.dump({"down": sorted(self.down), "updates": sorted(self.updates)}, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError as exc:
            log(f"Stand der Überwachung nicht gespeichert: {exc}")

    def tick(self, now=None):
        now = now or time.time()
        rows = self.piholes.overview()["instances"]
        changed = False
        urls = {r["url"] for r in rows}
        for row in rows:
            url, name = row["url"], row["name"]
            if not row["online"]:
                self.fails[url] = self.fails.get(url, 0) + 1
                if self.fails[url] >= OFFLINE_AFTER and url not in self.down:
                    self.down.add(url)
                    changed = True
                    spool_event("pihole_offline", f"{name} ist nicht erreichbar",
                                f"{row['error']} Der Werbefilter läuft weiter über die anderen Pi-holes, "
                                "falls sie die gemeinsame Adresse übernehmen.", "high")
                continue
            self.fails[url] = 0
            if url in self.down:
                self.down.discard(url)
                changed = True
                spool_event("pihole_online", f"{name} ist wieder erreichbar", "Es antwortet wieder normal.")
        if self.down - urls:  # removed from the settings
            self.down &= urls
            changed = True
        if now - self.last_update_check >= UPDATE_EVERY:
            self.last_update_check = now
            for row in rows:
                for u in row.get("updates") or []:
                    key = f"{row['url']}|{u['part']}|{u['available']}"
                    if key not in self.updates:
                        self.updates.add(key)
                        changed = True
                        spool_event("pihole_update", f"Update für {row['name']}",
                                    f"Pi-hole {u['part']} {u['available']} ist erschienen, installiert ist "
                                    f"{u['installed']}. Einspielen auf dem Gerät mit „pihole -up“.")
        if changed:
            self.save()


def watch(watcher):
    while True:
        time.sleep(CHECK_EVERY)
        try:
            if watcher.piholes.settings().get("instances"):
                watcher.tick()
        except Exception as exc:  # noqa: BLE001 – the watcher must keep running
            log(f"Überwachung: {exc}")


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    settings = None
    piholes = None
    updater = None

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.headers.get('X-Forwarded-For', 'socket') if self.headers else 'socket'} {fmt % args}\n")

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

    def role(self):
        return self.headers.get("X-Home-Net-Role", "owner")

    def actor(self):
        return self.headers.get("X-Home-Net-User", "?")

    def manager(self):
        return self.role() in ("owner", "admin")

    def route(self):
        split = urllib.parse.urlsplit(self.path)
        body = self.read_body() if self.command in ("POST", "PUT", "DELETE") else {}
        try:
            self.dispatch(self.command, split.path, body)
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"detail": str(exc) or "Das darf nur ein Admin"})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})
        except PiholeError as exc:
            self.send_json(exc.status, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        configured = bool(self.settings.get()["instances"])

        if path == "/api/settings":
            if not self.manager():
                raise PermissionError("Die Pi-holes eintragen dürfen nur Eigentümer und Admins von Home-Net.")
            if method == "PUT":
                self.settings.update(body)
                log(f"{self.actor()}: Pi-holes geändert ({len(self.settings.get()['instances'])})")
            elif method != "GET":
                raise LookupError
            return self.send_json(200, dict(self.settings.public(), key_line=self.updater.key_line() if self.updater else ""))
        if method == "GET" and path == "/api/overview":
            if not configured:
                return self.send_json(200, {"configured": False, "manager": self.manager(), "instances": [],
                                            "blocking": "unknown"})
            return self.send_json(200, dict(self.piholes.overview(), configured=True, manager=self.manager()))
        if method == "GET" and path == "/api/stats":
            if not configured:
                return self.send_json(200, {"configured": False})
            return self.send_json(200, dict(self.piholes.stats(), configured=True))
        match = re.fullmatch(r"/api/instances/(\d{1,2})", path)
        if method == "GET" and match:
            details = self.piholes.details(int(match.group(1)))
            details["update_job"] = self.updater.status(details["url"]) if self.updater else None
            details["manager"] = self.manager()
            return self.send_json(200, details)
        match = re.fullmatch(r"/api/(?:instances/(\d{1,2})/)?update", path)
        if method == "GET" and match:
            items = self.settings.get()["instances"]
            return self.send_json(200, {"jobs": [dict(self.updater.status(i["url"]) or {"state": "", "log": []},
                                                      name=i["name"]) for i in items]})
        if method == "POST" and match:
            if not self.manager():
                raise PermissionError("Aktualisieren dürfen nur Eigentümer und Admins von Home-Net.")
            items = self.settings.get()["instances"]
            if match.group(1) is not None:
                n = int(match.group(1))
                if not 0 <= n < len(items):
                    raise LookupError
                items = [items[n]]
            if not items:
                raise ValueError("Es ist noch kein Pi-hole eingetragen.")
            try:
                self.updater.start(items, on_done=self.update_done)
            except RuntimeError as exc:
                raise ValueError(str(exc))
            log(f"{self.actor()}: Update gestartet für " + ", ".join(i["name"] for i in items))
            return self.send_json(202, {"started": [i["name"] for i in items]})
        if method == "POST" and path in ("/api/pause", "/api/resume"):
            if not configured:
                raise ValueError("Es ist noch kein Pi-hole eingetragen.")
            if path == "/api/resume":
                result = self.piholes.set_blocking(True)
                log(f"{self.actor()}: Werbefilter wieder an")
            else:
                seconds = body.get("seconds")
                if seconds is not None:
                    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= MAX_PAUSE:
                        raise ValueError("Bitte eine Dauer zwischen 1 Sekunde und 24 Stunden angeben.")
                elif not self.manager():
                    raise PermissionError("Ohne Ende pausieren dürfen nur Eigentümer und Admins.")
                result = self.piholes.set_blocking(False, seconds)
                log(f"{self.actor()}: Werbefilter pausiert " + (f"für {seconds} s" if seconds else "ohne Ende"))
            failed = [r for r in result["instances"] if not r["ok"]]
            if failed and len(failed) == len(result["instances"]):
                raise PiholeError("Kein Pi-hole hat reagiert: " + " ".join(r["error"] for r in failed))
            return self.send_json(200, result)
        raise LookupError

    @staticmethod
    def update_done(inst, ok, lines):
        log(f"Update {inst['name']}: {'fertig' if ok else 'fehlgeschlagen'}")
        last = next((ln for ln in reversed(lines) if ln), "")
        spool_event("pihole_updated" if ok else "pihole_update_failed",
                    f"{inst['name']}: Update {'fertig' if ok else 'fehlgeschlagen'}", last[:300],
                    "default" if ok else "high")


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.settings = Settings(DATA)
    ApiHandler.piholes = Piholes(ApiHandler.settings.get)
    ApiHandler.updater = Updater(DATA)
    try:
        ApiHandler.updater.ensure_key()
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"SSH-Schlüssel nicht angelegt: {exc}")
    if EVENTS_DIR:
        threading.Thread(target=watch, args=(Watcher(ApiHandler.piholes, DATA),), daemon=True).start()
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
