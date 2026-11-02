#!/usr/bin/env python3
"""Unraid-Verwaltung: Freigaben, Benutzer, Einstellungen, Docker und VMs des Unraid-Servers, auf dem Home-Net läuft.

Nur über den Zugang erreichbar (Socket-Datei). Ansehen dürfen alle mit Zugriff auf das Modul nur die Übersicht;
alles andere, auch das Durchsehen der Freigaben, nur Eigentümer und Admins.
"""
import json
import mimetypes
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

import host  # noqa: E402
from host import HostError, Server  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
NOTIFY_DIR = os.environ.get("NOTIFY_DIR", "/host/notifications")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_DOWNLOAD = 200 * 1024 * 1024


def log(text):
    print(text, file=sys.stderr, flush=True)


class Settings:
    DEFAULTS = {"api_key": "", "allow_untested": False, "forward": True}

    def __init__(self, folder):
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = dict(self.DEFAULTS, **json.load(f))
        except (OSError, ValueError):
            self.data = dict(self.DEFAULTS)

    def get(self):
        return dict(self.data)

    def update(self, changes):
        with self.lock:
            data = dict(self.data)
            if "api_key" in changes and changes["api_key"] is not None:
                key = str(changes["api_key"]).strip()
                if len(key) > 512 or (key and not re.fullmatch(r"[A-Za-z0-9._\-]+", key)):
                    raise ValueError("Der API-Schlüssel sieht nicht richtig aus.")
                data["api_key"] = key
            for flag in ("allow_untested", "forward"):
                if flag in changes:
                    data[flag] = bool(changes[flag])
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.data = data


# --- Meldungen von Unraid an die App weitergeben --------------------------------------------------

def spool_event(kind, title, message, priority="default", link=""):
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


class Forwarder:
    """Unraid's own notifications (/tmp/notifications/unread) become Home-Net notifications for the app."""

    KINDS = {"alert": ("unraid_alert", "high"), "warning": ("unraid_warning", "default"),
             "normal": ("unraid_info", "low")}

    def __init__(self, settings, folder=None, state_dir=None):
        self.settings = settings
        self.folder = os.path.join(folder or NOTIFY_DIR, "unread")
        self.state = os.path.join(state_dir or DATA, "forwarded.json")
        try:
            with open(self.state) as f:
                self.seen = set(json.load(f))
            self.first = False
        except (OSError, ValueError):
            self.seen = set()
            self.first = True  # do not flood the app with everything that is already there

    def tick(self):
        try:
            names = sorted(n for n in os.listdir(self.folder) if n.endswith(".notify"))
        except OSError:
            return
        new = [n for n in names if n not in self.seen]
        if new and not self.first and self.settings.get().get("forward", True):
            for name in new[:20]:
                note = host.read_ini(name, self.folder)
                kind, priority = self.KINDS.get(note.get("importance", "normal"), self.KINDS["normal"])
                title = note.get("subject") or note.get("event") or "Unraid"
                text = " – ".join(x for x in (note.get("event", ""), note.get("description", "")) if x)
                spool_event(kind, f"Unraid: {title}"[:120], text[:400], priority)
        self.first = False
        if new or len(self.seen) > len(names) + 500:
            self.seen = set(names) | (self.seen & set(names))
            try:
                with open(self.state + ".tmp", "w") as f:
                    json.dump(sorted(self.seen), f)
                os.replace(self.state + ".tmp", self.state)
            except OSError as exc:
                log(f"Stand der Meldungen nicht gespeichert: {exc}")

    def unread(self):
        out = []
        try:
            names = sorted((n for n in os.listdir(self.folder) if n.endswith(".notify")), reverse=True)
        except OSError:
            return out
        for name in names[:50]:
            note = host.read_ini(name, self.folder)
            out.append({"time": host.to_int(note.get("timestamp")), "event": note.get("event", ""),
                        "subject": note.get("subject", ""), "description": note.get("description", ""),
                        "importance": note.get("importance", "normal")})
        return sorted(out, key=lambda n: n["time"], reverse=True)


