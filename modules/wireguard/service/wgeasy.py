"""Talks to the REST API of WireGuard-Easy (https://wg-easy.github.io/wg-easy/), version 15 or newer.

The API accepts HTTP Basic Authentication with the login of the web interface. That does not work for accounts with
two-factor authentication, so Home-Net needs an account without it.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

PAGE_SIZE = 100
TIMEOUT = 15
ONLINE_SECONDS = 180  # a handshake younger than this means the device is connected


class WgEasyError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class WgEasy:
    def __init__(self, settings):
        self.settings = settings  # callable returning {"url": ..., "username": ..., "password": ...}

    # --- HTTP ------------------------------------------------------------------------------------

    def base(self):
        url = (self.settings().get("url") or "").rstrip("/")
        if not url:
            raise WgEasyError("Die Adresse von WireGuard-Easy fehlt. Bitte unter Einstellungen eintragen.", 400)
        return url

    def request(self, method, path, body=None, query=None, raw=False):
        s = self.settings()
        if not s.get("username") or not s.get("password"):
            raise WgEasyError("Die Anmeldung für WireGuard-Easy fehlt. Bitte unter Einstellungen eintragen.", 400)
        base = self.base()
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        token = base64.b64encode(f"{s['username']}:{s['password']}".encode()).decode()
        headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                   # wg-easy checks the origin of changes when TRUSTED_PROXIES is set
                   "Origin": base_origin(base)}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                content = resp.read()
        except urllib.error.HTTPError as exc:
            raise WgEasyError(self.explain(exc), 400 if exc.code in (400, 404, 409) else 502)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise WgEasyError(f"WireGuard-Easy ist nicht erreichbar ({reason}).")
        if raw:
            return content
        try:
            return json.loads(content) if content else {}
        except ValueError:
            raise WgEasyError("WireGuard-Easy antwortet unerwartet. Läuft dort Version 15 oder neuer?")

    @staticmethod
    def explain(exc):
        try:
            detail = json.loads(exc.read() or b"{}")
            detail = detail.get("statusMessage") or detail.get("message") or ""
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 401:
            return ("WireGuard-Easy lehnt die Anmeldung ab. Stimmen Benutzername und Passwort? "
                    "Ein Konto mit Zwei-Faktor-Anmeldung geht hier nicht.")
        if exc.code == 403:
            return "WireGuard-Easy verweigert den Zugriff" + (f" ({detail})." if detail else ".")
        if exc.code == 404:
            return "In WireGuard-Easy nicht gefunden. Läuft dort Version 15 oder neuer?"
        return f"WireGuard-Easy meldet: {detail}" if detail else f"WireGuard-Easy antwortet mit Fehler {exc.code}."

    # --- Lesen -----------------------------------------------------------------------------------

    def information(self):
        try:
            return self.request("GET", "/api/information")
        except WgEasyError:
            return {}

    def clients(self):
        out, page = [], 1
        while True:
            data = self.request("GET", "/api/client", query={"page": page, "pageSize": PAGE_SIZE})
            if isinstance(data, list):  # 15.0 to 15.3 return everything at once
                return data
            items = data.get("clients") or []
            out.extend(items)
            if not items or len(out) >= int(data.get("total") or 0):
                return out
            page += 1

    def overview(self):
        now = time.time()
        rows = [public_client(c, now) for c in self.clients()]
        rows.sort(key=lambda r: r["name"].lower())
        info = self.information()
        return {"clients": rows, "version": info.get("currentRelease", ""),
                "update": bool(info.get("updateAvailable")),
                "latest": (info.get("latestRelease") or {}).get("version", "")}

    def configuration(self, client_id):
        text = self.request("GET", f"/api/client/{int(client_id)}/configuration", raw=True).decode()
        if "[Interface]" not in text:
            raise WgEasyError("WireGuard-Easy hat keine Konfiguration geliefert.")
        return text

    def qrcode(self, client_id):
        return self.request("GET", f"/api/client/{int(client_id)}/qrcode.svg", raw=True)

    # --- Ändern ----------------------------------------------------------------------------------

    def create(self, name, expires=None):
        name = clean_name(name)
        if any(c.get("name", "").lower() == name.lower() for c in self.clients()):
            raise WgEasyError(f"Einen Zugang „{name}“ gibt es schon.", 400)
        result = self.request("POST", "/api/client", {"name": name, "expiresAt": expires or None})
        return {"id": result.get("clientId"), "name": name}

    def set_enabled(self, client_id, on):
        self.request("POST", f"/api/client/{int(client_id)}/{'enable' if on else 'disable'}")

    def delete(self, client_id):
        self.request("DELETE", f"/api/client/{int(client_id)}")


def base_origin(url):
    split = urllib.parse.urlsplit(url)
    return f"{split.scheme}://{split.netloc}"


def clean_name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise WgEasyError("Bitte einen Namen angeben, z. B. „Handy Jan“.", 400)
    if len(name) > 64:
        raise WgEasyError("Der Name ist zu lang (höchstens 64 Zeichen).", 400)
    return name


def epoch(value):
    """wg-easy sends ISO dates (or null); the app gets seconds since 1970, 0 for none."""
    if not value:
        return 0
    try:
        text = str(value).replace("Z", "+00:00")
        if len(text) == 10:
            text += "T00:00:00+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def public_client(c, now=None):
    now = now or time.time()
    handshake = epoch(c.get("latestHandshakeAt"))
    expires = epoch(c.get("expiresAt"))
    return {"id": c.get("id"), "name": c.get("name") or f"Zugang {c.get('id')}",
            "enabled": bool(c.get("enabled")), "address": c.get("ipv4Address") or c.get("address") or "",
            "created": epoch(c.get("createdAt")), "expires": expires, "expired": bool(expires and expires < now),
            "handshake": handshake, "online": bool(handshake and now - handshake < ONLINE_SECONDS),
            "rx": int(c.get("transferRx") or 0), "tx": int(c.get("transferTx") or 0)}
