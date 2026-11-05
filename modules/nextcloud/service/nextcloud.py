"""Talks to Nextcloud (https://nextcloud.com) with the user name and an app password of an admin account.

Read: /status.php (no sign-in), the app serverinfo (system, storage, active users, updates) and the OCS Provisioning
API (users, groups, apps). Write: only the Provisioning API (create, change, lock, delete users; groups).

All OCS calls go to /ocs/v2.php, which answers with real HTTP status codes, and send JSON bodies.
"""
import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20
USER_PAGE = 500         # Nextcloud refuses limit > 500 with error 400
USER_MAX = 20000
STORAGE_WARN = 20 * 1024 ** 3   # free bytes on the data disk below which the module warns
QUOTA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB)", re.I)
USER_ID_RE = re.compile(r"[A-Za-z0-9_.@\- ']{1,64}")


# Nextcloud 32+ wants the account password for every change of users ("password confirmation"), which an app password
# cannot give. The pages then ask for it once; it is kept in memory for CONFIRM_FOR seconds, like Nextcloud's own
# 30 minutes, and never written to disk. Clients recognise this exact text.
CONFIRM_TEXT = "Nextcloud verlangt eine Bestätigung mit dem Passwort."
CONFIRM_FOR = 30 * 60


class NextcloudError(Exception):
    def __init__(self, message, status=502, confirm=False):
        super().__init__(message)
        self.status = status
        self.confirm = confirm


def quota_text(value):
    """Turns what the user typed into what Nextcloud takes: "none" (no limit), "default" or e.g. "5 GB"."""
    text = str(value or "").strip()
    if text.lower() in ("", "default", "standard"):
        return "default"
    if text.lower() in ("none", "unbegrenzt", "unlimited", "-3"):
        return "none"
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        text += " GB"
    match = QUOTA_RE.fullmatch(text)
    if not match:
        raise ValueError("Speicherplatz bitte als Zahl mit Einheit angeben, z. B. 5 GB, oder „unbegrenzt“.")
    return f"{match.group(1).replace(',', '.')} {match.group(2).upper()}"


def check_user_id(user_id):
    user_id = str(user_id or "").strip()
    if not USER_ID_RE.fullmatch(user_id):
        raise ValueError("Der Benutzername darf nur Buchstaben, Ziffern und _ . @ - enthalten (höchstens 64 Zeichen).")
    return user_id


