"""Talks to the Unraid server the module runs on.

Three ways in, all mounted into the container by module.json:
  - the state files of emhttpd in /var/local/emhttp (read only): shares, users, disks, settings
  - the emhttpd socket: the same interface the Unraid web interface uses when you press "Apply"; it changes shares,
    users and settings. It is not documented by Lime Technology, so writes are only allowed on tested versions.
  - the socket of the official Unraid API (GraphQL), for Docker containers and VMs; it needs an API key.
"""
import base64
import http.client
import json
import os
import re
import socket
import time
import urllib.parse

EMHTTP = os.environ.get("EMHTTP_DIR", "/host/emhttp")
EMHTTPD_SOCKET = os.environ.get("EMHTTPD_SOCKET", "/host/emhttpd.socket")
API_SOCKET = os.environ.get("UNRAID_API_SOCKET", "/host/unraid-api.sock")
SHARES_ROOT = os.environ.get("SHARES_ROOT", "/mnt/user")
DYNAMIX_CFG = os.environ.get("DYNAMIX_CFG", "/host/dynamix/dynamix.cfg")
TEMP_DEFAULTS = {"hot": 45, "max": 55, "hotssd": 60, "maxssd": 70}

# Versions on which the write commands were checked against the web interface's forms. Others stay read only unless
# the owner allows them in the settings.
TESTED = ("7.2.", "7.3.")

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,39}\$?$")
SHARE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,39}$")


class HostError(RuntimeError):
    stale = False  # a socket that was bound into the container before its service restarted


# --- state files ---------------------------------------------------------------------------------

def parse_ini(text):
    """The files emhttpd writes: sections ["name"] or [name] and lines key="value"."""
    out, section, top = {}, None, {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().strip('"')
            section = out.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        (section if section is not None else top)[key.strip()] = value
    return out if out else top


def read_ini(name, root=None):
    path = os.path.join(root or EMHTTP, name)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_ini(f.read())
    except FileNotFoundError:
        return {}


def to_int(value, default=0):
    try:
        return int(str(value).strip() or default)
    except ValueError:
        return default


# --- emhttpd -------------------------------------------------------------------------------------

class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=30):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            error = HostError(f"Unraid nicht erreichbar ({os.path.basename(self.path)}): {exc.strerror or exc}")
            error.stale = isinstance(exc, ConnectionRefusedError)
            raise error from exc
        self.sock = sock


