#!/usr/bin/env python3
"""WireGuard-Verwaltung: VPN-Zugänge in WireGuard-Easy anlegen, sperren, löschen und als QR-Code zeigen.

Nur über den Zugang erreichbar (Socket-Datei). Alles darin dürfen nur Eigentümer und Admins von Home-Net, denn ein
Zugang öffnet das ganze Heimnetz.
"""
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wgeasy import WgEasy, WgEasyError  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
DEFAULT_URL = os.environ.get("WG_EASY_URL", "http://host.docker.internal:51821")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


def log(text):
    print(text, file=sys.stderr, flush=True)


class Settings:
    """Address and login of WireGuard-Easy. Home-Net's own WireGuard-Easy (module wireguard-server) leaves its login in
    the file login.json in the data folder; settings made here win over it."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = {"url": "", "username": "", "password": "", **json.load(f)}
        except (OSError, ValueError):
            self.data = {"url": "", "username": "", "password": ""}

    def get(self):
        data = dict(self.data)
        if not data["username"] or not data["password"]:
            try:
                with open(os.path.join(self.folder, "login.json")) as f:
                    given = json.load(f)
                data["username"], data["password"] = given.get("username", ""), given.get("password", "")
                data["url"] = data["url"] or given.get("url", "")
            except (OSError, ValueError, AttributeError):
                pass
        data["url"] = data["url"] or DEFAULT_URL
        return data

    def update(self, changes):
        with self.lock:
            data = dict(self.data)
            if changes.get("url") is not None:
                url = str(changes["url"]).strip().rstrip("/")
                if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
                    raise ValueError("Die Adresse sieht nicht richtig aus, z. B. http://192.168.1.10:51821")
                data["url"] = url
            if changes.get("username") is not None:
                name = str(changes["username"]).strip()
                if len(name) > 128 or ":" in name:
                    raise ValueError("Der Benutzername sieht nicht richtig aus.")
                data["username"] = name
            if changes.get("password") is not None:
                password = str(changes["password"])
                if len(password) > 512:
                    raise ValueError("Das Passwort ist zu lang.")
                data["password"] = password
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.data = data


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    settings = None
    wg = None

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
        except WgEasyError as exc:
            self.send_json(exc.status, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if self.role() not in ("owner", "admin"):
            raise PermissionError("WireGuard dürfen nur Eigentümer und Admins von Home-Net verwalten.")
        wg, settings = self.wg, self.settings

        if path == "/api/settings":
            if method == "PUT":
                settings.update(body)
                log(f"{self.actor()}: Einstellungen geändert")
            s = settings.get()
            return self.send_json(200, {"url": s["url"], "username": s["username"], "password_set": bool(s["password"])})
        if method == "GET" and path == "/api/overview":
            s = settings.get()
            empty = {"clients": [], "version": "", "update": False, "latest": ""}
            if not s["username"] or not s["password"]:
                return self.send_json(200, dict(empty, configured=False, error=""))
            try:
                data = wg.overview()
            except WgEasyError as exc:
                return self.send_json(200, dict(empty, configured=True, error=str(exc)))
            return self.send_json(200, dict(data, configured=True, error=""))
        if method == "POST" and path == "/api/clients":
            created = wg.create(body.get("name"), body.get("expires"))
            log(f"{self.actor()}: Zugang {created['name']} angelegt")
            return self.send_json(201, created)

        match = re.fullmatch(r"/api/clients/(\d{1,9})(?:/(config|qrcode\.svg))?", path)
        if not match:
            raise LookupError
        client_id, part = int(match.group(1)), match.group(2)
        if method == "PUT" and not part:
            if not isinstance(body.get("enabled"), bool):
                raise ValueError("Bitte angeben, ob der Zugang an oder aus sein soll.")
            wg.set_enabled(client_id, body["enabled"])
            log(f"{self.actor()}: Zugang {client_id} {'entsperrt' if body['enabled'] else 'gesperrt'}")
            return self.send_json(200, {"ok": True})
        if method == "DELETE" and not part:
            wg.delete(client_id)
            log(f"{self.actor()}: Zugang {client_id} gelöscht")
            return self.send_json(200, {"ok": True})
        if method == "GET" and part == "config":
            text = wg.configuration(client_id)
            log(f"{self.actor()}: Konfiguration von Zugang {client_id} abgerufen")
            return self.send_json(200, {"config": text})
        if method == "GET" and part == "qrcode.svg":
            svg = wg.qrcode(client_id)
            log(f"{self.actor()}: QR-Code von Zugang {client_id} abgerufen")
            return self.send_body(200, svg, "image/svg+xml")
        raise LookupError


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.settings = Settings(DATA)
    ApiHandler.wg = WgEasy(ApiHandler.settings.get)
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