def ticker(forwarder):
    while True:
        try:
            forwarder.tick()
        except Exception as exc:  # the service must keep running
            log(f"Meldungen: {exc!r}")
        time.sleep(30)


# --- HTTP ----------------------------------------------------------------------------------------

class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_api = None
    settings = None
    forwarder = None

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.headers.get('X-Forwarded-For', 'socket') if self.headers else 'socket'} {fmt % args}\n")

    def send_json(self, code, payload):
        self.send_body(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_body(self, code, body, content_type, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 1024 * 1024)) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def role(self):
        return self.headers.get("X-Home-Net-Role", "owner")

    def is_manager(self):
        return self.role() in ("owner", "admin")

    def need_manager(self):
        if not self.is_manager():
            raise PermissionError("Das darf nur ein Admin")

    def need_owner(self):
        if self.role() != "owner":
            raise PermissionError("Das darf nur der Eigentümer")

    def route(self):
        split = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(split.query))
        body = self.read_body() if self.command in ("POST", "PUT", "DELETE") else {}
        try:
            self.dispatch(self.command, split.path, query, body)
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"detail": str(exc) or "Das darf nur ein Admin"})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})
        except (HostError, OSError) as exc:
            if getattr(exc, "stale", False):
                exc = HostError(f"{exc}. Die Verbindung wird neu aufgebaut, bitte gleich noch einmal versuchen.")
                restart_soon()
            self.send_json(502, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, query, body):
        srv, settings = self.server_api, self.settings
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/overview":
            data = srv.overview()
            data["manager"] = self.is_manager()
            data["owner"] = self.role() == "owner"
            data["notifications"] = self.forwarder.unread()[:10] if self.forwarder else []
            data["temps"] = srv.temps()
            return self.send_json(200, data)

        self.need_manager()

        if path == "/api/settings":
            if method == "PUT":
                if "allow_untested" in body:
                    self.need_owner()
                settings.update(body)
            s = settings.get()
            return self.send_json(200, {"api_key_set": bool(s["api_key"]), "allow_untested": s["allow_untested"],
                                        "forward": s["forward"], "version": srv.version(), "tested": host.TESTED})

        # Freigaben
        if path == "/api/shares":
            if method == "GET":
                return self.send_json(200, {"shares": [public_share(s) for s in srv.shares()], "pools": srv.pools(),
                                            "users": [u["name"] for u in srv.users() if u["name"] != "root"],
                                            "fruit": srv.var().get("enableFruit") == "yes",
                                            "writable": srv.writable()})
            if method == "POST":
                name = srv.save_share("", body)
                if isinstance(body.get("smb"), dict):
                    wait_for(lambda: any(s["name"] == name for s in srv.shares()))
                    srv.share_security(name, body["smb"])
                    if isinstance(body.get("access"), dict):
                        srv.share_access(name, {str(k): str(v) for k, v in body["access"].items()},
                                         security=body["smb"].get("security"))
                return self.send_json(201, {"name": name})
        match = re.fullmatch(r"/api/shares/([^/]+)(?:/(security|access))?", path)
        if match:
            name, part = urllib.parse.unquote(match.group(1)), match.group(2)
            if method == "GET" and not part:
                return self.send_json(200, public_share(srv.share(name)))
            if method == "PUT" and not part:
                return self.send_json(200, {"name": srv.save_share(name, body)})
            if method == "DELETE" and not part:
                srv.delete_share(name)
                return self.send_json(200, {"ok": True})
            if method == "PUT" and part == "security":
                srv.share_security(name, body)
                return self.send_json(200, {"ok": True})
            if method == "PUT" and part == "access":
                access = body.get("access")
                if not isinstance(access, dict):
                    raise ValueError("Bitte die Rechte angeben.")
                security = body.get("security") if body.get("security") in ("secure", "private") else None
                srv.share_access(name, {str(k): str(v) for k, v in access.items()}, security=security)
                return self.send_json(200, {"ok": True})

        # Benutzer
        if path == "/api/users":
            if method == "GET":
                shares = srv.shares()
                users = []
                for u in srv.users():
                    rights = {s["name"]: user_right(s, u["name"]) for s in shares if s["smb"]["export"] != "-"}
                    users.append(dict(u, rights=rights))
                return self.send_json(200, {"users": users, "writable": srv.writable()})
            if method == "POST":
                srv.add_user(body.get("name"), body.get("desc", ""), body.get("password", ""))
                return self.send_json(201, {"ok": True})
        match = re.fullmatch(r"/api/users/([^/]+)(/password)?", path)
        if match:
            name = urllib.parse.unquote(match.group(1))
            if method == "PUT" and match.group(2):
                srv.set_password(name, body.get("password", ""))
                return self.send_json(200, {"ok": True})
            if method == "PUT":
                srv.edit_user(name, body.get("desc", ""))
                return self.send_json(200, {"ok": True})
            if method == "DELETE":
                srv.delete_user(name)
                return self.send_json(200, {"ok": True})

        # Einstellungen von Unraid
        if method == "GET" and path == "/api/general":
            v = srv.var()
            data = srv.general()
            data.update({k: v.get(k, "") for k in ("serverMultiChannel", "USE_NETBIOS", "USE_WSD", "WSD2_OPT",
                                                   "fuse_remember")})
            return self.send_json(200, {"values": data, "temps": srv.temps(), "writable": srv.writable()})
        match = re.fullmatch(r"/api/general/(\w+)", path)
        if match and method == "PUT" and match.group(1) == "temps":
            srv.set_temps(body)
            return self.send_json(200, {"ok": True})
        if match and method == "PUT":
            srv.save_general(match.group(1), body)
            return self.send_json(200, {"ok": True})

        match = re.fullmatch(r"/api/array/(\w+)", path)
        if match and method == "POST":
            srv.array_command(match.group(1))
            return self.send_json(202, {"ok": True})

        # Docker und VMs
        if method == "GET" and path == "/api/docker":
            return self.send_json(200, {"containers": srv.containers()})
        if method == "POST" and path == "/api/docker":
            return self.send_json(200, {"name": srv.container_command(str(body.get("id", "")),
                                                                      str(body.get("command", "")))})
        if method == "GET" and path == "/api/vms":
            return self.send_json(200, {"vms": srv.vms()})
        if method == "POST" and path == "/api/vms":
            return self.send_json(200, {"name": srv.vm_command(str(body.get("id", "")), str(body.get("command", "")))})

        # Dateien
        if method == "GET" and path == "/api/files":
            return self.send_json(200, srv.browse(query.get("path", "")))
        if method == "GET" and path == "/api/download":
            full = srv.resolve(query.get("path", ""))
            if not os.path.isfile(full):
                raise LookupError
            if os.path.getsize(full) > MAX_DOWNLOAD:
                raise ValueError("Die Datei ist zu groß zum Herunterladen über Home-Net (höchstens 200 MB).")
            with open(full, "rb") as f:
                data = f.read()
            kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
            name = urllib.parse.quote(os.path.basename(full))
            return self.send_body(200, data, kind, {"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

        if method == "GET" and path == "/api/notifications":
            return self.send_json(200, {"notifications": self.forwarder.unread() if self.forwarder else []})
        self.send_json(404, {"detail": "Nicht gefunden"})


STARTED = time.time()


def restart_soon():
    """Sockets are bound into the container as files; after Unraid restarts a service they point to the old one.
    Docker starts the container again (restart: unless-stopped) and binds the new socket."""
    if time.time() - STARTED > 120:
        log("Socket von Unraid veraltet, starte neu")
        threading.Timer(1.0, os._exit, (3,)).start()


def public_share(share):
    return {k: v for k, v in share.items() if k != "raw"} | {"allocator": share["raw"]["allocator"]}


def user_right(share, user):
    smb = share["smb"]
    if smb["security"] == "public":
        return "read-write"
    right = smb["access"].get(user)
    if right:
        return right
    return "read-only" if smb["security"] == "secure" else "no-access"


def wait_for(check, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.5)
    return False


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    settings = Settings(DATA)
    ApiHandler.settings = settings
    ApiHandler.server_api = Server(settings.get)
    ApiHandler.forwarder = Forwarder(settings)
    threading.Thread(target=ticker, args=(ApiHandler.forwarder,), daemon=True).start()
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
