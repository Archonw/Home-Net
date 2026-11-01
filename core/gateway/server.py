#!/usr/bin/env python3
"""The one address of Home-Net: accounts, login, portal page, module list for the app, proxy to each module.

Inside the home network an account logs in with its password. Through the reverse proxy it also needs a
passkey. Apps use a device key they got once with a pairing code. Modules learn who is asking and which
categories that account may see from headers that only the gateway can set.
"""
import base64
import http.client
import http.cookies
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import events
import webauthn
from security import DEVICE_PREFIX, SESSION_SECONDS, Security

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL = os.path.join(HERE, "portal.html")
LOGIN = os.path.join(HERE, "login.html")
EVENTS_DIR = os.environ.get("EVENTS_DIR", "/events")
SCAN_SECONDS = 2
STREAM_PING_SECONDS = 45
COOKIE = "home_net_session"
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60
MAX_BODY = 200 * 1024 * 1024
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
              "proxy-authenticate", "host", "content-length", "cookie"}
BAD_LOGIN = "Benutzername oder Passwort ist falsch"


# The same way back on every module's page.
BACK_LINK = ('<a href="/" style="display:table;position:relative;z-index:1000;padding:8px 16px;'
             'font:600 15px system-ui,sans-serif;color:inherit;text-decoration:none">‹ Home-Net</a>')


def load(name):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return json.load(f)


def security():
    return Security.shared(CONFIG_DIR)


def inbox():
    return events.Inbox.shared(CONFIG_DIR, EVENTS_DIR)


def catalog():
    return events.catalog_of(load("gateway.json")["modules"])


def readable(user, entries):
    table = catalog()
    return [e for e in entries if events.may_read(user, e, table, Security.categories_of, Security.may_use)]


def public_event(entry):
    return {k: entry[k] for k in ("id", "time", "module", "type", "title", "message", "priority", "link")}


class Limiter:
    def __init__(self):
        self.fails = {}
        self.lock = threading.Lock()

    def blocked(self, ip):
        with self.lock:
            now = time.time()
            self.fails[ip] = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            return len(self.fails[ip]) >= MAX_FAILS

    def failed(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())
            just_locked = len(self.fails[ip]) == MAX_FAILS
        if just_locked:
            inbox().add(events.GATEWAY_MODULE, "login_locked", "Anmeldung gesperrt",
                        f"{MAX_FAILS} Fehlversuche von {ip}, die Adresse ist für 15 Minuten gesperrt.", "high")

    def succeeded(self, ip):
        with self.lock:
            self.fails.pop(ip, None)


