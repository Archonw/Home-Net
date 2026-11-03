#!/usr/bin/env python3
"""Pocket-ID-Verwaltung: Benutzer von Pocket ID anlegen, einladen, sperren und ihnen Apps zuordnen.

Nur über den Zugang erreichbar (Socket-Datei). Alles darin dürfen nur Eigentümer und Admins von Home-Net, denn wer hier
ändern kann, entscheidet über die Anmeldung an allen Apps.
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

from pocketid import PocketId, PocketIdError  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
DEFAULT_URL = os.environ.get("POCKET_ID_URL", "http://host.docker.internal:1411")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
ID = r"[A-Za-z0-9-]{1,64}"


def log(text):
    print(text, file=sys.stderr, flush=True)


class Settings:
    """Address and API key. The key may also lie in the file api-key in the data folder (e.g. copied there once)."""

    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = {"url": "", "api_key": "", **json.load(f)}
        except (OSError, ValueError):
            self.data = {"url": "", "api_key": ""}

    def get(self):
        data = dict(self.data)
        data["url"] = data["url"] or DEFAULT_URL
        if not data["api_key"]:
            try:
                with open(os.path.join(self.folder, "api-key")) as f:
                    data["api_key"] = f.read().strip()
            except OSError:
                pass
        return data

    def update(self, changes):
        with self.lock:
            data = dict(self.data)
            if changes.get("url") is not None:
                url = str(changes["url"]).strip().rstrip("/")
                if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
                    raise ValueError("Die Adresse sieht nicht richtig aus, z. B. http://192.168.1.10:1411")
                data["url"] = url
            if changes.get("api_key") is not None:
                key = str(changes["api_key"]).strip()
                if len(key) > 512 or (key and not re.fullmatch(r"[A-Za-z0-9._\-]+", key)):
                    raise ValueError("Der API-Schlüssel sieht nicht richtig aus.")
                data["api_key"] = key
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.data = data


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    settings = None
    pid = None

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
        raw = self.rfile.read(min(length, 256 * 1024)) if length else b""
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
        except PocketIdError as exc:
            self.send_json(exc.status, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if self.role() not in ("owner", "admin"):
            raise PermissionError("Pocket ID dürfen nur Eigentümer und Admins von Home-Net verwalten.")
        pid, settings = self.pid, self.settings

        if path == "/api/settings":
            if method == "PUT":
                settings.update(body)
                log(f"{self.actor()}: Einstellungen geändert")
            s = settings.get()
            return self.send_json(200, {"url": s["url"], "api_key_set": bool(s["api_key"])})
        if method == "GET" and path == "/api/overview":
            s = settings.get()
            if not s["api_key"]:
                return self.send_json(200, {"configured": False, "users": [], "apps": {"switches": [], "open": []},
                                            "groups": [], "issuer": "", "error": ""})
            try:
                data = pid.overview()
            except PocketIdError as exc:
                return self.send_json(200, {"configured": True, "users": [], "apps": {"switches": [], "open": []},
                                            "groups": [], "issuer": "", "error": str(exc)})
            return self.send_json(200, dict(data, configured=True, error=""))
        if method == "POST" and path == "/api/users":
            created = pid.create_user(body)
            log(f"{self.actor()}: Benutzer {created.get('username')} angelegt")
            invite = pid.invite(created["id"], body.get("hours") or 72) if body.get("invite", True) else None
            return self.send_json(201, {"id": created["id"], "invite": invite})

        match = re.fullmatch(rf"/api/users/({ID})(?:/(groups|apps|invite))?", path)
        if not match:
            raise LookupError
        user_id, part = match.group(1), match.group(2)
        if method == "PUT" and not part:
            pid.update_user(user_id, body)
            log(f"{self.actor()}: Benutzer {user_id} geändert"
                + (" (gesperrt)" if body.get("disabled") else " (entsperrt)" if "disabled" in body else ""))
            return self.send_json(200, {"ok": True})
        if method == "DELETE" and not part:
            gone = pid.delete_user(user_id)
            log(f"{self.actor()}: Benutzer {gone.get('username')} gelöscht")
            return self.send_json(200, {"ok": True})
        if method == "PUT" and part == "groups":
            groups = body.get("groups")
            if not isinstance(groups, list):
                raise ValueError("Bitte die Gruppen angeben.")
            pid.set_groups(user_id, groups)
            log(f"{self.actor()}: Gruppen von {user_id} geändert")
            return self.send_json(200, {"ok": True})
        if method == "PUT" and part == "apps":
            group = str(body.get("group") or "")
            if not re.fullmatch(ID, group):
                raise ValueError("Bitte die App angeben.")
            pid.set_app(user_id, group, bool(body.get("on")))
            log(f"{self.actor()}: App-Gruppe {group} für {user_id} {'an' if body.get('on') else 'aus'}")
            return self.send_json(200, {"ok": True})
        if method == "POST" and part == "invite":
            result = pid.invite(user_id, body.get("hours") or 72, email=bool(body.get("email")))
            log(f"{self.actor()}: Einladung für {user_id}" + (" per E-Mail" if body.get("email") else ""))
            return self.send_json(200, result)
        raise LookupError


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.settings = Settings(DATA)
    ApiHandler.pid = PocketId(ApiHandler.settings.get)
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
