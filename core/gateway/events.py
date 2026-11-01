"""Messages from the modules: collected in one list, every account reads only what it may and wants to see.

A module drops a JSON file into its own folder below the spool; the folder name says who sent it, so one module
cannot speak for another. Files survive a restart of the gateway and need neither a network nor a password.
"""

import json
import os
import sys
import threading
import time

KEEP = 500
MAX_FILE = 16 * 1024
GATEWAY_MODULE = "home-net"
GATEWAY_EVENTS = [
    {"id": "login_locked", "label": "Anmeldung wegen Fehlversuchen gesperrt", "manager_only": True},
    {"id": "passkey_added", "label": "Neuer Passkey eingerichtet", "manager_only": True},
    {"id": "device_paired", "label": "Neues Gerät gekoppelt", "manager_only": True},
]


def category_allowed(category, scopes):
    if "*" in scopes:
        return True
    return any(category == s or category.startswith(s + "/") for s in scopes)


def catalog_of(modules):
    """{module id: (name, {event id: event})} of the installed modules and the gateway itself."""
    catalog = {GATEWAY_MODULE: ("Sicherheit", {e["id"]: e for e in GATEWAY_EVENTS})}
    for module in modules:
        if module.get("events"):
            catalog[module["id"]] = (module["name"], {e["id"]: e for e in module["events"]})
    return catalog


def is_enabled(user, module_id, event):
    return user.get("notify", {}).get(f"{module_id}.{event['id']}", event.get("default", True))


def may_read(user, entry, catalog, categories_of, may_use):
    """Rights are checked when reading, so taking a right away also covers messages not yet delivered."""
    known = catalog.get(entry["module"])
    event = known[1].get(entry["type"]) if known else None
    if not event or not is_enabled(user, entry["module"], event):
        return False
    manager = user["role"] in ("owner", "admin")
    if event.get("manager_only"):
        return manager
    if entry["module"] != GATEWAY_MODULE and not may_use(user, entry["module"]):
        return False
    return not entry.get("category") or category_allowed(entry["category"], categories_of(user))


class Inbox:
    instances = {}
    instances_lock = threading.Lock()

    @classmethod
    def shared(cls, config_dir, spool):
        with cls.instances_lock:
            if config_dir not in cls.instances:
                cls.instances[config_dir] = cls(config_dir, spool)
            return cls.instances[config_dir]

    def __init__(self, config_dir, spool):
        self.path = os.path.join(config_dir, "notifications.jsonl")
        self.spool = spool
        self.changed = threading.Condition()
        self.entries = []
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                self.entries = [json.loads(line) for line in f if line.strip()][-KEEP:]
        except (OSError, ValueError):
            self.entries = []
        self.rewrite()

    def rewrite(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(json.dumps(e) + "\n" for e in self.entries)
        os.replace(tmp, self.path)

    def add(self, module, kind, title, message="", priority="default", category="", link=""):
        with self.changed:
            entry = {"id": (self.entries[-1]["id"] + 1) if self.entries else 1, "time": int(time.time()),
                     "module": module, "type": kind, "title": str(title)[:200], "message": str(message)[:1000],
                     "priority": "high" if priority == "high" else "default", "category": str(category)[:200],
                     "link": str(link)[:200]}
            self.entries.append(entry)
            if len(self.entries) > KEEP * 2:
                self.entries = self.entries[-KEEP:]
                self.rewrite()
            else:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            self.changed.notify_all()
        return entry

    def scan(self, catalog):
        """Takes over what the modules left in the spool; only known messages of the sending module count."""
        try:
            folders = sorted(d.name for d in os.scandir(self.spool) if d.is_dir())
        except OSError:
            return
        for module in folders:
            folder = os.path.join(self.spool, module)
            for name in sorted(os.listdir(folder)):
                if name.endswith(".json"):
                    self.take_file(module, os.path.join(folder, name), catalog)

    def take_file(self, module, path, catalog):
        try:
            with open(path, "rb") as f:
                data = json.loads(f.read(MAX_FILE))
            if module == GATEWAY_MODULE or data["type"] not in catalog[module][1]:
                raise KeyError(data["type"])
            self.add(module, data["type"], data["title"], data.get("message", ""), data.get("priority", "default"),
                     data.get("category", ""), data.get("link", ""))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            sys.stderr.write(f"Meldung von {module} verworfen: {exc!r}\n")
        try:
            os.remove(path)
        except OSError:
            pass

    def latest_id(self):
        with self.changed:
            return self.entries[-1]["id"] if self.entries else 0

    def since(self, last_id):
        with self.changed:
            return [e for e in self.entries if e["id"] > last_id]

    def recent(self, count=500):
        with self.changed:
            return list(self.entries[-count:])

    def wait(self, last_id, timeout):
        """Returns once something newer exists, someone calls wake, or the time is up."""
        with self.changed:
            if not self.entries or self.entries[-1]["id"] <= last_id:
                self.changed.wait(timeout)

    def wake(self):
        with self.changed:
            self.changed.notify_all()
