"""PCs, Aufträge und Läufe der PC-Sicherung: alles, was nicht vom Netz oder von restic abhängt."""
import copy
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

SCHEDULES = ("off", "daily", "weekly", "monthly")
AFTER = ("poweroff", "reboot")
WHEN = ("next", "window")
OVERDUE_DAYS = {"daily": 2, "weekly": 9, "monthly": 35}
ARM_SECONDS = {"auto": 20 * 60}
SILENT_SECONDS = 30 * 60
WAITING_SECONDS = 2 * 3600
MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
FORBIDDEN = re.compile(r"[/\\:*?\"<>|\x00-\x1f]")
HISTORY = 30


def normal_mac(text):
    mac = str(text).strip().lower().replace("-", ":")
    if re.fullmatch(r"[0-9a-f]{12}", mac):
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    return mac


def category_allowed(category, scopes):
    """Rights include subcategories, as everywhere in Home-Net."""
    if "*" in scopes:
        return True
    return any(category == s or category.startswith(s + "/") for s in scopes)


def new_run_id(now=None):
    return datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d_%H%M%S")


def run_label(run_id):
    m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)_(\d\d)(\d\d)\d*", run_id or "")
    return f"{m[3]}.{m[2]}.{m[1]}, {m[4]}:{m[5]} Uhr" if m else run_id


def due_key(pc, now):
    """The slot a schedule is due in at this moment, or None; one slot is run at most once."""
    when = datetime.fromtimestamp(now)
    if pc["schedule"] == "off" or when.hour < pc["hour"]:
        return None
    if pc["schedule"] == "daily":
        return when.strftime("%Y-%m-%d")
    if pc["schedule"] == "weekly":
        if when.weekday() != pc.get("weekday", 6):
            return None
        return when.strftime("%Y-W%W")
    if when.day != 1:
        return None
    return when.strftime("%Y-%m")


def in_window(pc, now):
    """Whether tasks for the PC may run at this moment: always without a maintenance window."""
    if not pc.get("window"):
        return True
    hour, start, end = datetime.fromtimestamp(now).hour, pc["window_from"], pc["window_to"]
    return start <= hour < end if start < end else hour >= start or hour < end


def window_key(pc, now):
    """The day the maintenance window that `now` lies in began, or None outside a window."""
    if not pc.get("window") or not in_window(pc, now):
        return None
    when = datetime.fromtimestamp(now)
    if pc["window_from"] > pc["window_to"] and when.hour < pc["window_to"]:
        when -= timedelta(days=1)
    return when.strftime("%Y-%m-%d")


def window_label(pc):
    return f"{pc['window_from']}–{pc['window_to']} Uhr" if pc.get("window") else ""


def next_due(pc, now):
    """Start of the next scheduled run, for display."""
    if pc["schedule"] == "off":
        return 0
    when = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    for hours in range(0, 24 * 40):
        slot = when + timedelta(hours=hours)
        if slot.hour != pc["hour"] or slot.timestamp() < now - 3600:
            continue
        if pc["schedule"] == "weekly" and slot.weekday() != pc.get("weekday", 6):
            continue
        if pc["schedule"] == "monthly" and slot.day != 1:
            continue
        return int(slot.timestamp())
    return 0


