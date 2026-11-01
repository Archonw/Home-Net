#!/usr/bin/env python3
"""The one address of Home-Net: login, portal page, module list for the app, and a proxy to each module.

Direct requests from the home network log in with the password. Requests through the reverse proxy need a
passkey as soon as one is set up; apps use a device key they got once with a pairing code.
"""
import base64
import hashlib
import hmac
import http.client
import http.cookies
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import webauthn
from security import DEVICE_PREFIX, SESSION_SECONDS, Security

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL = os.path.join(HERE, "portal.html")
LOGIN = os.path.join(HERE, "login.html")
COOKIE = "home_net_session"
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60
MAX_BODY = 200 * 1024 * 1024
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
              "proxy-authenticate", "host", "content-length", "cookie"}


def load(name):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return json.load(f)


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

    def succeeded(self, ip):
        with self.lock:
            self.fails.pop(ip, None)


def password_ok(password):
    try:
        record = load("auth.json")
    except (OSError, ValueError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), record["rounds"])
    return hmac.compare_digest(digest.hex(), record["hash"])


def rewrite_html(body, prefix):
    """Modules write some links from their own root; under the gateway those need the module's prefix."""
    text = body.decode("utf-8", "replace")
    text = re.sub(r'(href|src|action)="/(?!/)', lambda m: f'{m.group(1)}="{prefix}/', text)
    text = text.replace("<head>", f'<head><script>window.HOME_NET_PREFIX="{prefix}"</script>', 1)
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

    def basic_password(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return ""
        try:
            return base64.b64decode(header[6:]).decode().split(":", 1)[1]
        except (ValueError, IndexError):
            return ""

    def bearer_token(self):
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def login_state(self):
        """'admin' for people, 'device' for a paired app, '' otherwise."""
        store = self.security()
        token = self.session_token()
        if token and store.session_ok(token):
            return "admin"
        bearer = self.bearer_token()
        if bearer.startswith(DEVICE_PREFIX) and store.device_ok(bearer):
            return "device"
        password = self.basic_password()
        if password and password_ok(password):
            # Once a passkey exists, the password alone only counts inside the home network.
            if not self.is_external() or not store.has_passkeys():
                return "admin"
        return ""

    def authorized(self, admin=False):
        ip = self.client_ip()
        if self.limiter.blocked(ip):
            self.reply_json(429, {"detail": "Zu viele Fehlversuche, bitte in 15 Minuten erneut versuchen"})
            return False
        state = self.login_state()
        if state == "admin" or (state == "device" and not admin):
            self.limiter.succeeded(ip)
            return True
        if state == "device":
            self.reply_json(403, {"detail": "Das geht nur nach der Anmeldung im Browser"})
            return False
        if self.headers.get("Authorization"):
            self.limiter.failed(ip)
        if self.is_external() and self.security().has_passkeys():
            if self.command == "GET" and "text/html" in self.headers.get("Accept", ""):
                self.reply(302, headers={"Location": "/login"})
            else:
                self.reply_json(401, {"detail": "Anmeldung erforderlich"})
            return False
        self.reply(401, headers={"WWW-Authenticate": 'Basic realm="Home-Net"'})
        return False

    def security(self):
        return Security.shared(CONFIG_DIR)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            return {}
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def cookie_header(self, token, seconds):
        return f"{COOKIE}={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={seconds}"

    def handle_auth(self, path):
        """Everything under /auth and the login page. True when the request was answered."""
        store = self.security()
        ip = self.client_ip()
        if path == "/login" and self.command == "GET":
            with open(LOGIN, "rb") as f:
                self.reply(200, f.read(), "text/html; charset=utf-8")
            return True
        if not path.startswith("/auth/"):
            return False
        if self.limiter.blocked(ip):
            self.reply_json(429, {"detail": "Zu viele Fehlversuche, bitte in 15 Minuten erneut versuchen"})
            return True
        if self.command == "POST" and path in PUBLIC_AUTH:
            try:
                PUBLIC_AUTH[path](self, self.read_json(), store, ip)
            except (webauthn.WebAuthnError, ValueError, KeyError, TypeError) as exc:
                self.limiter.failed(ip)
                self.reply_json(400, {"detail": str(exc) or "Ungültige Anfrage"})
            return True
        if not self.authorized(admin=True):
            return True
        match = re.fullmatch(r"/auth/(passkeys|devices)/([A-Za-z0-9_-]+)", path)
        if path == "/auth/security" and self.command == "GET":
            self.reply_json(200, store.summary())
        elif path == "/auth/code" and self.command == "POST":
            kind = self.read_json().get("kind")
            if kind not in ("setup", "device"):
                self.reply_json(400, {"detail": "Unbekannte Art"})
            else:
                self.reply_json(200, {"code": store.new_code(kind)})
        elif path == "/auth/external" and self.command == "POST":
            try:
                store.set_external_url(str(self.read_json().get("url", "")))
                self.reply_json(200, {"ok": True})
            except ValueError as exc:
                self.reply_json(400, {"detail": str(exc)})
        elif path == "/auth/logout" and self.command == "POST":
            store.drop_session(self.session_token())
            self.reply_json(200, {"ok": True}, headers={"Set-Cookie": self.cookie_header("", 0)})
        elif match and self.command == "DELETE":
            ok = store.remove(match.group(1), match.group(2))
            self.reply_json(200 if ok else 404, {"ok": ok})
        else:
            self.reply_json(404, {"detail": "Nicht gefunden"})
        return True

    def modules(self):
        return load("gateway.json")["modules"]

    def handle_any(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply_json(200, {"ok": True})
            return
        if self.handle_auth(path):
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
                self.proxy(module, rest, authenticate=False)
                return
        if not self.authorized():
            return
        if module:
            self.proxy(module, match.group(2) or "/", authenticate=True)
        elif path == "/" and self.command in ("GET", "HEAD"):
            with open(PORTAL, "rb") as f:
                self.reply(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/modules" and self.command == "GET":
            self.reply_json(200, {"modules": [self.public_entry(m) for m in self.modules()]})
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

    def proxy(self, module, rest, authenticate):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.reply_json(413, {"detail": "Zu groß"})
            return
        body = self.rfile.read(length) if length else None
        url = urllib.parse.urlsplit(module["upstream"])
        query = urllib.parse.urlsplit(self.path).query
        target = rest + (f"?{query}" if query else "")
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP and k.lower() not in ("authorization", "accept-encoding")}
        if authenticate and module.get("password"):
            token = base64.b64encode(f"app:{module['password']}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        headers["X-Forwarded-For"] = self.client_ip()
        prefix = f"/m/{module['id']}"
        headers["X-Forwarded-Prefix"] = prefix
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=180)
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
            # A rejected gateway login never reaches this point, so this is a wrong module password.
            self.reply_json(502, {"detail": f"{module['name']} lehnt die Anmeldung des Gateways ab"})
            return
        self.reply(resp.status, data, content_type, extra)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = handle_any


def check_password(handler, body, ip):
    if not password_ok(str(body.get("password", ""))):
        handler.limiter.failed(ip)
        handler.reply_json(401, {"detail": "Passwort falsch"})
        return False
    return True


def login_options(handler, body, store, ip):
    """First step of a browser login: password right, now the passkey."""
    if not check_password(handler, body, ip):
        return
    data = store.load()
    if not data["passkeys"]:
        handler.reply_json(400, {"detail": "Noch kein Passkey eingerichtet"})
        return
    _, rp_id = store.origin_and_rp()
    ticket, challenge = store.new_challenge("login")
    handler.reply_json(200, {"ticket": ticket, "options": {
        "challenge": challenge, "rpId": rp_id, "userVerification": "required", "timeout": 60000,
        "allowCredentials": [{"type": "public-key", "id": p["id"]} for p in data["passkeys"]]}})


def login_finish(handler, body, store, ip):
    pending = store.take_challenge(body.get("ticket"), "login")
    if not pending:
        raise ValueError("Die Anmeldung ist abgelaufen, bitte von vorn beginnen")
    passkey = store.find_passkey(str(body.get("id", "")))
    if not passkey:
        raise ValueError("Unbekannter Passkey")
    origin, rp_id = store.origin_and_rp()
    count = webauthn.verify_assertion(body["clientData"], body["authData"], body["signature"], passkey,
                                      pending["challenge"], origin, rp_id)
    store.update_count(passkey["id"], count)
    handler.limiter.succeeded(ip)
    handler.reply_json(200, {"ok": True}, headers={"Set-Cookie": handler.cookie_header(store.new_session(), SESSION_SECONDS)})


def login_recovery(handler, body, store, ip):
    if not check_password(handler, body, ip):
        return
    if not store.use_recovery(body.get("code", "")):
        raise ValueError("Der Wiederherstellungscode stimmt nicht")
    handler.limiter.succeeded(ip)
    handler.reply_json(200, {"ok": True}, headers={"Set-Cookie": handler.cookie_header(store.new_session(), SESSION_SECONDS)})


def setup_options(handler, body, store, ip):
    """Adds a passkey. The code comes from the logged-in start page, so a password alone is not enough."""
    if not check_password(handler, body, ip):
        return
    origin, rp_id = store.origin_and_rp()
    if not origin:
        raise ValueError("Bitte zuerst die Adresse für unterwegs eintragen")
    if not store.take_code("setup", body.get("code", "")):
        raise ValueError("Der Einrichtungscode stimmt nicht oder ist abgelaufen")
    ticket, challenge = store.new_challenge("setup", name=str(body.get("name") or "Passkey")[:60])
    handler.reply_json(200, {"ticket": ticket, "options": {
        "challenge": challenge, "rp": {"id": rp_id, "name": "Home-Net"},
        "user": {"id": webauthn.b64url(b"home-net"), "name": "home-net", "displayName": "Home-Net"},
        "pubKeyCredParams": [{"type": "public-key", "alg": webauthn.ES256}], "timeout": 60000,
        "attestation": "none", "authenticatorSelection": {"residentKey": "preferred", "userVerification": "required"},
        "excludeCredentials": [{"type": "public-key", "id": p["id"]} for p in store.load()["passkeys"]]}})


def setup_finish(handler, body, store, ip):
    pending = store.take_challenge(body.get("ticket"), "setup")
    if not pending:
        raise ValueError("Die Einrichtung ist abgelaufen, bitte von vorn beginnen")
    origin, rp_id = store.origin_and_rp()
    credential_id, x, y, count = webauthn.verify_registration(body["clientData"], body["attestation"],
                                                              pending["challenge"], origin, rp_id)
    recovery = store.add_passkey(credential_id, x, y, count, pending["name"])
    store.drop_all_sessions()
    handler.limiter.succeeded(ip)
    handler.reply_json(200, {"recovery": recovery},
                       headers={"Set-Cookie": handler.cookie_header(store.new_session(), SESSION_SECONDS)})


def pair_device(handler, body, store, ip):
    """Gives an app its own key. The code comes from the logged-in start page."""
    if not check_password(handler, body, ip):
        return
    if not store.take_code("device", body.get("code", "")):
        raise ValueError("Der Kopplungscode stimmt nicht oder ist abgelaufen")
    handler.limiter.succeeded(ip)
    handler.reply_json(200, {"token": store.add_device(str(body.get("name") or "Handy")[:60])})


PUBLIC_AUTH = {"/auth/login": login_options, "/auth/login/finish": login_finish, "/auth/recovery": login_recovery,
               "/auth/setup": setup_options, "/auth/setup/finish": setup_finish, "/auth/pair": pair_device}


def module_online(module):
    url = urllib.parse.urlsplit(module["upstream"])
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
    try:
        conn.request("GET", module.get("health", "/health"))
        return conn.getresponse().status < 500
    except OSError:
        return False
    finally:
        conn.close()


def main():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
