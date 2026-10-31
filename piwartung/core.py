"""Data model and Unraid access for Pi-Wartung. No GUI code in here."""
import json
import hashlib
import os
import re
import shlex
import subprocess
import uuid
from dataclasses import asdict, dataclass, field, fields

CONFIG_DIR = os.path.expanduser("~/.config/pi-wartung")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
ENGINE_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "engine", "pi-backup.sh")
USER_SCRIPT_DIR = "/boot/config/plugins/user.scripts"
USER_SCRIPT_NAME = "PiWartung-Geplant"
DEFAULT_KEY = "/root/.ssh/pi-wartung"


class UnraidError(Exception):
    pass


@dataclass
class Settings:
    unraid_ssh: str = "unraid"
    root: str = "/mnt/user/backup1/Raspis/PiWartung"
    remote_prefix: str = "/mnt/user"
    local_prefix: str = "/mnt/unraid"

    @classmethod
    def load(cls):
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def to_local(self, remote_path):
        if remote_path.startswith(self.remote_prefix + "/"):
            return self.local_prefix + remote_path[len(self.remote_prefix):]
        return remote_path


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
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def conf_text(self):
        values = {
            "NAME": self.name, "HOST": self.host, "PORT": self.port, "USER_NAME": self.user,
            "KEY": self.key, "JUMP": self.jump, "JUMP_PORT": self.jump_port, "KEEP": self.keep,
            "FORMAT": self.format, "EXPAND": int(self.expand), "EXCLUDES": self.excludes,
            "BWLIMIT": self.bwlimit, "COMPRESS": int(self.compress), "PRE_CMD": self.pre_cmd,
            "POST_CMD": self.post_cmd, "SCHEDULE": self.schedule, "HOUR": self.hour,
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


def ssh_config_hosts(path="~/.ssh/config"):
    """Hosts from the user's ssh config, to prefill a new Pi."""
    hosts, current = {}, []
    try:
        with open(os.path.expanduser(path)) as f:
            for raw in f:
                parts = raw.split(None, 1)
                if len(parts) < 2 or parts[0].startswith("#"):
                    continue
                key, value = parts[0].lower(), parts[1].strip()
                if key == "host":
                    current = [h for h in value.split() if "*" not in h and "?" not in h]
                    for h in current:
                        hosts.setdefault(h, {"host": h, "port": 22, "user": "pi", "identity": ""})
                elif key == "hostname":
                    for h in current:
                        hosts[h]["host"] = value
                elif key == "port" and value.isdigit():
                    for h in current:
                        hosts[h]["port"] = int(value)
                elif key == "user":
                    for h in current:
                        hosts[h]["user"] = value
                elif key == "identityfile":
                    for h in current:
                        hosts[h]["identity"] = value
    except OSError:
        pass
    return hosts


class Unraid:
    def __init__(self, settings):
        self.s = settings
        self.engine = f"{settings.root}/.engine/pi-backup.sh"
        self.inventory_file = f"{settings.root}/inventory.json"

    def _ssh(self):
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "LogLevel=ERROR", self.s.unraid_ssh]

    def run(self, command, stdin=None, timeout=120):
        try:
            p = subprocess.run(self._ssh() + [command], input=stdin, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise UnraidError("Zeitüberschreitung bei der Verbindung zum Unraid")
        if p.returncode != 0:
            raise UnraidError((p.stderr or p.stdout).strip() or f"Fehlercode {p.returncode}")
        return p.stdout

    def tail_process(self, path):
        cmd = f"tail -n +1 -F {shlex.quote(path)} 2>/dev/null"
        return subprocess.Popen(self._ssh() + [cmd], stdout=subprocess.PIPE, text=True,
                                errors="replace")

    def write_file(self, path, text, mode=None):
        q = shlex.quote(path)
        cmd = f"mkdir -p \"$(dirname {q})\" && cat > {q}.new && mv {q}.new {q}"
        if mode:
            cmd += f" && chmod {mode} {q}"
        self.run(cmd, stdin=text)

    def deploy_engine(self):
        with open(ENGINE_SRC) as f:
            text = f.read()
        want = hashlib.sha256(text.encode()).hexdigest()
        q = shlex.quote(self.engine)
        have = self.run(f"sha256sum {q} 2>/dev/null | cut -d' ' -f1; true").strip()
        if have != want:
            self.write_file(self.engine, text, mode="700")

    # ---- inventory
    def load_inventory(self):
        text = self.run(f"cat {shlex.quote(self.inventory_file)} 2>/dev/null; true")
        if not text.strip():
            return Inventory()
        data = json.loads(text)
        known = {f.name for f in fields(Pi)}
        pis = [Pi(**{k: v for k, v in p.items() if k in known}) for p in data.get("pis", [])]
        return Inventory(categories=data.get("categories", []), pis=pis)

    def save_inventory(self, inv):
        data = {"categories": sorted(inv.categories), "pis": [asdict(p) for p in inv.pis]}
        self.write_file(self.inventory_file, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    # ---- per Pi
    def pidir(self, pi):
        parts = [self.s.root] + ([pi.category] if pi.category else []) + [pi.name]
        return "/".join(parts)

    def write_conf(self, pi):
        self.write_file(f"{self.pidir(pi)}/pi.conf", pi.conf_text(), mode="600")

    def move_pi(self, old, new):
        """Moves images and mirror along when a Pi is renamed or recategorized."""
        src, dst = self.pidir(old), self.pidir(new)
        if src == dst:
            return
        q_src, q_dst = shlex.quote(src), shlex.quote(dst)
        self.run(f"if [ -d {q_src} ]; then [ ! -e {q_dst} ] || {{ echo 'Ziel existiert schon' >&2; exit 1; }}; "
                 f"mkdir -p \"$(dirname {q_dst})\" && mv {q_src} {q_dst}; fi", timeout=600)
        if old.name != new.name:
            # Image names carry the Pi name; keep them recognisable for retention.
            self.run(f"cd {q_dst} 2>/dev/null || exit 0; for f in {shlex.quote(old.name)}_*; do "
                     f"[ -e \"$f\" ] && mv \"$f\" {shlex.quote(new.name)}\"_${{f#{shlex.quote(old.name)}_}}\"; done; true")

    def engine_cmd(self, verb, pi, timeout=120):
        return self.run(f"bash {shlex.quote(self.engine)} {verb} {shlex.quote(self.pidir(pi))}",
                        timeout=timeout)

    def state(self, pi):
        return parse_shell_kv(self.engine_cmd("state", pi))

    def status(self, pi):
        return parse_kv(self.engine_cmd("status", pi, timeout=60))

    def start_backup(self, pi):
        return self.engine_cmd("start", pi).strip()

    def cancel(self, pi):
        return self.engine_cmd("cancel", pi)

    def images(self, pi):
        rows = []
        for line in self.engine_cmd("images", pi).splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            info = parse_kv(parts[3].replace("\x1e", "\n")) if len(parts) > 3 else {}
            rows.append({"file": parts[0], "size": int(parts[1]), "mtime": int(parts[2]), "info": info})
        return rows

    def delete_image(self, pi, filename):
        if "/" in filename or not filename.startswith(pi.name + "_"):
            raise UnraidError("Ungültiger Dateiname")
        path = f"{self.pidir(pi)}/{filename}"
        self.run(f"rm -f {shlex.quote(path)} {shlex.quote(path + '.info')}")

    def delete_pi_data(self, pi):
        d = self.pidir(pi)
        if not d.startswith(self.s.root + "/") or not pi.name:
            raise UnraidError("Ungültiger Pfad")
        self.run(f"rm -rf {shlex.quote(d)}", timeout=300)

    # ---- access
    def install_key_with_password(self, pi, password):
        """Creates the key of this Pi on the server and installs it with a one-time password."""
        self.write_conf(pi)
        out = self.run(f"bash {shlex.quote(self.engine)} install-key {shlex.quote(self.pidir(pi))}",
                       stdin=password + "\n", timeout=90)
        return parse_kv(out)

    def install_key_from_desktop(self, pi):
        """Same, but through the user's own ssh login, also on a jump host if there is one."""
        self.write_conf(pi)
        pub = self.engine_cmd("keygen", pi).strip()
        script = ("umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
                  f"grep -qxF {shlex.quote(pub)} ~/.ssh/authorized_keys || "
                  f"echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys")
        logins = [pi.ssh_command()]
        if pi.jump:
            logins.insert(0, ["ssh", "-p", str(pi.jump_port), pi.jump])
        for login in logins:
            p = subprocess.run(login[:1] + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"] + login[1:] + [script],
                               capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                raise UnraidError(p.stderr.strip() or "Anmeldung fehlgeschlagen")
        return self.status(pi)

    def list_keys(self):
        out = self.run("ls -1 /root/.ssh/*.pub 2>/dev/null | sed 's/\\.pub$//'; true")
        return [line for line in out.splitlines() if line]

    # ---- schedule through the User Scripts plugin
    def install_user_script(self):
        d = f"{USER_SCRIPT_DIR}/scripts/{USER_SCRIPT_NAME}"
        script = (f"#!/bin/bash\n# Prüft stündlich, welche Pis laut Pi-Wartung fällig sind.\n"
                  f"[ -f {shlex.quote(self.engine)} ] || exit 0\n"
                  f"exec bash {shlex.quote(self.engine)} due {shlex.quote(self.s.root)}\n")
        self.write_file(f"{d}/script", script)
        self.write_file(f"{d}/name", USER_SCRIPT_NAME + "\n")
        self.write_file(f"{d}/description", "Geplante Image-Sicherungen aus der Pi-Wartung-App\n")
        sched_file = f"{USER_SCRIPT_DIR}/schedule.json"
        cron_file = f"{USER_SCRIPT_DIR}/customSchedule.cron"
        schedule = json.loads(self.run(f"cat {sched_file} 2>/dev/null || echo '{{}}'") or "{}")
        key = f"{d}/script"
        expr = "5 * * * *"
        if schedule.get(key, {}).get("custom") != expr:
            self.run(f"cp {sched_file} {sched_file}.pi-wartung.bak 2>/dev/null; true")
            schedule[key] = {"script": key, "frequency": "custom",
                             "id": "schedule" + USER_SCRIPT_NAME, "custom": expr}
            self.write_file(sched_file, json.dumps(schedule, indent=4) + "\n")
        line = f"{expr} /usr/local/emhttp/plugins/user.scripts/startCustom.php {key} > /dev/null 2>&1"
        self.run(f"touch {cron_file}; grep -qF {shlex.quote(key)} {cron_file} || "
                 f"echo {shlex.quote(line)} >> {cron_file}; /usr/local/sbin/update_cron")


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