class Nextcloud:
    def __init__(self, settings):
        self.settings = settings  # callable returning {"url", "user", "password", "verify_tls"}
        self._cache = (0.0, None)
        self._confirmed = ("", 0.0)  # account password and until when (monotonic)

    # --- HTTP ------------------------------------------------------------------------------------

    def base(self):
        url = (self.settings().get("url") or "").rstrip("/")
        if not url:
            raise NextcloudError("Die Adresse der Nextcloud fehlt. Bitte unter Einstellungen eintragen.", 400)
        return url

    def context(self):
        if self.settings().get("verify_tls", True):
            return ssl.create_default_context()
        return ssl._create_unverified_context()  # eigenes Zertifikat im Heimnetz, vom Admin so gewählt

    def open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=self.context()) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            text = self.explain(exc)
            if text == CONFIRM_TEXT:
                raise NextcloudError(text, 403, confirm=True)
            raise NextcloudError(text, 400 if exc.code in (400, 404, 409) else 502)
        except ssl.SSLCertVerificationError:
            raise NextcloudError("Das Zertifikat der Nextcloud passt nicht. Für ein eigenes Zertifikat im Heimnetz "
                                 "unter Einstellungen „Zertifikat nicht prüfen“ wählen.")
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise NextcloudError("Das Zertifikat der Nextcloud passt nicht. Für ein eigenes Zertifikat im "
                                     "Heimnetz unter Einstellungen „Zertifikat nicht prüfen“ wählen.")
            raise NextcloudError(f"Nextcloud ist nicht erreichbar ({reason}).")

    def ocs(self, method, path, body=None, query=None, password=None):
        """password: the account password instead of the app password (only for confirmed changes)."""
        s = self.settings()
        if not s.get("user") or not s.get("password"):
            raise NextcloudError("Benutzername und App-Passwort fehlen. Bitte unter Einstellungen eintragen.", 400)
        url = self.base() + "/ocs/v2.php" + path + "?" + urllib.parse.urlencode({"format": "json", **(query or {})})
        data = json.dumps(body).encode() if body is not None else None
        token = base64.b64encode(f"{s['user']}:{password or s['password']}".encode()).decode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Basic {token}", "OCS-APIRequest": "true", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        raw = self.open(req)
        try:
            out = json.loads(raw)["ocs"]
        except (ValueError, KeyError, TypeError):
            raise NextcloudError("Nextcloud antwortet nicht wie erwartet. Stimmt die Adresse?")
        return out.get("data")

    @staticmethod
    def explain(exc):
        try:
            meta = json.loads(exc.read() or b"{}").get("ocs", {}).get("meta", {})
            detail = meta.get("message") or ""
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 401:
            return "Nextcloud lehnt die Anmeldung ab. Stimmen Benutzername und App-Passwort?"
        if exc.code == 403 and "password confirmation" in detail.lower():
            return CONFIRM_TEXT
        if exc.code == 403:
            return detail or "Das Konto für Home-Net ist in Nextcloud kein Admin."
        if exc.code == 404:
            return detail or "In Nextcloud nicht gefunden."
        if exc.code == 400 and "trusted" in (detail or "").lower():
            return "Nextcloud kennt diese Adresse nicht (trusted_domains)."
        return f"Nextcloud meldet: {detail}" if detail else f"Nextcloud antwortet mit Fehler {exc.code}."

    # --- Lesen -----------------------------------------------------------------------------------

    def status(self):
        """/status.php needs no sign-in: whether Nextcloud answers, its version and maintenance mode."""
        req = urllib.request.Request(self.base() + "/status.php", headers={"Accept": "application/json"})
        try:
            data = json.loads(self.open(req))
        except ValueError:
            raise NextcloudError("Unter der Adresse antwortet keine Nextcloud.")
        return {"installed": bool(data.get("installed")), "maintenance": bool(data.get("maintenance")),
                "needs_upgrade": bool(data.get("needsDbUpgrade")),
                "version": str(data.get("versionstring") or data.get("version") or ""),
                "name": str(data.get("productname") or "Nextcloud")}

    def serverinfo(self):
        """System, storage and updates from the app serverinfo; None when it is switched off."""
        try:
            data = self.ocs("GET", "/apps/serverinfo/api/v1/info", query={"skipApps": "false", "skipUpdate": "false"})
        except NextcloudError as exc:
            if exc.status == 400:  # 404: app not enabled
                return None
            raise
        nc = (data or {}).get("nextcloud") or {}
        system = nc.get("system") or {}
        storage = nc.get("storage") or {}
        server = (data or {}).get("server") or {}
        active = (data or {}).get("activeUsers") or {}
        apps = system.get("apps") or {}
        update = system.get("update") or {}
        app_updates = apps.get("app_updates") or {}
        if not isinstance(app_updates, dict):
            app_updates = {}
        free = number(system.get("freespace"))
        db = server.get("database") or {}
        return {
            "free_bytes": free,
            "storage_low": free is not None and free < STORAGE_WARN,
            "files": int(number(storage.get("num_files")) or 0),
            "shares": int(number((nc.get("shares") or {}).get("num_shares")) or 0),
            "mem_total": kib(system.get("mem_total")),
            "mem_free": kib(system.get("mem_free")),
            "cpu_load": [round(float(x), 2) for x in (system.get("cpuload") or [])[:3] if isinstance(x, (int, float))],
            "php": str((server.get("php") or {}).get("version") or ""),
            "database": " ".join(str(x) for x in (db.get("type"), db.get("version")) if x),
            "database_bytes": number(db.get("size")),
            "active": {"5min": int(number(active.get("last5minutes")) or 0),
                       "1h": int(number(active.get("last1hour")) or 0),
                       "24h": int(number(active.get("last24hours")) or 0)},
            "apps_installed": int(number(apps.get("num_installed")) or 0),
            "app_updates": [{"id": str(k), "version": str(v)} for k, v in sorted(app_updates.items())],
            "server_update": str(update.get("available_version") or "") if update.get("available") else "",
        }

    def users(self):
        items, offset = [], 0
        while offset < USER_MAX:
            data = self.ocs("GET", "/cloud/users/details", query={"limit": USER_PAGE, "offset": offset}) or {}
            raw = data.get("users") or {}
            page = list(raw.values() if isinstance(raw, dict) else raw)
            items.extend(page)
            if len(page) < USER_PAGE:
                break
            offset += USER_PAGE
        out, seen = [], set()
        for u in items:
            if isinstance(u, dict) and u.get("id") in seen:
                continue
            seen.add(u.get("id") if isinstance(u, dict) else None)
            if not isinstance(u, dict) or not u.get("id"):
                continue
            q = u.get("quota") or {}
            limit = q.get("quota")
            out.append({
                "id": str(u["id"]),
                "name": str(u.get("displayname") or u.get("display-name") or u["id"]),
                "email": str(u.get("email") or ""),
                "enabled": bool(u.get("enabled", True)),
                "groups": [str(g) for g in u.get("groups") or []],
                "admin": "admin" in (u.get("groups") or []),
                "used": number(q.get("used")),
                "quota": quota_limit(limit),
                "quota_text": quota_label(limit),
                "last_login": int(number(u.get("lastLogin")) or 0) // 1000,
                "backend": str(u.get("backend") or ""),
                "can_password": bool((u.get("backendCapabilities") or {}).get("setPassword", u.get("backend") == "Database")),
            })
        return sorted(out, key=lambda u: u["name"].lower())

    def groups(self):
        data = self.ocs("GET", "/cloud/groups") or {}
        return sorted((str(g) for g in data.get("groups") or []), key=str.lower)

    def overview(self, full=True):
        """Everything for the pages; cached for a few seconds because the app asks for it on every screen."""
        stamp, cached = self._cache
        if cached is not None and time.time() - stamp < 5 and cached["full"] == full:
            return cached["data"]
        status = self.status()
        info = self.serverinfo()
        out = {"status": status, "info": info, "users": [], "groups": [], "user_count": None}
        if full:
            out["users"] = self.users()
            out["groups"] = self.groups()
            out["user_count"] = len(out["users"])
        self._cache = (time.time(), {"full": full, "data": out})
        return out

    def forget(self):
        self._cache = (0.0, None)

    # --- Ändern ----------------------------------------------------------------------------------

    def confirm(self, password):
        """Checks the account password with a harmless call; on success changes use it for CONFIRM_FOR seconds."""
        password = str(password or "")
        if not password or len(password) > 300:
            raise ValueError("Bitte das Passwort eingeben.")
        if password == self.settings().get("password"):
            raise ValueError("Das ist das App-Passwort. Gemeint ist das normale Passwort des Kontos in Nextcloud.")
        try:
            self.ocs("GET", "/cloud/user", password=password)
        except NextcloudError as exc:
            if "lehnt die Anmeldung ab" in str(exc):
                raise ValueError("Nextcloud lehnt das Passwort ab. Gemeint ist das normale Passwort des Kontos, nicht "
                                 "das App-Passwort. Mit Zwei-Faktor-Anmeldung geht es so nicht, dann hilft nur "
                                 "allowed_no_password_confirmation_ranges (siehe Anleitung).")
            raise
        self._confirmed = (password, time.monotonic() + CONFIRM_FOR)

    def confirmed(self):
        password, until = self._confirmed
        return password if password and time.monotonic() < until else None

    def write(self, method, path, body=None, query=None):
        password = self.confirmed()
        try:
            return self.ocs(method, path, body, query, password=password)
        except NextcloudError as exc:
            if exc.confirm:
                self._confirmed = ("", 0.0)
            raise

    def create_user(self, user_id, name="", email="", password="", groups=(), quota=""):
        user_id = check_user_id(user_id)
        if not password and not email:
            raise ValueError("Bitte ein Passwort oder eine E-Mail-Adresse angeben. Mit E-Mail schickt Nextcloud einen "
                             "Link zum Festlegen des Passworts.")
        body = {"userid": user_id, "groups": list(groups)}
        if name:
            body["displayName"] = name
        if email:
            body["email"] = email
        if password:
            body["password"] = password
        if quota:
            body["quota"] = quota_text(quota)
        self.write("POST", "/cloud/users", body)
        self.forget()

    def set_field(self, user_id, key, value):
        if key not in ("displayname", "email", "quota", "password"):
            raise ValueError("Unbekanntes Feld")
        self.write("PUT", "/cloud/users/" + quote(user_id), {"key": key, "value": value})
        self.forget()

    def set_enabled(self, user_id, enabled):
        self.write("PUT", "/cloud/users/" + quote(user_id) + ("/enable" if enabled else "/disable"), {})
        self.forget()

    def set_group(self, user_id, group, member):
        if member:
            self.write("POST", "/cloud/users/" + quote(user_id) + "/groups", {"groupid": group})
        else:
            self.write("DELETE", "/cloud/users/" + quote(user_id) + "/groups", query={"groupid": group})
        self.forget()

    def welcome(self, user_id):
        self.write("POST", "/cloud/users/" + quote(user_id) + "/welcome", {})

    def delete_user(self, user_id):
        self.write("DELETE", "/cloud/users/" + quote(user_id))
        self.forget()

    def create_group(self, group):
        group = str(group or "").strip()
        if not group or len(group) > 64:
            raise ValueError("Bitte einen Namen für die Gruppe angeben (höchstens 64 Zeichen).")
        self.write("POST", "/cloud/groups", {"groupid": group})
        self.forget()


def quote(user_id):
    return urllib.parse.quote(str(user_id), safe="")


def number(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def kib(value):
    n = number(value)
    return n * 1024 if n is not None else None


def quota_limit(value):
    """Bytes of the limit, or None for no limit / default."""
    n = number(value)
    return n if n is not None and n >= 0 else None


def quota_label(value):
    if value in (None, "", "default"):
        return "Standard"
    n = number(value)
    if value == "none" or (n is not None and n < 0):
        return "Unbegrenzt"
    if n is None:
        return str(value)
    for unit, size in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + " " + unit
    return f"{int(n)} B"
