"""Data model and parsing for the Pi-Wartung service."""
import re
import shlex
import uuid
from dataclasses import dataclass, field

DEFAULT_KEY = "/root/.ssh/pi-wartung"


@dataclass
class Pi:
    name: str = ""
    category: str = ""           # "Firma/Standort A"; empty means no category
    host: str = ""
    port: int = 22
    user: str = "pi"
    key: str = DEFAULT_KEY       # private key path on the Unraid server
    jump: str = ""               # user@host of an optional jump host
    jump_port: int = 22
    keep: int = 3
    format: str = "xz"           # xz, gz or img
    expand: bool = True
    excludes: str = ""
    bwlimit: int = 0             # KiB/s, 0 means unlimited
    compress: bool = False       # rsync -z, useful over the internet
    pre_cmd: str = ""
    post_cmd: str = ""
    schedule: str = "off"        # off, daily, weekly, monthly
    hour: int = 3
    notify: str = "errors"       # errors, always or off
    notes: str = ""
    # all: part of "update all", single: only on its own, careful: only on its own after typing the name.
    # Pis from before this setting count as single; the engine treats a pi.conf without it as careful.
    updates: str = "single"
    update_tile: bool = True     # counts in the tile "X Pis haben Updates"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def conf_text(self):
        values = {
            "NAME": self.name, "HOST": self.host, "PORT": self.port, "USER_NAME": self.user,
            "KEY": self.key, "JUMP": self.jump, "JUMP_PORT": self.jump_port, "KEEP": self.keep,
            "FORMAT": self.format, "EXPAND": int(self.expand), "EXCLUDES": self.excludes,
            "BWLIMIT": self.bwlimit, "COMPRESS": int(self.compress), "PRE_CMD": self.pre_cmd,
            "POST_CMD": self.post_cmd, "SCHEDULE": self.schedule, "HOUR": self.hour,
            "NOTIFY": self.notify, "UPDATES": self.updates,
        }
        lines = ["# Von Pi-Wartung erzeugt, Änderungen bitte in der App vornehmen."]
        lines += [f"{k}={shlex.quote(str(v))}" for k, v in values.items()]
        return "\n".join(lines) + "\n"

    def own_key_path(self):
        """Path of the key that belongs to this Pi alone, on the Unraid server."""
        return f"/root/.ssh/pi-wartung-{self.id[:8]}"

    def ssh_command(self):
        """ssh call for this Pi as run from the desktop, for terminal and key setup."""
        cmd = ["ssh", "-p", str(self.port)]
        if self.jump:
            cmd += ["-J", f"{self.jump}:{self.jump_port}"]
        return cmd + [f"{self.user}@{self.host}"]


def valid_segment(text):
    """A name usable as one folder name on the server."""
    return bool(text) and text not in (".", "..") and not text.startswith(".") \
        and not re.search(r'[/\\\x00-\x1f"\'`$*?<>|:]', text)


def valid_category(path):
    return path == "" or all(valid_segment(p) for p in path.split("/"))


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def parse_shell_kv(text):
    """key=value lines written with printf %q, as in the state output."""
    out = {}
    for k, v in parse_kv(text).items():
        try:
            parts = shlex.split(v)
            out[k] = parts[0] if parts else ""
        except ValueError:
            out[k] = v
    return out


def parse_summary(text):
    """Result of the engine's summary command, with Pis and their runs keyed by folder."""
    out = {"avail": 0, "total": 0, "pis": {}, "runs": []}
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "DISK" and len(f) >= 3:
            out["avail"], out["total"] = int(f[1]), int(f[2])
        elif f[0] == "PI" and len(f) >= 11:
            out["pis"][f[1]] = {"running": f[2] == "1", "status": f[3], "time": int(f[4] or 0), "ok_time": int(f[5] or 0),
                                "count": int(f[6]), "bytes": int(f[7]), "mirror": int(f[8]), "schedule": f[9],
                                "newest": int(f[10]), "warnings": f[11] if len(f) > 11 else ""}
            if len(f) > 15:
                out["pis"][f[1]].update(upd_count=int(f[12] or -1), upd_time=int(f[13] or 0), upd_state=f[14],
                                        upd_last=f[15])
        elif f[0] == "RUN" and len(f) >= 4:
            out["runs"].append({"dir": f[1], "stamp": f[2], "status": f[3], "error": f[4] if len(f) > 4 else ""})
    return out


def attention_items(inventory_pis, dirs, summary, devices, now, disk_warn_pct=85, temp_warn=75, stale_days=14):
    """What needs a look, most serious first. Returns (severity, pi name, text) with severity 0 or 1."""
    items = []
    for pi in inventory_pis:
        row = summary["pis"].get(dirs[pi.id], {})
        dev = devices.get(pi.id)
        status, ok_time = row.get("status", ""), row.get("ok_time", 0)
        if status == "failed":
            items.append((0, pi.name, "Letzte Sicherung ist fehlgeschlagen"))
        if dev is not None and not dev:
            items.append((0, pi.name, "Nicht erreichbar"))
        if not row.get("count"):
            items.append((1, pi.name, "Noch kein Image vorhanden"))
        elif ok_time and (now - ok_time) / 86400 >= stale_days:
            items.append((1, pi.name, f"Letztes erfolgreiches Backup vor {int((now - ok_time) / 86400)} Tagen"))
        if row.get("schedule", pi.schedule) == "off":
            items.append((1, pi.name, "Kein Zeitplan aktiv"))
        if row.get("warnings"):
            items.append((1, pi.name, row["warnings"]))
        if row.get("upd_last") == "failed" and row.get("upd_state") != "upgrading":
            items.append((1, pi.name, "Letztes Update ist fehlgeschlagen"))
        if dev:
            parts = dev.get("disk", "").split()
            pct = parts[2].rstrip("%") if len(parts) == 3 else ""
            if pct.isdigit() and int(pct) >= disk_warn_pct:
                items.append((1, pi.name, f"SD-Karte zu {pct} % voll"))
            temp = dev.get("temp", "").split(" ")[0]
            if temp.isdigit() and int(temp) >= temp_warn:
                items.append((1, pi.name, f"Temperatur {temp} °C"))
            if dev.get("reboot") == "ja":
                items.append((1, pi.name, "Neustart nötig"))
            if dev.get("sudo") == "fehlt" or dev.get("rsync") == "fehlt":
                items.append((0, pi.name, "sudo ohne Passwort oder rsync fehlt, Sicherung nicht möglich"))
    if summary["total"] and summary["avail"] / summary["total"] < 0.1:
        items.append((0, "Unraid", "Weniger als 10 % Speicher frei"))
    return sorted(items, key=lambda i: i[0])


@dataclass
class Inventory:
    categories: list = field(default_factory=list)
    pis: list = field(default_factory=list)

    def all_categories(self):
        """Every category including implied parents and those only used by a Pi."""
        out = set()
        for c in list(self.categories) + [p.category for p in self.pis]:
            parts = c.split("/") if c else []
            for i in range(1, len(parts) + 1):
                out.add("/".join(parts[:i]))
        return sorted(out, key=str.casefold)

    def find(self, pi_id):
        return next((p for p in self.pis if p.id == pi_id), None)

    def name_taken(self, name, category, ignore_id=None):
        return any(p.name == name and p.category == category and p.id != ignore_id for p in self.pis)


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit in ("B", "KiB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