def pc_from_form(form, base=None):
    """A validated PC from form values; raises ValueError with a message for the user."""
    pc = copy.deepcopy(base) if base else {
        "id": secrets.token_hex(6), "name": "", "category": "", "macs": [], "schedule": "off", "hour": 3,
        "weekday": 6, "keep": 3, "after": "poweroff", "wake": True, "window": False, "window_from": 23, "window_to": 6, "skip_disks": [], "notes": "",
        "repo_password": secrets.token_urlsafe(24), "rest_password": secrets.token_urlsafe(24),
        "created": int(time.time()),
    }
    for key in ("name", "category", "notes"):
        if key in form:
            pc[key] = str(form[key]).strip() if key != "notes" else str(form[key])
    for key in ("hour", "weekday", "keep", "window_from", "window_to"):
        if key in form:
            try:
                pc[key] = int(form[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} muss eine Zahl sein")
    for key in ("schedule", "after"):
        if key in form:
            pc[key] = str(form[key])
    if "wake" in form:
        pc["wake"] = bool(form["wake"])
    if "window" in form:
        pc["window"] = bool(form["window"])
    pc.setdefault("window", False)
    pc.setdefault("window_from", 23)
    pc.setdefault("window_to", 6)
    if "macs" in form:
        raw = form["macs"] if isinstance(form["macs"], list) else re.split(r"[\s,;]+", str(form["macs"]))
        pc["macs"] = [normal_mac(m) for m in raw if str(m).strip()]
    if "skip_disks" in form:
        raw = form["skip_disks"] if isinstance(form["skip_disks"], list) else re.split(r"[\s,;]+", str(form["skip_disks"]))
        pc["skip_disks"] = [str(s).strip() for s in raw if str(s).strip()]
    if not pc["name"] or FORBIDDEN.search(pc["name"]) or len(pc["name"]) > 60:
        raise ValueError("Der Name darf nicht leer sein und keine Sonderzeichen wie / oder : enthalten.")
    if any(not part or FORBIDDEN.search(part) for part in pc["category"].split("/")) and pc["category"]:
        raise ValueError("Die Kategorie enthält ungültige Zeichen.")
    if not pc["macs"]:
        raise ValueError("Bitte die MAC-Adresse der Netzwerkkarte angeben, z. B. 3c:7c:3f:12:34:56.")
    bad = [m for m in pc["macs"] if not MAC.match(m)]
    if bad:
        raise ValueError(f"Ungültige MAC-Adresse: {bad[0]}")
    if pc["schedule"] not in SCHEDULES or pc["after"] not in AFTER:
        raise ValueError("Ungültige Auswahl beim Zeitplan.")
    if not 0 <= pc["hour"] <= 23 or not 0 <= pc["weekday"] <= 6:
        raise ValueError("Die Uhrzeit muss zwischen 0 und 23 liegen.")
    if not 0 <= pc["window_from"] <= 23 or not 0 <= pc["window_to"] <= 23:
        raise ValueError("Das Wartungsfenster braucht Stunden zwischen 0 und 23.")
    if pc["window"] and pc["window_from"] == pc["window_to"]:
        raise ValueError("Beginn und Ende des Wartungsfensters dürfen nicht gleich sein.")
    if not 1 <= pc["keep"] <= 100:
        raise ValueError("Behalten: zwischen 1 und 100 Sicherungen.")
    return pc


class Store:
    """pcs.json holds what the user sets, state.json what happens; both survive a restart."""

    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.RLock()
        self.pcs_path = os.path.join(folder, "pcs.json")
        self.state_path = os.path.join(folder, "state.json")
        self.pcs = self.read(self.pcs_path, {"pcs": [], "settings": {}})
        self.state = self.read(self.state_path, {})
        for key, empty in (("jobs", {}), ("history", {}), ("waiting", {}), ("seen", {}), ("scheduled", {}),
                           ("sent", {})):
            self.state.setdefault(key, empty)
        settings = self.pcs.setdefault("settings", {})
        settings.setdefault("netboot", False)
        settings.setdefault("server_ip", "")
        settings.setdefault("compression", "max")

    @staticmethod
    def read(path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def write(self, path, data):
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)

    def save(self):
        with self.lock:
            self.write(self.pcs_path, self.pcs)
            self.write(self.state_path, self.state)

    # --- PCs ---

    @property
    def settings(self):
        return self.pcs["settings"]

    def all_pcs(self, scopes=("*",)):
        with self.lock:
            return [copy.deepcopy(p) for p in self.pcs["pcs"] if category_allowed(p["category"], scopes)]

    def find(self, pc_id, scopes=("*",)):
        with self.lock:
            return next((copy.deepcopy(p) for p in self.pcs["pcs"]
                         if p["id"] == pc_id and category_allowed(p["category"], scopes)), None)

    def by_mac(self, mac):
        mac = normal_mac(mac)
        with self.lock:
            return next((copy.deepcopy(p) for p in self.pcs["pcs"] if mac in p["macs"]), None)

    def check_free(self, pc):
        for other in self.pcs["pcs"]:
            if other["id"] == pc["id"]:
                continue
            if other["name"].casefold() == pc["name"].casefold():
                raise ValueError("Es gibt schon einen PC mit diesem Namen.")
            same = set(other["macs"]) & set(pc["macs"])
            if same:
                raise ValueError(f"Die MAC-Adresse {same.pop()} gehört schon zu {other['name']}.")

    def create(self, form, scopes=("*",)):
        pc = pc_from_form(form)
        if not category_allowed(pc["category"], scopes):
            raise ValueError("Für diese Kategorie fehlt die Berechtigung.")
        with self.lock:
            self.check_free(pc)
            self.pcs["pcs"].append(pc)
            self.save()
        return pc

    def update(self, pc_id, form, scopes=("*",)):
        with self.lock:
            old = self.find(pc_id, scopes)
            if not old:
                raise LookupError
            pc = pc_from_form(form, old)
            if not category_allowed(pc["category"], scopes):
                raise ValueError("Für diese Kategorie fehlt die Berechtigung.")
            self.check_free(pc)
            self.pcs["pcs"] = [pc if p["id"] == pc_id else p for p in self.pcs["pcs"]]
            self.save()
        return pc

    def delete(self, pc_id):
        with self.lock:
            self.pcs["pcs"] = [p for p in self.pcs["pcs"] if p["id"] != pc_id]
            self.state["jobs"].pop(pc_id, None)
            self.state["history"].pop(pc_id, None)
            self.save()

    def set_settings(self, form):
        with self.lock:
            s = self.settings
            if "netboot" in form:
                s["netboot"] = bool(form["netboot"])
            if "server_ip" in form:
                ip = str(form["server_ip"]).strip()
                if ip and not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", ip):
                    raise ValueError("Bitte eine IPv4-Adresse angeben, z. B. 192.168.178.20.")
                s["server_ip"] = ip
            if "compression" in form:
                if form["compression"] not in ("auto", "max"):
                    raise ValueError("Ungültige Kompression")
                s["compression"] = form["compression"]
            self.save()
            return dict(s)

    # --- Aufträge ---

    def job(self, pc_id):
        with self.lock:
            job = self.state["jobs"].get(pc_id)
            return copy.deepcopy(job) if job else None

    def job_by_token(self, token):
        if not token:
            return None, None
        with self.lock:
            for pc_id, job in self.state["jobs"].items():
                if secrets.compare_digest(job["token"], token):
                    return pc_id, copy.deepcopy(job)
        return None, None

    def arm(self, pc_id, action, origin, after, run=None, parts=None, token=None, who="", when="next"):
        """A task for a PC; it waits until the PC starts over the network or from the stick.

        A task given by hand waits for the next start (when "next") or the next start inside the PC's maintenance
        window ("window") for as long as it takes. A scheduled one gives up after a while: without a window when the
        PC has not come 20 minutes after waking, with one when the window is over."""
        with self.lock:
            if pc_id in self.state["jobs"]:
                raise ValueError("Für diesen PC läuft schon ein Auftrag.")
            now = time.time()
            job = {"action": action, "origin": origin, "after": after, "token": token or secrets.token_urlsafe(24),
                   "run": run or new_run_id(now), "parts": parts or [], "state": "armed", "created": now,
                   "expires": now + ARM_SECONDS["auto"] if origin == "auto" and when == "next" else None,
                   "seen": now, "progress": None, "who": who, "when": when}
            self.state["jobs"][pc_id] = job
            self.save()
            return copy.deepcopy(job)

    def touch(self, pc_id, **changes):
        with self.lock:
            job = self.state["jobs"].get(pc_id)
            if not job:
                return None
            job.update(changes, seen=time.time())
            self.save()
            return copy.deepcopy(job)

    def finish(self, pc_id, result, message, stats=None, log=""):
        """Ends the task and records it; returns the finished task or None."""
        with self.lock:
            job = self.state["jobs"].pop(pc_id, None)
            if not job:
                return None
            entry = {"action": job["action"], "run": job["run"], "result": result, "message": message,
                     "start": int(job.get("started") or job["created"]), "end": int(time.time()), "origin": job["origin"],
                     "added": sum(int(v.get("added") or 0) for v in (stats or {}).values()),
                     "processed": sum(int(v.get("size") or 0) for v in (stats or {}).values()),
                     "log": log[-4000:], "who": job.get("who", "")}
            items = self.state["history"].setdefault(pc_id, [])
            items.insert(0, entry)
            del items[HISTORY:]
            self.save()
            job["entry"] = entry
            return job

    def history(self, pc_id):
        with self.lock:
            return copy.deepcopy(self.state["history"].get(pc_id, []))

    def last(self, pc_id, action="backup", result=None):
        for entry in self.history(pc_id):
            if entry["action"] == action and (result is None or entry["result"] == result):
                return entry
        return None

    def expired(self, now=None):
        """Tasks whose PC never came or fell silent. A waiting task whose PC got the boot system but never reported
        (another boot entry picked, stick pulled) waits again instead."""
        now = now or time.time()
        out = []
        with self.lock:
            for pc_id, job in self.state["jobs"].items():
                if job["state"] == "armed" and job.get("expires") and now > job["expires"]:
                    out.append((pc_id, "missed"))
                elif job["state"] == "booting" and not job.get("expires") and now - job["seen"] > SILENT_SECONDS:
                    job.update(state="armed", seen=now)
                    self.save()
                elif job["state"] in ("booting", "running") and now - job["seen"] > SILENT_SECONDS:
                    out.append((pc_id, "silent"))
        return out

    # --- PCs, die vom Stick oder unbekannt übers Netz starten ---

    def hello(self, info):
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.drop_old_waiting()
            mac = normal_mac(info.get("mac", ""))
            # One entry per machine: a restarted stick replaces its older entry
            self.state["waiting"] = {t: w for t, w in self.state["waiting"].items() if w["mac"] != mac}
            known = self.by_mac(mac)
            self.state["waiting"][token] = {
                "mac": mac, "vendor": str(info.get("vendor", ""))[:80], "product": str(info.get("product", ""))[:80],
                "disks": str(info.get("disks", ""))[:300], "since": time.time(), "seen": time.time(),
                "pc": known["id"] if known else "", "dismissed": False}
            self.save()
        return token

    def drop_old_waiting(self):
        now = time.time()
        self.state["waiting"] = {t: w for t, w in self.state["waiting"].items() if now - w["seen"] < WAITING_SECONDS}

    def waiting(self, token):
        with self.lock:
            w = self.state["waiting"].get(token)
            if w:
                w["seen"] = time.time()
            return copy.deepcopy(w) if w else None

    def waiting_list(self, scopes=("*",)):
        with self.lock:
            self.drop_old_waiting()
            out = []
            for token, w in self.state["waiting"].items():
                if w["dismissed"]:
                    continue
                pc = self.find(w["pc"]) if w["pc"] else None
                if pc and not category_allowed(pc["category"], scopes):
                    continue
                if not pc and "*" not in scopes:
                    continue
                out.append(dict(w, id=token[:12], pc_name=pc["name"] if pc else ""))
            return out

    def waiting_token(self, short):
        with self.lock:
            return next((t for t in self.state["waiting"] if t[:12] == short), None)

    def dismiss(self, short):
        with self.lock:
            token = self.waiting_token(short)
            if token:
                self.state["waiting"][token]["dismissed"] = True
                self.save()
            return token

    def take_waiting(self, token):
        with self.lock:
            w = self.state["waiting"].pop(token, None)
            self.save()
            return w

    def saw_mac(self, mac):
        """A machine without a PC entry asked to start over the network; offered in the list for adding."""
        with self.lock:
            self.state["seen"][normal_mac(mac)] = time.time()
            cutoff = time.time() - 7 * 86400
            self.state["seen"] = {m: t for m, t in self.state["seen"].items() if t > cutoff}
            self.save()

    def seen_unknown(self):
        with self.lock:
            known = {m for p in self.pcs["pcs"] for m in p["macs"]}
            return [{"mac": m, "time": int(t)} for m, t in sorted(self.state["seen"].items(), key=lambda x: -x[1])
                    if m not in known]

    # --- Zeitplan und Meldungen ---

    def take_due(self, now=None):
        """PCs whose scheduled slot has come; each slot is handed out once."""
        now = now or time.time()
        out = []
        with self.lock:
            for pc in self.pcs["pcs"]:
                key = due_key(pc, now)
                if key and self.state["scheduled"].get(pc["id"]) != key:
                    self.state["scheduled"][pc["id"]] = key
                    out.append(copy.deepcopy(pc))
            if out:
                self.save()
        return out

    def once_a_day(self, key, now=None):
        """True at most once every 24 hours for the same key; keeps repeated warnings quiet."""
        now = now or time.time()
        with self.lock:
            if now - self.state["sent"].get(key, 0) < 86400:
                return False
            self.state["sent"][key] = now
            self.save()
            return True

    def clear_sent(self, key):
        with self.lock:
            if self.state["sent"].pop(key, None):
                self.save()
