#!/usr/bin/env python3
"""Nextcloud-Verwaltung: Zustand, Speicher und Updates einer Nextcloud, dazu ihre Benutzer anlegen, ändern, sperren.

Nur über den Zugang erreichbar (Socket-Datei). Den Zustand (Version, Speicher, Updates) sieht jeder mit Zugriff auf das
Modul; die Benutzerliste, alle Änderungen und die Einstellungen nur Eigentümer und Admins von Home-Net. Ein Wächter
meldet, wenn Nextcloud nicht antwortet, der Speicher knapp wird oder ein Update bereitsteht.
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

from nextcloud import STORAGE_WARN, Nextcloud, NextcloudError, check_user_id, quota_text  # noqa: E402

DATA = os.environ.get("DATA", "/data")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
WATCH_EVERY = int(os.environ.get("WATCH_EVERY", "600"))
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
LINK = "/m/nextcloud/"
MANAGERS = ("owner", "admin")


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
    """Address of Nextcloud and the admin account (user name + app password) Home-Net signs in with."""

    EMPTY = {"url": "", "user": "", "password": "", "verify_tls": True}

    def __init__(self, folder):
        self.path = os.path.join(folder, "settings.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = {**self.EMPTY, **json.load(f)}
        except (OSError, ValueError):
            self.data = dict(self.EMPTY)

    def get(self):
        return dict(self.data)

    def configured(self):
        return bool(self.data["url"] and self.data["user"] and self.data["password"])

    def update(self, changes):
        with self.lock:
            data = dict(self.data)
            if changes.get("url") is not None:
                url = str(changes["url"]).strip().rstrip("/")
                if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
                    raise ValueError("Die Adresse sieht nicht richtig aus, z. B. https://cloud.example.org")
                data["url"] = url
            if changes.get("user") is not None:
                user = str(changes["user"]).strip()
                if len(user) > 64:
                    raise ValueError("Der Benutzername ist zu lang.")
                data["user"] = user
            if changes.get("password") is not None:
                password = str(changes["password"]).strip()
                if len(password) > 300:
                    raise ValueError("Das App-Passwort ist zu lang.")
                data["password"] = password
            if changes.get("verify_tls") is not None:
                data["verify_tls"] = bool(changes["verify_tls"])
            write_private(self.path, data)
            self.data = data


class Watcher:
    """Checks every WATCH_EVERY seconds. Reports Nextcloud as down after two failed checks in a row and once when it is
    back, low disk space once until it recovers, and each new version (Nextcloud or app) once."""

    def __init__(self, folder, nc):
        self.path = os.path.join(folder, "watch.json")
        self.nc = nc
        try:
            with open(self.path) as f:
                self.state = {"fails": 0, "down": False, "low": False, "seen": [], **json.load(f)}
        except (OSError, ValueError):
            self.state = {"fails": 0, "down": False, "low": False, "seen": []}

    def save(self):
        write_private(self.path, self.state)

    def check(self):
        try:
            status = self.nc.status()
        except NextcloudError as exc:
            self.state["fails"] += 1
            if self.state["fails"] == 2 and not self.state["down"]:
                self.state["down"] = True
                spool_event("nextcloud_down", "Nextcloud nicht erreichbar", str(exc), "high")
            self.save()
            return
        if self.state["down"]:
            spool_event("nextcloud_down", "Nextcloud wieder erreichbar", "Nextcloud antwortet wieder.")
        self.state["fails"], self.state["down"] = 0, False
        if status["maintenance"]:
            log("Nextcloud ist im Wartungsmodus")
        info = self.nc.serverinfo()
        if info:
            self.check_storage(info)
            self.check_updates(status, info)
        self.save()

    def check_storage(self, info):
        free = info.get("free_bytes")
        low = free is not None and free < STORAGE_WARN
        if low and not self.state["low"]:
            spool_event("nextcloud_storage", "Nextcloud: Speicher wird knapp",
                        f"Nur noch {free / 1024 ** 3:.0f} GB frei.", "high")
        self.state["low"] = low

    def check_updates(self, status, info):
        seen = set(self.state["seen"])
        now = set()
        if info["server_update"]:
            key = f"server:{info['server_update']}"
            now.add(key)
            if key not in seen:
                spool_event("nextcloud_update", "Update für Nextcloud",
                            f"Nextcloud {info['server_update']} ist verfügbar (läuft: {status['version']}).")
        fresh = []
        for app in info["app_updates"]:
            key = f"app:{app['id']}:{app['version']}"
            now.add(key)
            if key not in seen:
                fresh.append(app["id"])
        if fresh:
            spool_event("nextcloud_update", "App-Updates in Nextcloud",
                        f"Neue Versionen für: {', '.join(fresh[:6])}" + (" …" if len(fresh) > 6 else ""))
        self.state["seen"] = sorted(now)

    def run(self, settings):
        time.sleep(60)
        while True:
            if settings.configured():
                try:
                    self.check()
                except NextcloudError as exc:
                    log(f"Prüfung: {exc}")
                except Exception as exc:  # keep watching whatever happens
                    log(f"Prüfung fehlgeschlagen: {exc!r}")
            time.sleep(WATCH_EVERY)


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    settings = None
    nc = None

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
        if self.role() not in MANAGERS:
            raise PermissionError("Das dürfen nur Eigentümer und Admins von Home-Net.")

    def route(self):
        split = urllib.parse.urlsplit(self.path)
        self.query = dict(urllib.parse.parse_qsl(split.query))
        body = self.read_body() if self.command in ("POST", "PUT", "DELETE") else {}
        try:
            self.dispatch(self.command, split.path, body)
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"detail": str(exc) or "Das darf nur ein Admin"})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})
        except NextcloudError as exc:
            self.send_json(exc.status if 400 <= exc.status < 600 else 502, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = route

    def dispatch(self, method, path, body):
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")

        if method == "GET" and path == "/api/overview":
            return self.overview()
        if path == "/api/settings":
            self.manager()
            if method == "PUT":
                self.settings.update(body)
                self.nc.forget()
                log(f"{self.actor()}: Einstellungen geändert")
            s = self.settings.get()
            return self.send_json(200, {"url": s["url"], "user": s["user"], "password_set": bool(s["password"]),
                                        "verify_tls": s["verify_tls"]})

        self.manager()
        if method == "POST" and path == "/api/users":
            return self.create_user(body)
        if method == "POST" and path == "/api/groups":
            self.nc.create_group(body.get("name"))
            log(f"{self.actor()}: Gruppe {body.get('name')} angelegt")
            return self.send_json(200, {"ok": True})
        match = re.fullmatch(r"/api/users/([^/]+)(/groups|/welcome)?", path)
        if match:
            user_id = check_user_id(urllib.parse.unquote(match.group(1)))
            part = match.group(2) or ""
            if method == "PUT" and part == "":
                return self.change_user(user_id, body)
            if method == "PUT" and part == "/groups":
                return self.change_group(user_id, body)
            if method == "POST" and part == "/welcome":
                self.nc.welcome(user_id)
                log(f"{self.actor()}: Willkommens-Mail an {user_id}")
                return self.send_json(200, {"ok": True})
            if method == "DELETE" and part == "":
                return self.delete_user(user_id, body)
        raise LookupError

    def overview(self):
        manager = self.role() in MANAGERS
        empty = {"configured": False, "error": "", "can_manage": manager, "status": None, "info": None,
                 "users": [], "groups": [], "user_count": None, "account": ""}
        if not self.settings.configured():
            return self.send_json(200, empty)
        try:
            data = self.nc.overview(full=manager)
        except NextcloudError as exc:
            return self.send_json(200, dict(empty, configured=True, error=str(exc)))
        return self.send_json(200, dict(empty, **data, configured=True,
                                        account=self.settings.get()["user"] if manager else ""))

    def own_account(self, user_id):
        return user_id.lower() == self.settings.get()["user"].lower()

    def create_user(self, body):
        groups = body.get("groups") or []
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise ValueError("Gruppen bitte als Liste angeben.")
        self.nc.create_user(body.get("id"), name=str(body.get("name") or "").strip(),
                            email=str(body.get("email") or "").strip(), password=str(body.get("password") or ""),
                            groups=groups, quota=str(body.get("quota") or ""))
        log(f"{self.actor()}: Benutzer {body.get('id')} angelegt")
        return self.send_json(200, {"ok": True})

    def change_user(self, user_id, body):
        changed = []
        if body.get("name") is not None:
            self.nc.set_field(user_id, "displayname", str(body["name"]).strip())
            changed.append("Name")
        if body.get("email") is not None:
            self.nc.set_field(user_id, "email", str(body["email"]).strip())
            changed.append("E-Mail")
        if body.get("quota") is not None:
            self.nc.set_field(user_id, "quota", quota_text(body["quota"]))
            changed.append("Speicherplatz")
        if body.get("password"):
            self.nc.set_field(user_id, "password", str(body["password"]))
            changed.append("Passwort")
        if isinstance(body.get("enabled"), bool):
            if not body["enabled"] and self.own_account(user_id):
                raise ValueError("Mit diesem Konto meldet sich Home-Net an. Es lässt sich hier nicht sperren.")
            self.nc.set_enabled(user_id, body["enabled"])
            changed.append("entsperrt" if body["enabled"] else "gesperrt")
        if not changed:
            raise ValueError("Nichts zu ändern.")
        log(f"{self.actor()}: Benutzer {user_id} geändert ({', '.join(changed)})")
        return self.send_json(200, {"ok": True})

    def change_group(self, user_id, body):
        group = str(body.get("group") or "").strip()
        if not group or not isinstance(body.get("member"), bool):
            raise ValueError("Bitte Gruppe und an/aus angeben.")
        if group == "admin" and not body["member"] and self.own_account(user_id):
            raise ValueError("Home-Net braucht für sein Konto die Gruppe admin.")
        self.nc.set_group(user_id, group, body["member"])
        log(f"{self.actor()}: {user_id} {'in' if body['member'] else 'aus'} Gruppe {group}")
        return self.send_json(200, {"ok": True})

    def delete_user(self, user_id, body):
        if self.own_account(user_id):
            raise ValueError("Mit diesem Konto meldet sich Home-Net an. Es lässt sich hier nicht löschen.")
        if str(body.get("confirm") or self.query.get("confirm") or "") != user_id:
            raise ValueError("Zum Löschen bitte den Benutzernamen genau eintippen.")
        self.nc.delete_user(user_id)
        log(f"{self.actor()}: Benutzer {user_id} gelöscht")
        return self.send_json(200, {"ok": True})


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    os.makedirs(DATA, exist_ok=True)
    ApiHandler.settings = Settings(DATA)
    ApiHandler.nc = Nextcloud(ApiHandler.settings.get)
    threading.Thread(target=Watcher(DATA, Nextcloud(ApiHandler.settings.get)).run, args=(ApiHandler.settings,),
                     daemon=True).start()
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
