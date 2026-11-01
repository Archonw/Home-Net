#!/usr/bin/env python3
"""The one address of Home-Net: login, portal page, module list for the app, and a proxy to each module."""
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
PORTAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portal.html")
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60
MAX_BODY = 200 * 1024 * 1024
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
              "proxy-authenticate", "host", "content-length"}


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

    def client_ip(self):
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

    def reply_json(self, code, payload):
        self.reply(code, json.dumps(payload, ensure_ascii=False).encode())

    def authorized(self):
        ip = self.client_ip()
        if self.limiter.blocked(ip):
            self.reply_json(429, {"detail": "Zu viele falsche Passwörter, bitte in 15 Minuten erneut versuchen"})
            return False
        header = self.headers.get("Authorization", "")
        password = ""
        if header.startswith("Basic "):
            try:
                password = base64.b64decode(header[6:]).decode().split(":", 1)[1]
            except (ValueError, IndexError):
                password = ""
        if password and password_ok(password):
            self.limiter.succeeded(ip)
            return True
        if header:
            self.limiter.failed(ip)
        self.reply(401, headers={"WWW-Authenticate": 'Basic realm="Home-Net"'})
        return False

    def modules(self):
        return load("gateway.json")["modules"]

    def handle_any(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply_json(200, {"ok": True})
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
