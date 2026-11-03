#!/usr/bin/env python3
"""Proxy-Manager-Übersicht: welche Adressen Nginx Proxy Manager nach außen zeigt, welche nur intern sind und wann die
Zertifikate ablaufen.

Nur über den Zugang erreichbar (Socket-Datei). Alles darin dürfen nur Eigentümer und Admins von Home-Net sehen, denn die
Liste zeigt, was von außen erreichbar ist. Ein Wächter prüft regelmäßig und meldet bald ablaufende Zertifikate.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npm import WARN_DAYS, Npm, NpmError  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
DEFAULT_URL = os.environ.get("NPM_URL", "http://host.docker.internal:81")
WATCH_EVERY = int(os.environ.get("WATCH_EVERY", "1800"))
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
LINK = "/m/proxy-manager/"


def log(text):
    print(text, file=sys.stderr, flush=True)


def write_private(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def spool_event(kind, title, message, priority="default"):
    if not EVENTS_DIR:
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=EVENTS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"type": kind, "title": title, "message": message, "priority": priority, "link": LINK}, f,
                      ensure_ascii=False)
        os.rename(tmp, tmp[:-4] + ".json")
    except OSError as exc:
        log(f"Meldung nicht abgelegt: {exc}")


class Settings:
    """Address of the admin API and the NPM user Home-Net signs in with."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = {"url": "", "email": "", "password": "", **json.load(f)}
        except (OSError, ValueError):
            self.data = {"url": "", "email": "", "password": ""}

    def get(self):
        return dict(self.data, url=self.data["url"] or DEFAULT_URL)

    def update(self, changes):
        with self.lock:
            data = dict(self.data)
            if changes.get("url") is not None:
                url = str(changes["url"]).strip().rstrip("/")
                if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
                    raise ValueError("Die Adresse sieht nicht richtig aus, z. B. http://192.168.1.10:81")
                data["url"] = url
            if changes.get("email") is not None:
                email = str(changes["email"]).strip()
                if len(email) > 200 or (email and "@" not in email):
                    raise ValueError("Bitte die E-Mail-Adresse des Benutzers im Proxy Manager angeben.")
                data["email"] = email
            if changes.get("password") is not None:
                password = str(changes["password"])
                if len(password) > 300:
                    raise ValueError("Das Passwort ist zu lang.")
                data["password"] = password
            write_private(self.path, data)
            self.data = data


class Watcher:
    """Checks every WATCH_EVERY seconds and reports each certificate once when it nears expiry and once when it
    expired; targets that stop answering are reported after two checks in a row."""

    def __init__(self, folder, npm):
        self.path = os.path.join(folder, "watch.json")
        self.npm = npm
        try:
            with open(self.path) as f:
                self.state = {"certs": {}, "down": {}, **json.load(f)}
        except (OSError, ValueError):
            self.state = {"certs": {}, "down": {}}

    def check(self):
        o = self.npm.overview()
        certs = {}
        for c in o["certs"]:
            if c["unused"] or c["days"] is None:
                continue
            key = str(c["id"])
            stage = "expired" if c["state"] == "expired" else "warn" if c["days"] < WARN_DAYS else ""
            before = self.state["certs"].get(key, "")
            if stage and stage != before:
                hosts = ", ".join(c["hosts"][:3]) + (" …" if len(c["hosts"]) > 3 else "")
                if stage == "expired":
                    spool_event("cert_expired", "Zertifikat abgelaufen",
                                f"{c['name']} ist abgelaufen. Betroffen: {hosts}", "high")
                else:
                    spool_event("cert_expiring", "Zertifikat läuft bald ab",
                                f"{c['name']} läuft in {c['days']} Tagen ab"
                                + (" und wurde nicht automatisch erneuert" if c["provider"] == "letsencrypt" else "")
                                + f". Betroffen: {hosts}")
            if stage:
                certs[key] = stage
        down = {}
        for h in o["hosts"]:
            if h["target_state"] != "down":
                continue
            key = f"{h['kind']}-{h['id']}"
            down[key] = self.state["down"].get(key, 0) + 1
            if down[key] == 2:
                spool_event("target_down", "Ziel antwortet nicht",
                            f"{(h['domains'] or ['?'])[0]} zeigt auf {h['target']}, dort antwortet nichts.")
        self.state = {"certs": certs, "down": down}
        write_private(self.path, self.state)

    def run(self, settings):
        time.sleep(60)
        while True:
            s = settings.get()
            if s["email"] and s["password"]:
                try:
                    self.check()
                except NpmError as exc:
                    log(f"Prüfung: {exc}")
                except Exception as exc:  # keep watching whatever happens
                    log(f"Prüfung fehlgeschlagen: {exc!r}")
            time.sleep(WATCH_EVERY)


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    settings = None
    npm = None

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
        except NpmError as exc:
            self.send_json(exc.status if 400 <= exc.status < 600 and exc.status != 401 else 502, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if self.role() not in ("owner", "admin"):
            raise PermissionError("Den Proxy Manager dürfen nur Eigentümer und Admins von Home-Net sehen.")

        if path == "/api/settings":
            if method == "PUT":
                self.settings.update(body)
                log(f"{self.actor()}: Einstellungen geändert")
            s = self.settings.get()
            return self.send_json(200, {"url": s["url"], "email": s["email"], "password_set": bool(s["password"])})
        if method == "GET" and path == "/api/overview":
            s = self.settings.get()
            empty = {"hosts": [], "certs": [], "streams": [], "summary": {}, "version": ""}
            if not s["email"] or not s["password"]:
                return self.send_json(200, dict(empty, configured=False, error=""))
            try:
                data = self.npm.overview()
            except NpmError as exc:
                return self.send_json(200, dict(empty, configured=True, error=str(exc)))
            return self.send_json(200, dict(data, configured=True, error=""))

        match = re.fullmatch(r"/api/certs/(\d{1,9})/renew", path)
        if method == "POST" and match:
            self.npm.renew(match.group(1))
            log(f"{self.actor()}: Zertifikat {match.group(1)} erneuert")
            return self.send_json(200, {"ok": True})
        match = re.fullmatch(r"/api/hosts/(proxy|redirect|404)/(\d{1,9})", path)
        if method == "PUT" and match:
            if not isinstance(body.get("enabled"), bool):
                raise ValueError("Bitte angeben, ob die Adresse an oder aus sein soll.")
            self.npm.set_enabled(match.group(1), match.group(2), body["enabled"])
            log(f"{self.actor()}: Adresse {match.group(1)}/{match.group(2)} {'an' if body['enabled'] else 'aus'}")
            return self.send_json(200, {"ok": True})
        raise LookupError


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.settings = Settings(DATA)
    ApiHandler.npm = Npm(ApiHandler.settings.get)
    threading.Thread(target=Watcher(DATA, ApiHandler.npm).run, args=(ApiHandler.settings,), daemon=True).start()
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