def rewrite_html(body, prefix):
    """Modules write some links from their own root; under the gateway those need the module's prefix."""
    text = body.decode("utf-8", "replace")
    text = re.sub(r'(href|src|action)="/(?!/)', lambda m: f'{m.group(1)}="{prefix}/', text)
    text = text.replace("<head>", f'<head><script>window.HOME_NET_PREFIX="{prefix}"</script>', 1)
    text = re.sub(r"<body[^>]*>", lambda m: m.group(0) + BACK_LINK, text, count=1)
    return text.encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    limiter = Limiter()

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.client_ip()} {fmt % args}\n")

    def handle_one_request(self):
        # Clients that hang up mid-request are normal, not an error worth a traceback.
        try:
            super().handle_one_request()
        except ConnectionError:
            self.close_connection = True

    def client_ip(self):
        if not getattr(self, "headers", None):
            return self.client_address[0]
        # The proxy appends the address it saw, anything before that comes from the client and can be made up.
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[-1].strip()

    def reply(self, code, body=b"", content_type="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, code, payload, headers=None):
        self.reply(code, json.dumps(payload, ensure_ascii=False).encode(), headers=headers)

    def is_external(self):
        """Through the reverse proxy, or from any address outside the private ranges."""
        if self.headers.get("X-Forwarded-For"):
            return True
        try:
            return not ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return True

    def session_token(self):
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return cookies[COOKIE].value if COOKIE in cookies else ""

    def cookie_header(self, token, seconds):
        secure = "Secure; " if self.is_external() else ""
        return f"{COOKIE}={token}; Path=/; HttpOnly; {secure}SameSite=Strict; Max-Age={seconds}"

    def session_headers(self, user):
        return {"Set-Cookie": self.cookie_header(security().new_session(user["id"]), SESSION_SECONDS)}

    def identify(self):
        """(user, how): 'browser' for a session or a password inside the home network, 'device' for an app."""
        store = security()
        token = self.session_token()
        if token:
            user = store.session_user(token)
            if user:
                return user, "browser"
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer " + DEVICE_PREFIX):
            user = store.device_user(header[7:])
            return (user, "device") if user else (None, "")
        if header.startswith("Basic ") and not self.is_external():
            try:
                name, password = base64.b64decode(header[6:]).decode().split(":", 1)
            except (ValueError, IndexError):
                return None, ""
            user = store.check_login(name, password)
            return (user, "browser") if user else (None, "")
        return None, ""

    def need_login(self):
        """The user of this request, or None after answering it."""
        ip = self.client_ip()
        if self.limiter.blocked(ip):
            self.reply_json(429, {"detail": "Zu viele Fehlversuche, bitte in 15 Minuten erneut versuchen"})
            return None, ""
        user, how = self.identify()
        if user:
            return user, how
        if self.headers.get("Authorization"):
            self.limiter.failed(ip)
        if self.command == "GET" and "text/html" in self.headers.get("Accept", ""):
            self.reply(302, headers={"Location": "/login"})
        else:
            self.reply_json(401, {"detail": "Anmeldung erforderlich"})
        return None, ""

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            return {}
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def modules(self):
        return load("gateway.json")["modules"]

    def handle_any(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply_json(200, {"ok": True})
            return
        if path == "/login" and self.command == "GET":
            with open(LOGIN, "rb") as f:
                self.reply(200, f.read(), "text/html; charset=utf-8")
            return
        if path.startswith("/auth/"):
            self.handle_auth(path)
            return
        match = re.fullmatch(r"/m/([a-z0-9-]+)(/.*)?", path)
        module = None
        if match:
            module = next((m for m in self.modules() if m["id"] == match.group(1) and m.get("upstream")), None)
            if not module:
                self.reply_json(404, {"detail": "Modul nicht gefunden"})
                return
            rest = match.group(2) or "/"
            if any(rest == p or rest.startswith(p + "/") for p in module.get("public", [])):
                self.proxy(module, rest, None)
                return
        user, _how = self.need_login()
        if not user:
            return
        if module:
            if not security().may_use(user, module["id"]):
                self.reply_json(403, {"detail": "Für diesen Dienst fehlt die Berechtigung"})
                return
            self.proxy(module, match.group(2) or "/", user)
        elif path == "/" and self.command in ("GET", "HEAD"):
            with open(PORTAL, "rb") as f:
                self.reply(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/modules" and self.command == "GET":
            allowed = [m for m in self.modules() if security().may_use(user, m["id"])]
            self.reply_json(200, {"modules": [self.public_entry(m) for m in allowed]})
        else:
            self.reply_json(404, {"detail": "Nicht gefunden"})

    def handle_auth(self, path):
        store = security()
        ip = self.client_ip()
        if self.limiter.blocked(ip):
            self.reply_json(429, {"detail": "Zu viele Fehlversuche, bitte in 15 Minuten erneut versuchen"})
            return
        try:
            if self.command == "POST" and path in PUBLIC_AUTH:
                PUBLIC_AUTH[path](self, self.read_json(), store, ip)
                return
            user, how = self.need_login()
            if not user:
                return
            if path == "/auth/notifications":
                self.handle_notifications(user, store)
                return
            if path == "/auth/stream" and self.command == "GET":
                self.stream_notifications(user)
                return
            if how == "device":
                self.reply_json(403, {"detail": "Das geht nur nach der Anmeldung im Browser"})
                return
            self.handle_account(path, user, store, ip)
            if self.command != "GET":
                inbox().wake()
        except (webauthn.WebAuthnError, ValueError, KeyError, TypeError) as exc:
            if self.command == "POST" and path in PUBLIC_AUTH:
                self.limiter.failed(ip)
            self.reply_json(400, {"detail": str(exc) or "Ungültige Anfrage"})
        except PermissionError as exc:
            self.reply_json(403, {"detail": str(exc)})
        except LookupError:
            self.reply_json(404, {"detail": "Nicht gefunden"})

    def handle_notifications(self, user, store):
        if self.command == "POST":
            store.set_notify(user["id"], self.read_json().get("enabled", {}))
            user = store.find_user(user["id"])
        elif self.command != "GET":
            self.reply_json(405, {"detail": "Nicht erlaubt"})
            return
        types = []
        for module_id, (name, known) in catalog().items():
            if module_id == events.GATEWAY_MODULE or security().may_use(user, module_id):
                types += [{"key": f"{module_id}.{e['id']}", "group": name, "label": e["label"],
                           "enabled": bool(events.is_enabled(user, module_id, e))}
                          for e in known.values() if user["role"] in ("owner", "admin") or not e.get("manager_only")]
        recent = readable(user, inbox().recent())[-30:]
        self.reply_json(200, {"types": types, "recent": [public_event(e) for e in reversed(recent)]})

    def stream_notifications(self, user):
        """Keeps the answer open and writes each message as it arrives (server-sent events).

        Without ?after= it starts with what comes next, so a newly coupled phone is not flooded with old messages.
        """
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            last_id = int(query["after"][0])
        except (KeyError, ValueError):
            last_id = inbox().latest_id()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(f"event: start\ndata: {inbox().latest_id()}\n\n".encode())
        while True:
            inbox().wait(last_id, STREAM_PING_SECONDS)
            # A removed device or blocked account stops receiving here.
            user, _how = self.identify()
            if not user:
                return
            fresh = inbox().since(last_id)
            for entry in readable(user, fresh):
                self.wfile.write(f"id: {entry['id']}\ndata: {json.dumps(public_event(entry))}\n\n".encode())
            if fresh:
                last_id = fresh[-1]["id"]
            self.wfile.write(b": ping\n\n")

    def handle_account(self, path, actor, store, ip):
        method = self.command
        manager = actor["role"] in ("owner", "admin")
        item = re.fullmatch(r"/auth/(passkeys|devices)/([A-Za-z0-9_-]+)", path)
        account = re.fullmatch(r"/auth/users/([0-9a-f]+)", path)
        if path == "/auth/me" and method == "GET":
            self.reply_json(200, store.account(actor))
        elif path == "/auth/me" and method == "POST":
            before = actor["name"]
            changed = store.change_own(actor["id"], self.read_json())
            store.audit(before, "Eigenes Konto geändert", changed["name"], ip)
            self.reply_json(200, changed)
        elif path == "/auth/logout" and method == "POST":
            store.drop_session(self.session_token())
            self.reply_json(200, {"ok": True}, headers={"Set-Cookie": self.cookie_header("", 0)})
        elif path == "/auth/code" and method == "POST":
            form = self.read_json()
            if form.get("kind") not in ("setup", "device"):
                raise ValueError("Unbekannte Art")
            target = actor
            if form.get("user") and form["user"] != actor["id"]:
                target = store.find_user(form["user"])
                if not target:
                    raise LookupError
                store.check_role_change(actor, target, target["role"])
                if not manager:
                    raise PermissionError("Das darf nur ein Admin")
            store.audit(actor["name"], "Code ausgestellt", f"{form['kind']} für {target['name']}", ip)
            self.reply_json(200, {"code": store.new_code(form["kind"], target["id"]), "for": target["name"]})
        elif item and method == "DELETE":
            removed = store.remove_item(item.group(1), item.group(2), actor)
            store.audit(actor["name"], "Passkey entfernt" if item.group(1) == "passkeys" else "Gerät entfernt",
                        removed["name"], ip)
            self.reply_json(200, {"ok": True})
        elif not manager:
            self.reply_json(403, {"detail": "Das darf nur ein Admin"})
        elif path == "/auth/external" and method == "POST":
            store.set_external_url(self.read_json().get("url", ""))
            store.audit(actor["name"], "Adresse für unterwegs geändert", "", ip)
            self.reply_json(200, {"ok": True})
        elif path == "/auth/users" and method == "GET":
            self.reply_json(200, {"users": store.users(), "items": store.all_items(),
                                  "modules": [{"id": m["id"], "name": m["name"]} for m in self.modules()]})
        elif path == "/auth/users" and method == "POST":
            created = store.create_user(actor, self.read_json())
            store.audit(actor["name"], "Konto angelegt", f"{created['name']} ({created['role']})", ip)
            self.reply_json(201, created)
        elif account and method == "PUT":
            form = self.read_json()
            changed = store.update_user(actor, account.group(1), form)
            what = ", ".join(sorted(k for k in form if k != "password")) + (" Passwort" if form.get("password") else "")
            store.audit(actor["name"], "Konto geändert", f"{changed['name']}: {what}", ip)
            self.reply_json(200, changed)
        elif account and method == "DELETE":
            removed = store.delete_user(actor, account.group(1))
            store.audit(actor["name"], "Konto gelöscht", removed["name"], ip)
            self.reply_json(200, {"ok": True})
        elif path == "/auth/audit" and method == "GET":
            self.reply_json(200, {"entries": store.audit_tail()})
        else:
            self.reply_json(404, {"detail": "Nicht gefunden"})

    @staticmethod
    def public_entry(module):
        out = {k: module[k] for k in ("id", "name", "description", "icon") if k in module}
        if module.get("upstream"):
            out["path"] = f"/m/{module['id']}"
            out["web"] = bool(module.get("web"))
            out["online"] = module_online(module)
        else:
            out["external_port"] = module.get("external_port")
        return out

    def proxy(self, module, rest, user):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.reply_json(413, {"detail": "Zu groß"})
            return
        body = self.rfile.read(length) if length else None
        query = urllib.parse.urlsplit(self.path).query
        target = rest + (f"?{query}" if query else "")
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP and k.lower() not in ("authorization", "accept-encoding")
                   and not k.lower().startswith("x-home-net-")}
        if user:
            if module.get("password"):
                token = base64.b64encode(f"app:{module['password']}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"
            headers["X-Home-Net-User"] = base64.b64encode(user["name"].encode()).decode()
            headers["X-Home-Net-Role"] = user["role"]
            headers["X-Home-Net-Categories"] = base64.b64encode(
                json.dumps(security().categories_of(user)).encode()).decode()
            if self.command not in ("GET", "HEAD"):
                security().audit(user["name"], f"{module['name']}: {self.command}", rest[:200], self.client_ip())
        headers["X-Forwarded-For"] = self.client_ip()
        prefix = f"/m/{module['id']}"
        headers["X-Forwarded-Prefix"] = prefix
        conn = upstream_connection(module, 180)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError as exc:
            self.reply_json(502, {"detail": f"{module['name']} nicht erreichbar ({type(exc).__name__})"})
            return
        finally:
            conn.close()
        content_type = resp.getheader("Content-Type", "application/octet-stream")
        if content_type.startswith("text/html"):
            data = rewrite_html(data, prefix)
        extra = {}
        location = resp.getheader("Location")
        if location:
            extra["Location"] = prefix + location if location.startswith("/") else location
        if resp.status == 401:
            # A rejected login never reaches this point, so this is a wrong module password.
            self.reply_json(502, {"detail": f"{module['name']} lehnt die Anmeldung des Gateways ab"})
            return
        self.reply(resp.status, data, content_type, extra)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = handle_any


def checked_user(handler, body, store, ip):
    """The account for these credentials, or None after answering."""
    user = store.check_login(body.get("username", ""), body.get("password", ""))
    if not user:
        handler.limiter.failed(ip)
        store.audit(str(body.get("username", ""))[:40], "Anmeldung fehlgeschlagen", "", ip)
        handler.reply_json(401, {"detail": BAD_LOGIN})
    return user


def login_start(handler, body, store, ip):
    """Inside the home network this logs in; from outside the passkey follows."""
    user = checked_user(handler, body, store, ip)
    if not user:
        return
    if not handler.is_external():
        handler.limiter.succeeded(ip)
        store.audit(user["name"], "Angemeldet", "Heimnetz", ip)
        handler.reply_json(200, {"ok": True}, headers=handler.session_headers(user))
        return
    keys = store.passkeys_of(user["id"])
    if not keys:
        handler.reply_json(403, {"detail": "Von außen braucht dieses Konto einen Passkey. Hol dir dafür einen Einrichtungscode."})
        return
    _, rp_id = store.origin_and_rp()
    ticket, challenge = store.new_challenge("login", user=user["id"])
    handler.reply_json(200, {"ticket": ticket, "options": {
        "challenge": challenge, "rpId": rp_id, "userVerification": "required", "timeout": 60000,
        "allowCredentials": [{"type": "public-key", "id": p["id"]} for p in keys]}})


def login_finish(handler, body, store, ip):
    pending = store.take_challenge(body.get("ticket"), "login")
    if not pending:
        raise ValueError("Die Anmeldung ist abgelaufen, bitte von vorn beginnen")
    passkey = store.find_passkey(pending["user"], str(body.get("id", "")))
    user = store.find_user(pending["user"])
    if not passkey or not user or not user.get("active"):
        raise ValueError("Unbekannter Passkey")
    origin, rp_id = store.origin_and_rp()
    count = webauthn.verify_assertion(body["clientData"], body["authData"], body["signature"], passkey,
                                      pending["challenge"], origin, rp_id)
    store.update_count(passkey["id"], count)
    handler.limiter.succeeded(ip)
    store.audit(user["name"], "Angemeldet", "Passkey", ip)
    handler.reply_json(200, {"ok": True}, headers=handler.session_headers(user))


def login_recovery(handler, body, store, ip):
    user = checked_user(handler, body, store, ip)
    if not user:
        return
    if not store.use_recovery(user["id"], body.get("code", "")):
        raise ValueError("Der Wiederherstellungscode stimmt nicht")
    handler.limiter.succeeded(ip)
    store.audit(user["name"], "Angemeldet", "Wiederherstellungscode", ip)
    handler.reply_json(200, {"ok": True}, headers=handler.session_headers(user))


def setup_options(handler, body, store, ip):
    """Adds a passkey. The code comes from the start page, so a password alone is not enough."""
    user = checked_user(handler, body, store, ip)
    if not user:
        return
    origin, rp_id = store.origin_and_rp()
    if not origin:
        raise ValueError("Es ist noch keine Adresse für unterwegs eingetragen")
    if not store.take_code("setup", user["id"], body.get("code", "")):
        raise ValueError("Der Einrichtungscode stimmt nicht oder ist abgelaufen")
    ticket, challenge = store.new_challenge("setup", user=user["id"], name=str(body.get("name") or "Passkey")[:60])
    handler.reply_json(200, {"ticket": ticket, "options": {
        "challenge": challenge, "rp": {"id": rp_id, "name": "Home-Net"},
        "user": {"id": webauthn.b64url(user["id"].encode()), "name": user["name"], "displayName": user["name"]},
        "pubKeyCredParams": [{"type": "public-key", "alg": webauthn.ES256}], "timeout": 60000,
        "attestation": "none", "authenticatorSelection": {"residentKey": "preferred", "userVerification": "required"},
        "excludeCredentials": [{"type": "public-key", "id": p["id"]} for p in store.passkeys_of(user["id"])]}})


def setup_finish(handler, body, store, ip):
    pending = store.take_challenge(body.get("ticket"), "setup")
    if not pending:
        raise ValueError("Die Einrichtung ist abgelaufen, bitte von vorn beginnen")
    user = store.find_user(pending["user"])
    if not user or not user.get("active"):
        raise ValueError("Unbekanntes Konto")
    origin, rp_id = store.origin_and_rp()
    credential_id, x, y, count = webauthn.verify_registration(body["clientData"], body["attestation"],
                                                              pending["challenge"], origin, rp_id)
    recovery = store.add_passkey(user["id"], credential_id, x, y, count, pending["name"])
    store.drop_sessions(user["id"])
    handler.limiter.succeeded(ip)
    store.audit(user["name"], "Passkey eingerichtet", pending["name"], ip)
    inbox().add(events.GATEWAY_MODULE, "passkey_added", "Neuer Passkey", f"{user['name']}: {pending['name']}", "high")
    handler.reply_json(200, {"recovery": recovery}, headers=handler.session_headers(user))


def pair_device(handler, body, store, ip):
    """Gives an app its own key. The code comes from the start page."""
    user = checked_user(handler, body, store, ip)
    if not user:
        return
    if not store.take_code("device", user["id"], body.get("code", "")):
        raise ValueError("Der Kopplungscode stimmt nicht oder ist abgelaufen")
    name = str(body.get("name") or "Handy")[:60]
    handler.limiter.succeeded(ip)
    store.audit(user["name"], "Gerät gekoppelt", name, ip)
    inbox().add(events.GATEWAY_MODULE, "device_paired", "Neues Gerät gekoppelt", f"{user['name']}: {name}", "high")
    handler.reply_json(200, {"token": store.add_device(user["id"], name)})


PUBLIC_AUTH = {"/auth/login": login_start, "/auth/login/finish": login_finish, "/auth/recovery": login_recovery,
               "/auth/setup": setup_options, "/auth/setup/finish": setup_finish, "/auth/pair": pair_device}


class UnixConnection(http.client.HTTPConnection):
    """HTTP to a module that listens on a socket file instead of a port."""

    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def upstream_connection(module, timeout):
    url = urllib.parse.urlsplit(module["upstream"])
    if url.scheme == "unix":
        return UnixConnection(url.path, timeout)
    return http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)


def module_online(module):
    conn = upstream_connection(module, 3)
    try:
        conn.request("GET", module.get("health", "/health"))
        return conn.getresponse().status < 500
    except OSError:
        return False
    finally:
        conn.close()


def scan_forever():
    while True:
        try:
            inbox().scan(catalog())
        except Exception as exc:  # the gateway must keep running whatever a module leaves behind
            sys.stderr.write(f"Meldungen nicht lesbar: {exc!r}\n")
        time.sleep(SCAN_SECONDS)


def main():
    threading.Thread(target=scan_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