class Emhttp:
    """Sends the forms of the Unraid web interface to emhttpd, with the CSRF token from var.ini."""

    def __init__(self, socket_path=None, root=None):
        self.socket_path = socket_path or EMHTTPD_SOCKET
        self.root = root

    def send(self, fields):
        token = read_ini("var.ini", self.root).get("csrf_token", "")
        if not token:
            raise HostError("Kein Zugriffsschlüssel von Unraid gefunden (var.ini)")
        body = urllib.parse.urlencode(list(fields.items()) + [("csrf_token", token)])
        conn = UnixHTTPConnection(self.socket_path, timeout=120)
        try:
            conn.request("POST", "/update", body=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded", "Host": "localhost"})
            reply = conn.getresponse()
            text = reply.read().decode("utf-8", "replace").strip()
        except (OSError, http.client.HTTPException) as exc:
            raise HostError(f"Unraid hat nicht geantwortet: {exc}") from exc
        finally:
            conn.close()
        if reply.status >= 400:
            raise HostError(f"Unraid meldet Fehler {reply.status}: {strip_tags(text)[:300]}")
        if text:
            # emhttpd answers nothing on success; anything else is its error message
            raise HostError(f"Unraid meldet: {strip_tags(text)[:300]}")


def strip_tags(text):
    return re.sub(r"<[^>]+>", " ", text).strip()


# --- official API --------------------------------------------------------------------------------

class UnraidApi:
    def __init__(self, key_getter, socket_path=None):
        self.key_getter = key_getter
        self.socket_path = socket_path or API_SOCKET

    def configured(self):
        return bool(self.key_getter())

    def query(self, text, variables=None):
        key = self.key_getter()
        if not key:
            raise HostError("Für Docker und VMs fehlt noch der API-Schlüssel (Einstellungen).")
        body = json.dumps({"query": text, "variables": variables or {}})
        conn = UnixHTTPConnection(self.socket_path, timeout=60)
        try:
            conn.request("POST", "/graphql", body=body,
                         headers={"Content-Type": "application/json", "x-api-key": key, "Host": "localhost"})
            reply = conn.getresponse()
            raw = reply.read()
        except (OSError, http.client.HTTPException) as exc:
            raise HostError(f"Unraid-API antwortet nicht: {exc}") from exc
        finally:
            conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            raise HostError(f"Unraid-API antwortet unverständlich (Fehler {reply.status})") from None
        if data.get("errors"):
            message = data["errors"][0].get("message", "Fehler")
            if reply.status in (401, 403) or "unauthor" in message.lower() or "forbidden" in message.lower():
                raise HostError("Die Unraid-API lehnt den API-Schlüssel ab. Bitte in den Einstellungen prüfen.")
            raise HostError(f"Unraid-API: {message}")
        return data.get("data") or {}


# --- the server ----------------------------------------------------------------------------------

def disk_kind(name, entry):
    kind = entry.get("type", "")
    if kind in ("Parity", "Data", "Cache", "Flash"):
        return {"Parity": "parity", "Data": "data", "Cache": "pool", "Flash": "flash"}[kind]
    if name.startswith("parity"):
        return "parity"
    if name.startswith("disk"):
        return "data"
    return "pool"


DISK_STATES = {
    "DISK_OK": "In Ordnung", "DISK_NP": "Nicht belegt", "DISK_NP_DSBL": "Leer, deaktiviert",
    "DISK_DSBL": "Deaktiviert", "DISK_DSBL_NEW": "Neu, deaktiviert", "DISK_INVALID": "Ungültig",
    "DISK_WRONG": "Falsche Platte", "DISK_NEW": "Neu", "DISK_NP_MISSING": "Fehlt",
}


class Server:
    def __init__(self, settings_getter, root=None, emhttp=None, api=None, shares_root=None):
        self.root = root
        self.settings = settings_getter
        self.emhttp = emhttp or Emhttp(root=root)
        self.api = api or UnraidApi(lambda: self.settings().get("api_key", ""))
        self.shares_root = shares_root or SHARES_ROOT

    # -- reading --
    def var(self):
        return read_ini("var.ini", self.root)

    def available(self):
        return bool(self.var().get("version"))

    def version(self):
        return self.var().get("version", "")

    def writable(self):
        version = self.version()
        return bool(version) and (version.startswith(TESTED) or bool(self.settings().get("allow_untested")))

    def need_writable(self):
        if not self.available():
            raise HostError("Die Daten von Unraid sind im Container nicht sichtbar. Läuft Home-Net auf dem Unraid-Server?")
        if not self.writable():
            raise PermissionError(f"Unraid {self.version()} ist für Änderungen noch nicht geprüft. Der Eigentümer kann "
                                  f"sie in den Einstellungen trotzdem erlauben.")

    def users(self):
        out = []
        for name, entry in read_ini("users.ini", self.root).items():
            out.append({"name": entry.get("name", name), "idx": to_int(entry.get("idx")), "desc": entry.get("desc", ""),
                        "password": entry.get("passwd", "") == "yes"})
        return sorted(out, key=lambda u: u["idx"])

    def user(self, name):
        user = next((u for u in self.users() if u["name"] == name), None)
        if not user:
            raise LookupError
        return user

    def pools(self):
        return sorted({n for n, e in read_ini("disks.ini", self.root).items()
                       if disk_kind(n, e) == "pool" and e.get("fsStatus", "") in ("Mounted", "")
                       and e.get("status", "DISK_OK") != "DISK_NP" and "~" not in n})

    def shares(self):
        raw = read_ini("shares.ini", self.root)
        sec = read_ini("sec.ini", self.root)
        nfs = read_ini("sec_nfs.ini", self.root)
        out = []
        for name, entry in raw.items():
            out.append(share_view(entry, sec.get(name, {}), nfs.get(name, {})))
        return sorted(out, key=lambda s: s["name"].lower())

    def share(self, name):
        share = next((s for s in self.shares() if s["name"] == name), None)
        if not share:
            raise LookupError
        return share

    def disks(self):
        out = []
        for name, entry in read_ini("disks.ini", self.root).items():
            status = entry.get("status", "")
            if status.startswith("DISK_NP") and not entry.get("device"):
                continue  # empty slot
            size = to_int(entry.get("fsSize")) * 1024
            free = to_int(entry.get("fsFree")) * 1024
            out.append({"name": name, "kind": disk_kind(name, entry), "device": entry.get("device", ""),
                        "model": entry.get("id", ""), "status": status,
                        "status_text": DISK_STATES.get(status, status or "–"), "ok": status in ("DISK_OK", ""),
                        "temp": None if entry.get("temp", "*") in ("*", "") else to_int(entry.get("temp")),
                        "spundown": entry.get("spundown") == "1", "ssd": entry.get("rotational") == "0", "errors": to_int(entry.get("numErrors")),
                        "fs": entry.get("fsType", ""), "mounted": entry.get("fsStatus", "") == "Mounted",
                        "size": size or to_int(entry.get("size")) * 1024, "free": free,
                        "warning": entry.get("color", "").startswith(("red", "yellow"))})
        return out

    def array(self):
        v = self.var()
        size = to_int(v.get("mdResyncSize"))
        pos = to_int(v.get("mdResyncPos"))
        running = to_int(v.get("mdResync")) > 0
        return {"state": v.get("mdState", ""), "started": v.get("mdState") == "STARTED",
                "disabled": to_int(v.get("mdNumDisabled")), "invalid": to_int(v.get("mdNumInvalid")),
                "missing": to_int(v.get("mdNumMissing")),
                "check": {"running": running, "paused": running and to_int(v.get("mdResyncDt")) == 0 and pos > 0,
                          "action": v.get("mdResyncAction", ""),
                          "percent": round(pos * 100 / size, 1) if running and size else None,
                          "last": to_int(v.get("sbSynced2")) or to_int(v.get("sbSynced")),
                          "last_errors": to_int(v.get("sbSyncErrs")),
                          "last_exit": to_int(v.get("sbSyncExit"))},
                "mover": v.get("shareMoverActive") == "yes"}

    def overview(self):
        v = self.var()
        mem = meminfo()
        return {"name": v.get("NAME", ""), "comment": v.get("COMMENT", ""), "version": v.get("version", ""),
                "available": bool(v.get("version")), "writable": self.writable(), "tested": TESTED,
                "uptime": uptime(), "load": loadavg(), "cpus": os.cpu_count() or 0, "memory": mem,
                "array": self.array(), "disks": self.disks(),
                "share_count": len(read_ini("shares.ini", self.root)), "user_count": len(self.users()),
                "api": self.api.configured()}

    def general(self):
        v = self.var()
        keys = ("NAME", "COMMENT", "SYS_MODEL", "WORKGROUP", "shareSMBEnabled", "shareNFSEnabled", "enableFruit",
                "localMaster", "hideDotFiles", "timeZone", "USE_NTP", "NTP_SERVER1", "NTP_SERVER2", "NTP_SERVER3",
                "NTP_SERVER4", "spindownDelay", "spinupGroups", "shareMoverSchedule", "USE_SSH", "PORTSSH")
        return {k: v.get(k, "") for k in keys}

    # -- writing --
    def _send(self, fields):
        self.need_writable()
        self.emhttp.send(fields)

    def add_user(self, name, desc, password):
        name = (name or "").strip()
        if not USER_RE.match(name):
            raise ValueError("Benutzernamen bitte nur aus Kleinbuchstaben, Ziffern, _ und - (am Anfang ein Buchstabe).")
        if name in {u["name"] for u in self.users()}:
            raise ValueError("Diesen Benutzer gibt es schon.")
        check_desc(desc)
        check_password(password)
        encoded = b64(password)
        self._send({"userName": name, "userDesc": desc or "", "userPassword": encoded, "userPasswordConf": encoded,
                    "cmdUserEdit": "Add"})

    def edit_user(self, name, desc):
        self.user(name)
        check_desc(desc)
        self._send({"userName": name, "userDesc": desc or "", "cmdUserEdit": "Apply"})

    def set_password(self, name, password):
        self.user(name)
        check_password(password)
        encoded = b64(password)
        self._send({"userName": name, "userPassword": encoded, "userPasswordConf": encoded, "cmdUserEdit": "Change"})

    def delete_user(self, name):
        if name == "root":
            raise ValueError("Den Benutzer root kann man nicht löschen.")
        user = self.user(name)
        self._send({"userName": name, "userDesc": user["desc"], "confirmDelete": "on", "cmdUserEdit": "Delete"})

    def share_fields(self, original, form):
        """The form of the web interface for a share; every field is sent, unchanged ones with their current value."""
        current = self.share(original) if original else None
        name = str(form.get("name", original or "")).strip()
        existing = {s["name"].lower() for s in self.shares()}
        if not original or name != original:
            if not SHARE_RE.match(name) or name.endswith("."):
                raise ValueError("Der Name muss mit einem Buchstaben beginnen und darf nur Buchstaben, Ziffern, . _ "
                                 "und - enthalten (höchstens 40 Zeichen).")
            if name.lower() in existing and name.lower() != (original or "").lower():
                raise ValueError("Diese Freigabe gibt es schon.")
            reserved = {r.strip().lower() for r in self.var().get("reservedNames", "").split(",") if r.strip()}
            if name.lower() in reserved or name.lower() in {p.lower() for p in self.pools()}:
                raise ValueError("Dieser Name ist bei Unraid reserviert.")
        raw = current["raw"] if current else {"allocator": "highwater", "splitLevel": "", "include": "", "exclude": "",
                                              "cow": "auto", "floor": ""}
        storage = storage_fields(form.get("storage") or (current or {}).get("storage") or {}, self.pools())
        comment = str(form.get("comment", (current or {}).get("comment", "")))
        if len(comment) > 256 or any(c in comment for c in '"\n\r'):
            raise ValueError("Die Beschreibung ist zu lang oder enthält ungültige Zeichen.")
        floor = form.get("floor", raw.get("floor", ""))
        floor = "" if floor in (None, "") else str(to_int(floor))
        fields = {"shareNameOrig": original or "", "shareName": name, "shareComment": comment,
                  "shareAllocator": raw.get("allocator", "highwater"), "shareSplitLevel": raw.get("splitLevel", ""),
                  "shareInclude": raw.get("include", ""), "shareExclude": raw.get("exclude", ""),
                  "shareFloor": floor, **storage}
        if not original:
            fields["shareCOW"] = raw.get("cow", "auto")  # only settable when adding, as in the web interface
        return name, fields

    def save_share(self, original, form):
        name, fields = self.share_fields(original, form)
        fields["cmdEditShare"] = "Apply" if original else "Add Share"
        self._send(fields)
        return name

    def delete_share(self, name):
        self.share(name)
        root = os.path.join(self.shares_root, name)
        try:
            if os.path.isdir(root) and os.listdir(root):
                raise ValueError("Die Freigabe ist nicht leer. Unraid löscht nur leere Freigaben.")
        except PermissionError:
            pass
        _, fields = self.share_fields(name, {})
        fields.update(confirmDelete="on", cmdEditShare="Delete")
        self._send(fields)

    def share_security(self, name, form):
        share = self.share(name)
        smb = share["smb"]
        export = form.get("export", smb["export"])
        if export not in ("-", "e", "eh", "et", "eth"):
            raise ValueError("Ungültige Auswahl für die Freigabe im Netz")
        security = form.get("security", smb["security"])
        if security not in ("public", "secure", "private"):
            raise ValueError("Ungültige Sicherheitsstufe")
        case = form.get("case", smb.get("case") or "auto")
        if case not in ("auto", "yes", "forced"):
            raise ValueError("Ungültige Auswahl für Groß- und Kleinschreibung")
        fields = {"shareName": name, "shareExport": export, "shareSecurity": security, "shareCaseSensitive": case}
        if self.var().get("enableFruit") == "yes":
            fields["shareVolsizelimit"] = str(to_int(form.get("volsizelimit", smb.get("volsizelimit")), 0) or "")
        fields["changeShareSecurity"] = "Apply"
        self._send(fields)

    def share_access(self, name, access, security=None):
        """access: {user: 'read-write'|'read-only'|'no-access'}; users not named keep their current right.
        security: the level just set, before Unraid has written it to sec.ini."""
        share = self.share(name)
        smb = dict(share["smb"], security=security or share["smb"]["security"])
        if smb["security"] == "public":
            raise ValueError("Bei einer öffentlichen Freigabe gibt es keine Rechte je Benutzer.")
        fields = {"shareName": name, "userAccess.0": "no-access"}
        for user in self.users():
            if user["name"] == "root":
                continue
            right = access.get(user["name"]) or smb["access"].get(user["name"], "no-access")
            if right not in ("read-write", "read-only", "no-access"):
                raise ValueError("Ungültiges Recht")
            if smb["security"] == "secure" and right == "no-access":
                right = "read-only"  # secure shares can always be read
            fields[f"userAccess.{user['idx']}"] = right
        fields["changeShareAccess"] = "Apply"
        self._send(fields)

    def save_general(self, section, form):
        v = self.var()

        def pick(key, allowed=None, limit=64):
            value = str(form.get(key, v.get(key, ""))).strip()
            if allowed is not None and value not in allowed:
                raise ValueError(f"Ungültiger Wert für {key}")
            if len(value) > limit or any(c in value for c in '"\n\r'):
                raise ValueError(f"Ungültiger Wert für {key}")
            return value

        if section == "identity":
            name = pick("NAME", limit=15)
            if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", name):
                raise ValueError("Der Servername darf nur Buchstaben, Ziffern, Punkt und Bindestrich enthalten.")
            fields = {"NAME": name, "COMMENT": pick("COMMENT"), "SYS_MODEL": pick("SYS_MODEL"), "changeNames": "Apply"}
        elif section == "smb":
            fields = {"shareSMBEnabled": pick("shareSMBEnabled", ("yes", "no", "ads")),
                      "hideDotFiles": pick("hideDotFiles", ("yes", "no")),
                      "enableFruit": pick("enableFruit", ("yes", "no")),
                      "serverMultiChannel": pick("serverMultiChannel", ("yes", "no", "")) or "no",
                      "USE_NETBIOS": pick("USE_NETBIOS", ("yes", "no", "")) or "no",
                      "USE_WSD": pick("USE_WSD", ("yes", "no", "")) or "no",
                      "WSD2_OPT": pick("WSD2_OPT", limit=200), "changeShare": "Apply"}
            if fields["shareSMBEnabled"] == "ads" and v.get("shareSMBEnabled") != "ads":
                raise ValueError("Active Directory bitte in der Unraid-Oberfläche einrichten.")
        elif section == "workgroup":
            group = pick("WORKGROUP", limit=15)
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", group):
                raise ValueError("Ungültige Arbeitsgruppe")
            fields = {"WORKGROUP": group, "localMaster": pick("localMaster", ("yes", "no")), "changeShare": "Apply"}
        elif section == "nfs":
            fields = {"shareNFSEnabled": pick("shareNFSEnabled", ("yes", "no")),
                      "fuse_remember": pick("fuse_remember", limit=8) or "330", "changeShare": "Apply"}
        elif section == "time":
            # The time zone and manual time also need PHP helpers of the web interface, so they stay there.
            if v.get("USE_NTP") != "yes":
                raise ValueError("Die Zeit wird ohne NTP gestellt. Das bitte in der Unraid-Oberfläche ändern.")
            fields = {"timeZone": v.get("timeZone", ""), "USE_NTP": "yes"}
            for n in range(1, 5):
                server = pick(f"NTP_SERVER{n}", limit=40)
                if server and not re.fullmatch(r"[A-Za-z0-9.:-]+", server):
                    raise ValueError("Ungültiger Zeitserver")
                fields[f"NTP_SERVER{n}"] = server
            if not any(fields[f"NTP_SERVER{n}"] for n in range(1, 5)):
                raise ValueError("Bitte mindestens einen Zeitserver angeben.")
            fields["setDateTime"] = "apply"
        elif section == "disks":
            delay = pick("spindownDelay", ("0", "15", "30", "45", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
            # the whole form of the web interface, the other values unchanged
            fields = {k: v.get(k, "") for k in DISK_FORM}
            fields.update(spindownDelay=delay, spinupGroups=pick("spinupGroups", ("yes", "no")), changeDisk="Apply")
        else:
            raise LookupError
        self._send(fields)

    def temps(self):
        display = read_cfg(DYNAMIX_CFG).get("display", {})
        return {k: to_int(display.get(k), d) or d for k, d in TEMP_DEFAULTS.items()} | \
            {"custom": {k: display.get(k, "") for k in TEMP_DEFAULTS}, "available": os.path.exists(DYNAMIX_CFG)}

    def set_temps(self, form):
        """Warning temperatures live in dynamix.cfg, which the web interface writes directly (Dispatcher.php)."""
        self.need_writable()
        if not os.path.exists(DYNAMIX_CFG):
            raise HostError("Die Einstellungsdatei von Unraid ist im Container nicht sichtbar.")
        values = {}
        for key in TEMP_DEFAULTS:
            value = form.get(key, None)
            if value in (None, ""):
                continue
            number = to_int(value, -1)
            if not 20 <= number <= 90:
                raise ValueError("Temperaturen bitte zwischen 20 und 90 °C angeben.")
            values[key] = number
        current = self.temps()
        merged = {k: values.get(k, current[k]) for k in TEMP_DEFAULTS}
        if merged["hot"] >= merged["max"] or merged["hotssd"] >= merged["maxssd"]:
            raise ValueError("Die Warnung muss unter der kritischen Temperatur liegen.")
        write_cfg(DYNAMIX_CFG, "display", {k: str(v) for k, v in values.items()})

    def array_command(self, command):
        a = self.array()
        if not a["started"]:
            raise ValueError("Das Array ist nicht gestartet.")
        if command == "check":
            if a["check"]["running"]:
                raise ValueError("Es läuft schon eine Prüfung.")
            self._send({"startState": "STARTED", "optionCorrect": "correct", "cmdCheck": "Check"})
        elif command == "check_nocorrect":
            if a["check"]["running"]:
                raise ValueError("Es läuft schon eine Prüfung.")
            self._send({"startState": "STARTED", "cmdCheck": "Check"})
        elif command in ("pause", "resume", "cancel"):
            if not a["check"]["running"]:
                raise ValueError("Es läuft keine Prüfung.")
            self._send({"startState": "STARTED",
                        {"pause": "cmdCheckPause", "resume": "cmdCheckResume", "cancel": "cmdCheckCancel"}[command]: ""})
        elif command == "mover":
            if a["mover"]:
                raise ValueError("Der Mover läuft schon.")
            self._send({"cmdStartMover": "Move"})
        else:
            raise LookupError

    # -- Docker and VMs through the official API --
    def containers(self):
        data = self.api.query("query { docker { containers(skipCache: true) { id names image state status autoStart } } }")
        out = []
        for c in (data.get("docker") or {}).get("containers") or []:
            name = ((c.get("names") or [""])[0] or "").lstrip("/")
            out.append({"id": c.get("id"), "name": name, "image": c.get("image", ""),
                        "state": str(c.get("state", "")).lower(), "status": c.get("status", ""),
                        "autostart": bool(c.get("autoStart")), "protected": is_protected(name)})
        return sorted(out, key=lambda c: c["name"].lower())

    def container_command(self, cid, command):
        if command not in ("start", "stop", "restart"):
            raise LookupError
        container = next((c for c in self.containers() if c["id"] == cid), None)
        if not container:
            raise LookupError
        if container["protected"] and command != "start":
            raise ValueError("Home-Net kann sich nicht selbst stoppen. Das geht nur in der Unraid-Oberfläche.")
        steps = ["stop", "start"] if command == "restart" else [command]
        for step in steps:
            self.api.query(f"mutation($id: PrefixedID!) {{ docker {{ {step}(id: $id) {{ id state }} }} }}", {"id": cid})
        return container["name"]

    def vms(self):
        data = self.api.query("query { vms { domains { id name state } } }")
        out = [{"id": d.get("id"), "name": d.get("name", ""), "state": str(d.get("state", "")).lower()}
               for d in (data.get("vms") or {}).get("domains") or []]
        return sorted(out, key=lambda d: d["name"].lower())

    def vm_command(self, vid, command):
        verbs = {"start": "start", "stop": "stop", "force": "forceStop", "pause": "pause", "resume": "resume",
                 "reboot": "reboot"}
        if command not in verbs:
            raise LookupError
        vm = next((d for d in self.vms() if d["id"] == vid), None)
        if not vm:
            raise LookupError
        self.api.query(f"mutation($id: PrefixedID!) {{ vm {{ {verbs[command]}(id: $id) }} }}", {"id": vid})
        return vm["name"]

    # -- files --
    def browse(self, rel):
        path = self.resolve(rel)
        if not os.path.isdir(path):
            raise LookupError
        entries = []
        try:
            names = os.listdir(path)
        except PermissionError as exc:
            raise PermissionError("Kein Zugriff auf diesen Ordner") from exc
        for name in names:
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append({"name": name, "dir": os.path.isdir(full), "size": st.st_size, "mtime": int(st.st_mtime)})
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {"path": os.path.relpath(path, self.shares_root).replace(".", "", 1) if path == self.shares_root
                else os.path.relpath(path, self.shares_root), "entries": entries[:5000], "more": len(entries) > 5000}

    def resolve(self, rel):
        rel = str(rel or "").strip("/")
        if "\x00" in rel:
            raise ValueError("Ungültiger Pfad")
        root = os.path.realpath(self.shares_root)
        path = os.path.realpath(os.path.join(root, rel))
        if path != root and not path.startswith(root + os.sep):
            raise PermissionError("Dieser Pfad liegt außerhalb der Freigaben")
        return path


DISK_FORM = ("startArray", "spindownDelay", "spinupGroups", "shutdownTimeout", "defaultFsType", "poll_attributes",
             "queueDepth", "nr_requests", "md_scheduler", "md_num_stripes", "md_queue_limit", "md_sync_limit",
             "md_write_method")


def read_cfg(path):
    """dynamix.cfg as {section: {key: value}}, in file order."""
    out, section = {}, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1], {})
        elif "=" in line and section is not None:
            key, value = line.split("=", 1)
            section[key.strip()] = value.strip().strip('"')
    return out


def write_cfg(path, section, values):
    """Like Dispatcher.php of the web interface: change keys of one section, keep everything else."""
    data = read_cfg(path)
    data.setdefault(section, {}).update(values)
    text = "".join(f"[{name}]\n" + "".join(f'{k}="{v}"\n' for k, v in block.items())
                   for name, block in data.items() if block)
    tmp = path + ".home-net"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


PROTECTED = ("home-net", "pc-sicherung", "pi-wartung", "networkmonitor", "unraid-verwaltung")


def is_protected(name):
    return name.startswith(PROTECTED)


def b64(text):
    return base64.b64encode(text.encode()).decode()


def check_desc(desc):
    if desc and (len(desc) > 64 or any(c in desc for c in '&:"')):
        raise ValueError('Die Beschreibung darf höchstens 64 Zeichen haben und kein &, : oder " enthalten.')


def check_password(password):
    if not password:
        raise ValueError("Bitte ein Passwort angeben.")
    if len(password) > 128:
        raise ValueError("Das Passwort darf höchstens 128 Zeichen haben.")


def share_view(entry, sec, nfs):
    name = entry.get("name", "")
    read = [u for u in sec.get("readList", "").split(",") if u]
    write = [u for u in sec.get("writeList", "").split(",") if u]
    access = {u: "read-only" for u in read}
    access.update({u: "read-write" for u in write})
    use = entry.get("useCache", "")
    primary = entry.get("cachePool", "") if use in ("yes", "only", "prefer") else ""
    secondary = entry.get("cachePool2", "") or ("array" if use in ("yes", "prefer") else "")
    if use == "prefer":
        mover = "to_primary"
    elif use == "yes":
        mover = "to_secondary"
    else:
        mover = ""
    return {"name": name, "comment": entry.get("comment", ""), "used": to_int(entry.get("used")) * 1024,
            "free": to_int(entry.get("free")) * 1024, "floor": entry.get("floor", ""),
            "color": entry.get("color", ""), "exclusive": entry.get("exclusive") == "yes",
            "storage": {"primary": primary or "array", "secondary": secondary, "mover": mover},
            "smb": {"export": sec.get("export", "-"), "security": sec.get("security", "public"),
                    "case": sec.get("caseSensitive", "auto"), "volsizelimit": sec.get("volsizelimit", ""),
                    "access": access},
            "nfs": {"export": nfs.get("export", "-"), "security": nfs.get("security", "public")},
            "raw": {k: entry.get(k, "") for k in ("allocator", "splitLevel", "include", "exclude", "cow", "floor",
                                                    "useCache", "cachePool", "cachePool2")}}


def storage_fields(storage, pools):
    """Primary and secondary storage as the web interface sends them (shareUseCache, shareCachePool, shareCachePool2)."""
    primary = str(storage.get("primary") or "array")
    secondary = str(storage.get("secondary") or "")
    mover = str(storage.get("mover") or "to_secondary")
    if primary != "array" and primary not in pools:
        raise ValueError("Diesen Pool gibt es nicht.")
    if primary == "array":
        if secondary:
            raise ValueError("Wenn das Array der erste Speicher ist, gibt es keinen zweiten.")
        return {"shareUseCache": "no", "shareCachePool": "", "shareCachePool2": ""}
    if not secondary:
        return {"shareUseCache": "only", "shareCachePool": primary, "shareCachePool2": ""}
    if secondary != "array" and (secondary not in pools or secondary == primary):
        raise ValueError("Ungültiger zweiter Speicher.")
    use = "prefer" if mover == "to_primary" else "yes"
    return {"shareUseCache": use, "shareCachePool": primary,
            "shareCachePool2": "" if secondary == "array" else secondary}


def meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError):
        return {"total": 0, "available": 0}
    return {"total": info.get("MemTotal", 0), "available": info.get("MemAvailable", 0)}


def loadavg():
    try:
        with open("/proc/loadavg") as f:
            return [float(x) for x in f.read().split()[:3]]
    except (OSError, ValueError):
        return [0, 0, 0]


def uptime():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return 0


def now():
    return int(time.time())
