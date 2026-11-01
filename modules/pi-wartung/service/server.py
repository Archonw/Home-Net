#!/usr/bin/env python3
"""HTTP service that lets the Home-Net phone app see and start Pi-Wartung backups.

It runs in a container and reaches the Unraid host over ssh with a key whose forced
command only allows the verbs in cmd_api of the engine.
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from piwartung.core import (  # noqa: E402
    Pi,
    attention_items,
    parse_kv,
    parse_shell_kv,
    parse_summary,
    valid_category,
    valid_segment,
)

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
UNRAID_HOST = os.environ.get("UNRAID_HOST", "")
UNRAID_PORT = os.environ.get("UNRAID_PORT", "22")
ROOT = os.environ.get("BACKUP_ROOT", "/mnt/user/backup1/Raspis/PiWartung")
ENGINE_SOCKET = os.environ.get("ENGINE_SOCKET", "")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_FAILS = 10
FAIL_WINDOW = 15 * 60
DEVICE_MAX_AGE = 5 * 60
SUMMARY_MAX_AGE = 15


class BackendError(Exception):
    pass


class Backend:
    def host_call(self, verb, arg=None, timeout=60):
        words = [verb] + ([base64.b64encode(arg.encode()).decode()] if arg else [])
        if ENGINE_SOCKET:
            return self.socket_call(words, timeout)
        cmd = ["ssh", "-i", f"{CONFIG_DIR}/id_ed25519", "-p", UNRAID_PORT, "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=accept-new",
               "-o", f"UserKnownHostsFile={CONFIG_DIR}/known_hosts", f"root@{UNRAID_HOST}", " ".join(words)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise BackendError("Zeitüberschreitung beim Unraid")
        if p.returncode != 0:
            raise BackendError(p.stderr.strip() or f"Fehlercode {p.returncode}")
        return p.stdout


    def admin(self, timeout=120, **request):
        """A change through the engine; refused or failed changes raise BackendError."""
        return self.socket_line(json.dumps(request), timeout)

    @staticmethod
    def socket_call(words, timeout):
        return Backend.socket_line(" ".join(words), timeout)

    @staticmethod
    def socket_line(line, timeout):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(ENGINE_SOCKET)
                s.sendall((line + "\n").encode())
                s.shutdown(socket.SHUT_WR)
                chunks = []
                while chunk := s.recv(65536):
                    chunks.append(chunk)
        except OSError as exc:
            raise BackendError(f"Sicherungsdienst nicht erreichbar: {exc}")
        reply = json.loads(b"".join(chunks))
        if reply["code"] != 0:
            raise BackendError(reply["err"].strip() or f"Fehlercode {reply['code']}")
        return reply["out"]


class Store:
    """Caches what the phone polls, so that many requests never turn into many ssh logins."""

    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.summary = None
        self.summary_time = 0
        self.pis = []
        self.devices = {}
        self.devices_time = 0
        self.checking = False

    def pidir(self, pi):
        return "/".join([ROOT] + ([pi.category] if pi.category else []) + [pi.name])

    def refresh(self):
        with self.lock:
            if self.summary and time.time() - self.summary_time < SUMMARY_MAX_AGE:
                return
            inventory = json.loads(self.backend.host_call("inventory") or "{}")
            known = set(Pi.__dataclass_fields__)
            self.pis = [Pi(**{k: v for k, v in p.items() if k in known}) for p in inventory.get("pis", [])]
            self.summary = parse_summary(self.backend.host_call("summary", timeout=120))
            self.summary_time = time.time()
        self.check_devices()

    def check_devices(self, force=False):
        with self.lock:
            if self.checking or (not force and time.time() - self.devices_time < DEVICE_MAX_AGE):
                return
            self.checking = True
            pis = list(self.pis)

        def one(pi):
            try:
                return pi.id, parse_kv(self.backend.host_call("status", self.pidir(pi)))
            except BackendError:
                return pi.id, {}

        def work():
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    result = dict(pool.map(one, pis))
                with self.lock:
                    self.devices = result
                    self.devices_time = time.time()
            finally:
                with self.lock:
                    self.checking = False

        threading.Thread(target=work, daemon=True).start()

    def invalidate(self):
        with self.lock:
            self.summary_time = 0

    def overview(self):
        self.refresh()
        with self.lock:
            s, pis, devices = self.summary, list(self.pis), dict(self.devices)
            checked = self.devices_time
        dirs = {p.id: self.pidir(p) for p in pis}
        rows = []
        for p in sorted(pis, key=lambda x: (x.category.casefold(), x.name.casefold())):
            rows.append(pi_row(p, s["pis"].get(dirs[p.id], {}), devices.get(p.id)))
        names = {dirs[p.id]: p.name for p in pis}
        failed = sorted((r for r in s["runs"] if r["status"] == "failed"), key=lambda r: r["stamp"], reverse=True)
        return {
            "pis": rows,
            "images": sum(r["images"] for r in rows),
            "images_bytes": sum(r["bytes"] for r in rows),
            "mirror_bytes": sum(s["pis"].get(dirs[p.id], {}).get("mirror", 0) for p in pis),
            "avail_bytes": s["avail"],
            "total_bytes": s["total"],
            "running": sum(1 for r in rows if r["running"]),
            "attention": [{"serious": i[0] == 0, "pi": i[1], "text": i[2]}
                          for i in attention_items(pis, dirs, s, devices, time.time())],
            "errors": [{"stamp": r["stamp"], "pi": names.get(r["dir"], ""), "message": r["error"] or "Unbekannt"}
                       for r in failed[:10]],
            "devices_checked": int(checked),
        }

    def inventory(self):
        data = json.loads(self.backend.host_call("inventory") or "{}")
        known = set(Pi.__dataclass_fields__)
        return data.get("categories", []), [Pi(**{k: v for k, v in p.items() if k in known}) for p in data.get("pis", [])]

    def save_inventory(self, categories, pis):
        text = json.dumps({"categories": sorted(categories), "pis": [asdict(p) for p in pis]}, indent=2,
                          ensure_ascii=False) + "\n"
        self.backend.admin(verb="put-file", path=f"{ROOT}/inventory.json", text=text)
        self.invalidate()

    def write_conf(self, pi):
        self.backend.admin(verb="put-file", path=f"{self.pidir(pi)}/pi.conf", text=pi.conf_text())

    def form_inventory(self):
        categories, pis = self.inventory()
        cats = set(categories)
        for pi in pis:
            parts = pi.category.split("/") if pi.category else []
            cats.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return {"categories": sorted(cats, key=str.casefold), "pis": [asdict(p) for p in pis]}

    def create_pi(self, form):
        categories, pis = self.inventory()
        pi = pi_from_form(form)
        pi.key = pi.own_key_path()
        self.check_free(pi, pis)
        self.write_conf(pi)
        self.save_inventory(set(categories) | ({pi.category} if pi.category else set()), pis + [pi])
        return pi

    def update_pi(self, pi_id, form):
        categories, pis = self.inventory()
        old = next((p for p in pis if p.id == pi_id), None)
        if not old:
            raise LookupError
        new = pi_from_form(form, old)
        self.check_free(new, pis)
        if self.pidir(old) != self.pidir(new):
            if parse_shell_kv(self.backend.host_call("state", self.pidir(old))).get("RUNNING") == "1":
                raise ValueError("Während einer Sicherung lassen sich Name und Kategorie nicht ändern.")
            self.backend.admin(verb="move", src=self.pidir(old), dst=self.pidir(new))
            if old.name != new.name:
                self.backend.admin(verb="rename-images", dir=self.pidir(new), old=old.name, new=new.name)
        self.write_conf(new)
        self.save_inventory(set(categories) | ({new.category} if new.category else set()),
                            [new if p.id == pi_id else p for p in pis])
        return new

    def delete_pi(self, pi_id, with_data):
        categories, pis = self.inventory()
        old = next((p for p in pis if p.id == pi_id), None)
        if not old:
            raise LookupError
        if with_data:
            self.backend.admin(verb="delete", dir=self.pidir(old))
        self.save_inventory(categories, [p for p in pis if p.id != pi_id])

    @staticmethod
    def check_free(pi, pis):
        if any(p.name == pi.name and p.category == pi.category and p.id != pi.id for p in pis):
            raise ValueError("In dieser Kategorie gibt es schon einen Pi mit diesem Namen.")

    def install_key(self, pi, password):
        fresh = copy.copy(pi)
        fresh.key = fresh.own_key_path()
        self.write_conf(fresh)
        try:
            out = self.backend.admin(verb="install-key", dir=self.pidir(fresh), password=password)
        except BackendError:
            self.write_conf(pi)
            raise
        categories, pis = self.inventory()
        self.save_inventory(categories, [fresh if p.id == pi.id else p for p in pis])
        return parse_kv(out)

    def ntfy(self):
        values = parse_shell_kv(self.backend.admin(verb="read-file", path=f"{ROOT}/.engine/ntfy.conf"))
        return {"url": values.get("NTFY_URL", ""), "topic": values.get("NTFY_TOPIC", ""),
                "has_token": bool(values.get("NTFY_TOKEN"))}

    def save_ntfy(self, form):
        current = parse_shell_kv(self.backend.admin(verb="read-file", path=f"{ROOT}/.engine/ntfy.conf"))
        token = str(form["token"]) if form.get("token") else current.get("NTFY_TOKEN", "")
        if form.get("remove_token"):
            token = ""
        pairs = (("NTFY_URL", str(form.get("url", "")).strip().rstrip("/")), ("NTFY_TOPIC", str(form.get("topic", "")).strip()),
                 ("NTFY_TOKEN", token))
        text = "".join(f"{k}={shlex.quote(v)}\n" for k, v in pairs)
        self.backend.admin(verb="put-file", path=f"{ROOT}/.engine/ntfy.conf", text=text)

    def find(self, pi_id):
        self.refresh()
        with self.lock:
            return next((p for p in self.pis if p.id == pi_id), None)

    def detail(self, pi):
        d = self.pidir(pi)
        with self.lock:
            row = self.summary["pis"].get(d, {})
            device = self.devices.get(pi.id)
        images = []
        for line in self.backend.host_call("images", d).splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                info = parse_kv(parts[3].replace("\x1e", "\n")) if len(parts) > 3 else {}
                images.append({"file": parts[0], "size": int(parts[1]), "time": int(parts[2]), "os": info.get("os", "")})
        state = parse_shell_kv(self.backend.host_call("state", d))
        running = state.get("RUNNING") == "1"
        out = pi_row(pi, row, device)
        out.update(running=running, images_list=images, error=state.get("LAST_ERROR", ""),
                   warnings=state.get("LAST_WARNINGS", ""), notes=pi.notes, device=device or {},
                   progress=parse_progress(self.backend.host_call("progress", d)) if running else None,
                   host=pi.host, user=pi.user, port=pi.port)
        return out


EDITABLE = {"name": str, "category": str, "host": str, "port": int, "user": str, "key": str, "jump": str,
            "jump_port": int, "keep": int, "format": str, "expand": bool, "excludes": str, "bwlimit": int,
            "compress": bool, "pre_cmd": str, "post_cmd": str, "schedule": str, "hour": int, "notify": str,
            "notes": str}
CHOICES = {"format": ("xz", "gz", "img"), "schedule": ("off", "daily", "weekly", "monthly"),
           "notify": ("errors", "always", "off")}


def pi_from_form(form, base=None):
    """A validated Pi from form values; raises ValueError with a message for the user."""
    pi = copy.copy(base) if base else Pi()
    for key, kind in EDITABLE.items():
        if key not in form:
            continue
        value = form[key]
        if kind is int:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} muss eine Zahl sein")
        elif kind is bool:
            value = bool(value)
        else:
            value = str(value).strip() if key not in ("excludes", "notes") else str(value)
        setattr(pi, key, value)
    if not valid_segment(pi.name):
        raise ValueError("Der Name darf nicht leer sein und keine Sonderzeichen wie / oder : enthalten.")
    if not valid_category(pi.category):
        raise ValueError("Die Kategorie enthält ungültige Zeichen.")
    if not pi.host or re.search(r"\s", pi.host):
        raise ValueError("Bitte eine Adresse ohne Leerzeichen angeben.")
    if not pi.user or re.search(r"\s", pi.user):
        raise ValueError("Bitte einen Benutzer angeben.")
    if not 1 <= pi.port <= 65535 or not 1 <= pi.jump_port <= 65535:
        raise ValueError("Der Port muss zwischen 1 und 65535 liegen.")
    if not 0 <= pi.hour <= 23:
        raise ValueError("Die Stunde muss zwischen 0 und 23 liegen.")
    if pi.keep < 0 or pi.bwlimit < 0:
        raise ValueError("Negative Werte sind nicht möglich.")
    for key, allowed in CHOICES.items():
        if getattr(pi, key) not in allowed:
            raise ValueError(f"Ungültiger Wert für {key}")
    return pi


def pi_row(pi, row, device):
    parts = device.get("disk", "").split() if device else []
    return {
        "id": pi.id, "name": pi.name, "category": pi.category, "running": row.get("running", False),
        "status": row.get("status", ""), "ok_time": row.get("ok_time", 0), "images": row.get("count", 0),
        "bytes": row.get("bytes", 0), "schedule": row.get("schedule", pi.schedule),
        "online": None if device is None else bool(device),
        "card": parts[2] if len(parts) == 3 else "", "temp": device.get("temp", "") if device else "",
    }


def parse_progress(text):
    out = {"step": "", "percent": 0, "detail": "", "log": []}
    for line in text.splitlines():
        if line.startswith("@@STEP"):
            parts = line.split(" ", 3)
            if len(parts) == 4:
                out["step"] = f"Schritt {parts[1]} von {parts[2]}: {parts[3]}"
        elif line.startswith("@@PROGRESS"):
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                out["percent"] = min(int(parts[1]), 100)
            out["detail"] = parts[2] if len(parts) > 2 else ""
        else:
            out["log"].append(line)
    return out


class Auth:
    def __init__(self, path):
        self.path = path
        self.fails = {}
        self.lock = threading.Lock()

    def blocked(self, ip):
        with self.lock:
            now = time.time()
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = recent
            return len(recent) >= MAX_FAILS

    def check(self, password):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None  # no password set yet
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(data["salt"]), data["rounds"])
        return hmac.compare_digest(digest.hex(), data["hash"])

    def failed(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def succeeded(self, ip):
        with self.lock:
            self.fails.pop(ip, None)


class Handler(BaseHTTPRequestHandler):
    store = None
    auth = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Addresses and paths only; the authorization header is never logged.
        sys.stderr.write(f"{self.client_ip()} {fmt % args}\n")

    def client_ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    def send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        ip = self.client_ip()
        if self.auth.blocked(ip):
            self.send_json(429, {"detail": "Zu viele falsche Passwörter, bitte in 15 Minuten erneut versuchen"})
            return False
        header = self.headers.get("Authorization", "")
        password = ""
        if header.startswith("Basic "):
            try:
                password = base64.b64decode(header[6:]).decode().split(":", 1)[1]
            except (ValueError, IndexError):
                password = ""
        result = self.auth.check(password)
        if result is None:
            self.send_json(503, {"detail": "Noch kein Passwort gesetzt, bitte in der Pi-Wartung-App am PC festlegen"})
            return False
        if not result:
            self.auth.failed(ip)
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Pi-Wartung"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        self.auth.succeeded(ip)
        return True

    def route(self, method):
        path = self.path.split("?", 1)[0]
        if method == "GET" and path == "/health":
            self.send_json(200, {"ok": True})
            return
        if not self.authorized():
            return
        try:
            self.dispatch(method, path)
        except BackendError as exc:
            self.send_json(502, {"detail": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})

    def send_page(self):
        with open(PAGE, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method, path):
        if self.dispatch_admin(method, path):
            return
        if method == "GET" and path == "/api/overview":
            self.send_json(200, self.store.overview())
            return
        if method == "POST" and path == "/api/check":
            self.store.refresh()
            self.store.check_devices(force=True)
            self.send_json(200, {"ok": True})
            return
        match = re.fullmatch(r"/api/pis/([0-9a-f]{1,64})(/backup|/cancel)?", path)
        if not match:
            self.send_json(404, {"detail": "Nicht gefunden"})
            return
        pi = self.store.find(match.group(1))
        if not pi:
            self.send_json(404, {"detail": "Pi nicht gefunden"})
            return
        action = match.group(2)
        if method == "GET" and not action:
            self.send_json(200, self.store.detail(pi))
        elif method == "POST" and action == "/backup":
            self.store.backend.host_call("start", self.store.pidir(pi))
            self.store.invalidate()
            self.send_json(202, {"ok": True})
        elif method == "POST" and action == "/cancel":
            self.store.backend.host_call("cancel", self.store.pidir(pi))
            self.store.invalidate()
            self.send_json(202, {"ok": True})
        else:
            self.send_json(405, {"detail": "Nicht erlaubt"})

    def dispatch_admin(self, method, path):
        """The parts the web interface needs to manage Pis. True when the request was answered."""
        store = self.store
        if method == "GET" and path == "/":
            self.send_page()
        elif method == "GET" and path == "/api/inventory":
            self.send_json(200, store.form_inventory())
        elif method == "POST" and path == "/api/pis":
            form = self.body
            pi = store.create_pi(form)
            sudo = store.install_key(pi, form["password"]).get("sudo") if form.get("password") else None
            self.send_json(201, {"id": pi.id, "sudo": sudo})
        elif method == "GET" and path == "/api/ntfy":
            self.send_json(200, store.ntfy())
        elif method == "PUT" and path == "/api/ntfy":
            store.save_ntfy(self.body)
            self.send_json(200, {"ok": True})
        elif method == "POST" and path == "/api/ntfy/test":
            store.backend.admin(verb="ntfy-test")
            self.send_json(200, {"ok": True})
        else:
            match = re.fullmatch(r"/api/pis/([0-9a-f]{1,64})(?:/(config|key|images/[^/]+))?", path)
            if not match or (method == "GET" and match.group(2) != "config") or (method == "POST" and match.group(2) != "key"):
                return False
            pi_id, part = match.groups()
            if method == "GET":
                _, pis = store.inventory()
                pi = next((p for p in pis if p.id == pi_id), None)
                if not pi:
                    raise LookupError
                self.send_json(200, asdict(pi))
            elif method == "PUT" and not part:
                store.update_pi(pi_id, self.body)
                self.send_json(200, {"ok": True})
            elif method == "DELETE" and not part:
                store.delete_pi(pi_id, bool(self.body.get("data")))
                self.send_json(200, {"ok": True})
            elif method == "DELETE" and part.startswith("images/"):
                pi = store.find(pi_id)
                if not pi:
                    raise LookupError
                store.backend.admin(verb="delete-image", dir=store.pidir(pi), file=urllib.parse.unquote(part[7:]))
                store.invalidate()
                self.send_json(200, {"ok": True})
            elif method == "POST" and part == "key":
                pi = store.find(pi_id)
                if not pi:
                    raise LookupError
                self.send_json(200, {"sudo": store.install_key(pi, str(self.body.get("password", ""))).get("sudo")})
            else:
                return False
        return True

    def do_GET(self):
        self.route("GET")

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 1024 * 1024)) if length else b""
        try:
            self.body = json.loads(raw) if raw else {}
        except ValueError:
            self.body = {}
        if not isinstance(self.body, dict):
            self.body = {}

    def do_POST(self):
        self.read_body()
        self.route("POST")

    def do_PUT(self):
        self.read_body()
        self.route("PUT")

    def do_DELETE(self):
        self.read_body()
        self.route("DELETE")


def main():
    Handler.store = Store(Backend())
    Handler.auth = Auth(f"{CONFIG_DIR}/auth.json")
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
