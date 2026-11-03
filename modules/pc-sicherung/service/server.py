#!/usr/bin/env python3
"""PC-Sicherung: sichert ganze PCs samt allen Systemen über einen Netzwerkstart auf den Server.

Drei Seiten:
  - Weboberfläche und App sprechen über die Socket-Datei, die nur der Zugang erreicht.
  - Die PCs starten über das Netz (Proxy-DHCP aus proxydhcp.py, TFTP aus tftp.py) und holen sich auf BOOT_PORT ihr Startsystem und
    ihren Auftrag; dort zählt nur das Einmal-Kennwort des Auftrags.
  - Die Daten gehen über rest-server auf REST_PORT in ein restic-Archiv je PC, mit eigenem Zugang je PC und ohne Recht
    zum Löschen. Aufräumen macht dieser Dienst selbst direkt im Archiv.
"""
import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxydhcp import ProxyDHCP  # noqa: E402
from tftp import TFTPServer  # noqa: E402
from store import AFTER, OVERDUE_DAYS, Store, category_allowed, next_due, normal_mac, run_label  # noqa: E402

DATA = os.environ.get("DATA", "/data")
BACKUPS = os.environ.get("BACKUPS", "/backups")
SRV = os.environ.get("SRV", "/srv")
LISTEN_SOCKET = os.environ.get("LISTEN_SOCKET", "")
EVENTS_DIR = os.environ.get("EVENTS_DIR", "")
BOOT_PORT = int(os.environ.get("BOOT_PORT", "8395"))
REST_PORT = int(os.environ.get("REST_PORT", "8394"))
API_PORT = int(os.environ.get("PORT", "0"))  # only for tests without the gateway
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
RUN_DIR = os.environ.get("RUN_DIR", "/run/pc-sicherung-boot")
KERNEL_EXTRA = os.environ.get("KERNEL_EXTRA", "")  # e.g. console=ttyS0 when looking for faults
DISK_LOW = 0.1


def log(text):
    print(text, file=sys.stderr, flush=True)


# --- Meldungen -----------------------------------------------------------------------------------

def spool_event(kind, title, message, priority="default", category="", link=""):
    if not EVENTS_DIR:
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=EVENTS_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"type": kind, "title": title, "message": message, "priority": priority,
                       "category": category, "link": link}, f, ensure_ascii=False)
        os.rename(tmp, tmp[:-4] + ".json")
    except OSError as exc:
        log(f"Meldung nicht abgelegt: {exc}")


# --- restic direkt im Archiv ---------------------------------------------------------------------

class Archive:
    """restic on the archive folders; the PCs themselves only reach them through rest-server."""

    def __init__(self, root):
        self.root = root
        self.locks = {}
        self.locks_lock = threading.Lock()
        self.cache = {}

    def lock(self, pc_id):
        with self.locks_lock:
            return self.locks.setdefault(pc_id, threading.Lock())

    def path(self, pc_id):
        return os.path.join(self.root, pc_id)

    def run(self, pc, *args, timeout=600, check=True):
        env = dict(os.environ, RESTIC_REPOSITORY=self.path(pc["id"]), RESTIC_PASSWORD=pc["repo_password"],
                   RESTIC_CACHE_DIR="/tmp/restic-cache")
        p = subprocess.run(["restic", *args], capture_output=True, text=True, timeout=timeout, env=env)
        if check and p.returncode != 0:
            raise RuntimeError((p.stderr.strip().splitlines() or [f"restic Fehler {p.returncode}"])[-1])
        return p.stdout

    def ensure(self, pc):
        if os.path.exists(os.path.join(self.path(pc["id"]), "config")):
            return
        os.makedirs(self.path(pc["id"]), exist_ok=True)
        self.run(pc, "init", "--repository-version", "2")

    def runs(self, pc, fresh=False):
        """[{id, label, time, complete, files}] newest first, from the snapshots in the archive."""
        cached = self.cache.get(pc["id"])
        if cached and not fresh and time.time() - cached[0] < 60:
            return cached[1]
        if not os.path.exists(os.path.join(self.path(pc["id"]), "config")):
            return []
        snaps = json.loads(self.run(pc, "snapshots", "--json", "--no-lock", timeout=120) or "[]")
        runs = {}
        for snap in snaps:
            run = next((t[4:] for t in snap.get("tags") or [] if t.startswith("run=")), None)
            if not run:
                continue
            entry = runs.setdefault(run, {"id": run, "label": run_label(run), "files": [], "complete": False,
                                          "snapshots": []})
            name = (snap.get("paths") or ["/"])[0].lstrip("/")
            entry["files"].append(name)
            entry["snapshots"].append(snap["short_id"])
            entry["complete"] = entry["complete"] or name == "meta.tar"
        out = sorted(runs.values(), key=lambda r: r["id"], reverse=True)
        self.cache[pc["id"]] = (time.time(), out)
        return out

    def parts(self, pc, run):
        """Partitions of one run, as the boot system recorded them."""
        snaps = json.loads(self.run(pc, "snapshots", "--json", "--no-lock", "--tag", f"run={run}") or "[]")
        meta = next((s for s in snaps if (s.get("paths") or [""])[0] == "/meta.tar"), None)
        if not meta:
            raise LookupError
        env = dict(os.environ, RESTIC_REPOSITORY=self.path(pc["id"]), RESTIC_PASSWORD=pc["repo_password"],
                   RESTIC_CACHE_DIR="/tmp/restic-cache")
        with tempfile.TemporaryDirectory() as tmp:
            dump = subprocess.run(["restic", "dump", "--no-lock", meta["short_id"], "/meta.tar"], capture_output=True,
                                  env=env, timeout=120)
            if dump.returncode != 0:
                raise RuntimeError("Beschreibung der Sicherung nicht lesbar")
            subprocess.run(["tar", "-x", "-C", tmp, "./disks.tsv", "./parts.tsv"], input=dump.stdout, check=True,
                           capture_output=True)
            disks = [line.split("\t") for line in open(os.path.join(tmp, "disks.tsv")).read().splitlines()]
            parts = [line.split("\t") for line in open(os.path.join(tmp, "parts.tsv")).read().splitlines()]
        def value(text):
            return "" if text == "-" else text

        disk_rows = [{"key": d[0], "size": int(d[2]), "serial": value(d[3]), "model": value(d[4]).replace("_", " "), "table": d[5]}
                     for d in disks if len(d) >= 6]
        part_rows = [{"key": p[0], "size": int(p[2]), "fs": p[3], "method": p[4], "label": value(p[6]) if len(p) > 6 else ""}
                     for p in parts if len(p) >= 5]
        return {"disks": disk_rows, "parts": part_rows}

    def size(self, pc_id):
        total = 0
        for folder, _dirs, files in os.walk(self.path(pc_id)):
            for name in files:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except OSError:
                    pass
        return total

    def tidy(self, pc, keep, also=()):
        """Keeps the newest complete runs; drops older ones and leftovers of broken runs, then frees the space."""
        with self.lock(pc["id"]):
            runs = self.runs(pc, fresh=True)
            complete = [r for r in runs if r["complete"]]
            keep_ids = {r["id"] for r in complete[:keep]} - set(also)
            newest = complete[0]["id"] if complete else ""
            drop = [r for r in runs if r["id"] not in keep_ids and (r["complete"] or r["id"] < newest or r["id"] in also)]
            if not drop:
                return 0
            ids = [s for r in drop for s in r["snapshots"]]
            self.run(pc, "forget", *ids, timeout=600)
            self.run(pc, "prune", "--max-unused", "0", timeout=6 * 3600)
            self.cache.pop(pc["id"], None)
            return len(drop)


# --- Zugang für die PCs (rest-server) und Netzwerkstart --------------------------------

class Services:
    """Starts and watches the helper programs; a crashed helper comes back within a minute."""

    def __init__(self, store):
        self.store = store
        self.procs = {}
        self.netboot_error = ""
        self.lock = threading.Lock()

    def write_htpasswd(self):
        lines = []
        for pc in self.store.all_pcs():
            out = subprocess.run(["htpasswd", "-nbB", pc["id"], pc["rest_password"]], capture_output=True, text=True,
                                 check=True).stdout.strip()
            lines.append(out)
        path = os.path.join(BACKUPS, ".htpasswd")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        self.write_keys()

    def write_keys(self):
        """The archive passwords beside the archives: without them nothing could be restored if Home-Net were lost."""
        keys = {pc["id"]: {"name": pc["name"], "password": pc["repo_password"]} for pc in self.store.all_pcs()}
        path = os.path.join(BACKUPS, "schluessel.json")
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(keys, f, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)

    def users_changed(self):
        self.write_htpasswd()
        proc = self.procs.get("rest")
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGHUP)

    def start_rest(self):
        self.write_htpasswd()
        return subprocess.Popen(["rest-server", "--path", BACKUPS, "--listen", f":{REST_PORT}", "--append-only",
                                 "--private-repos", "--htpasswd-file", os.path.join(BACKUPS, ".htpasswd")],
                                stdout=subprocess.DEVNULL, stderr=sys.stderr)

    def server_ip(self):
        ip = self.store.settings.get("server_ip")
        return ip or detect_ip()

    def start_netboot(self):
        ip = self.server_ip()
        iface, _network = interface_of(ip)
        if not iface:
            self.netboot_error = f"Keine Netzwerkkarte mit der Adresse {ip} gefunden"
            return None
        files = TFTPServer(os.path.join(RUN_DIR, "tftp"), ip, log=log)
        proxy = ProxyDHCP(iface, ip, log=log, known=self.known_mac)
        try:
            files.start()
            proxy.start()
        except OSError as exc:
            files.stop()
            proxy.stop()
            self.netboot_error = f"Port 67, 69 oder 4011 belegt ({exc})"
            return None
        self.netboot_error = ""
        return Netboot(proxy, files)

    def known_mac(self, mac):
        return any(mac in pc["macs"] for pc in self.store.all_pcs())

    def prepare_tftp(self):
        """The TFTP tree with a start file that knows this server's address."""
        tftp = os.path.join(RUN_DIR, "tftp")
        shutil.rmtree(tftp, ignore_errors=True)
        shutil.copytree(os.path.join(SRV, "tftp"), tftp)
        os.makedirs(os.path.join(tftp, "grub"), exist_ok=True)
        with open(os.path.join(tftp, "grub", "grub.cfg"), "w") as f:
            f.write(grub_entry(self.server_ip()))
        with open(os.path.join(tftp, "start.0"), "w") as f:
            f.write(ipxe_entry(self.server_ip()))

    def check(self):
        with self.lock:
            rest = self.procs.get("rest")
            if not rest or rest.poll() is not None:
                self.procs["rest"] = self.start_rest()
            net = self.procs.get("dns")
            wanted = self.store.settings.get("netboot")
            if wanted and (not net or net.poll() is not None):
                if net:
                    net.terminate()
                self.prepare_tftp()
                self.procs["dns"] = self.start_netboot()
            elif not wanted and net:
                net.terminate()
                self.procs["dns"] = None
                self.netboot_error = ""

    def restart_netboot(self):
        with self.lock:
            net = self.procs.get("dns")
            if net:
                net.terminate()
            self.procs["dns"] = None
        self.check()

    def netboot_state(self):
        dns = self.procs.get("dns")
        return {"enabled": bool(self.store.settings.get("netboot")), "running": bool(dns and dns.poll() is None),
                "error": self.netboot_error, "server_ip": self.server_ip(),
                "configured_ip": self.store.settings.get("server_ip", "")}


def detect_ip():
    """The address this server uses towards the home network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def interface_of(ip):
    """(interface, network/prefix) that holds this address."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.search(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/(\d+)", line)
        if m and m.group(2) == ip:
            import ipaddress
            net = ipaddress.ip_network(f"{ip}/{m.group(3)}", strict=False)
            return m.group(1).split("@")[0], str(net.network_address)
    return None, None


class Netboot:
    """Proxy DHCP and TFTP together, in the place of a process: poll() is None while both run."""

    def __init__(self, proxy, files):
        self.proxy = proxy
        self.files = files

    def poll(self):
        return None if self.proxy.alive() and self.files.alive() else 1

    def terminate(self):
        self.proxy.stop()
        self.files.stop()


def posix_tz():
    """The server's time zone as a POSIX rule ("CET-1CEST,M3.5.0,M10.5.0/3"), which the boot system can use without
    a zone database: the last line of a TZif file."""
    name = os.environ.get("TZ", "").lstrip(":")
    for path in ([os.path.join("/usr/share/zoneinfo", name)] if name else []) + ["/etc/localtime"]:
        try:
            with open(path, "rb") as f:
                rule = f.read().rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("ascii")
        except (OSError, UnicodeDecodeError):
            continue
        if rule and not rule.startswith("TZif") and len(rule) < 80:
            return rule
    return "UTC0"


def grub_entry(ip):
    """First file every PC loads: it asks this service what to do with exactly this network card."""
    # If the server does not answer, the PC must still start its own system and never stop at a GRUB prompt
    return f"""set timeout=0
insmod http
for try in 1 2 3; do
  configfile (http,{ip}:{BOOT_PORT})/boot/grub/$net_default_mac
  sleep 1
done
exit
"""


def grub_local():
    """No task: back to the firmware, which starts the next entry, normally Windows or Linux."""
    return """set timeout=0
exit
"""


def ipxe_entry(ip):
    return f"""#!ipxe
chain http://{ip}:{BOOT_PORT}/boot/ipxe/${{net0/mac}} || chain http://{ip}:{BOOT_PORT}/boot/ipxe/${{net0/mac}} || exit
"""


def ipxe_local():
    return "#!ipxe\nexit\n"


def ipxe_live(ip, token):
    server = f"http://{ip}:{BOOT_PORT}"
    return f"""#!ipxe
echo Home-Net PC-Sicherung wird geladen ...
kernel {server}/boot/vmlinuz quiet loglevel=3 hn_server={server} hn_token={token} {KERNEL_EXTRA}
initrd {server}/boot/initrd.img
boot
"""


def grub_live(ip, token):
    server = f"http://{ip}:{BOOT_PORT}"
    return f"""set timeout=0
echo "Home-Net PC-Sicherung wird geladen ..."
linux (http,{ip}:{BOOT_PORT})/boot/vmlinuz quiet loglevel=3 hn_server={server} hn_token={token} {KERNEL_EXTRA}
initrd (http,{ip}:{BOOT_PORT})/boot/initrd.img
boot
"""


def wake(macs):
    """Wake-on-LAN to the whole home network."""
    for mac in macs:
        payload = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        for target in ("255.255.255.255", broadcast_of(detect_ip())):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(payload, (target, 9))
            except OSError:
                pass


def broadcast_of(ip):
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.search(r"inet ([\d.]+)/\d+ brd ([\d.]+)", line)
        if m and m.group(1) == ip:
            return m.group(2)
    return "255.255.255.255"


# --- Stick ---------------------------------------------------------------------------------------

STICK_MB = 160


def build_stick(ip, target):
    """A FAT image with the signed start files and the boot system; Etcher or the Pi Imager write it to a stick."""
    server = f"http://{ip}:{BOOT_PORT}"
    cfg = f"""set timeout=3
set default=0
menuentry "Home-Net PC-Sicherung ({ip})" {{
  linux /hn/vmlinuz quiet loglevel=3 hn_server={server} hn_stick=1
  initrd /hn/initrd.img
}}
menuentry "Vom Laufwerk des PCs starten" {{
  exit
}}
"""
    tmp = target + ".tmp"
    with tempfile.TemporaryDirectory() as work:
        with open(os.path.join(work, "grub.cfg"), "w") as f:
            f.write(cfg)
        with open(os.path.join(work, "LIESMICH.txt"), "w") as f:
            f.write(f"Home-Net PC-Sicherung\r\nDieser Stick startet das Sicherungssystem und meldet sich bei {server}.\r\n")
        subprocess.run(["truncate", "-s", f"{STICK_MB}M", tmp], check=True)
        # A partition table, so that firmware and Windows treat it like any stick
        subprocess.run(["sfdisk", "-q", tmp], input="label: dos\nstart=2048, type=ef, bootable\n", text=True, check=True,
                       capture_output=True)
        offset = 2048 * 512
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
        img = f"{tmp}@@{offset}"
        subprocess.run(["mformat", "-i", img, "-F", "-v", "HN-SICHERUNG", "-T", str((STICK_MB * 2048) - 2048), "::"],
                       check=True, env=env, capture_output=True)
        for folder in ("::/EFI", "::/EFI/BOOT", "::/EFI/ubuntu", "::/hn"):
            subprocess.run(["mmd", "-i", img, folder], check=True, env=env, capture_output=True)
        copies = [(os.path.join(SRV, "stick", "BOOTX64.EFI"), "::/EFI/BOOT/BOOTX64.EFI"),
                  (os.path.join(SRV, "stick", "grubx64.efi"), "::/EFI/BOOT/grubx64.efi"),
                  (os.path.join(SRV, "stick", "mmx64.efi"), "::/EFI/BOOT/mmx64.efi"),
                  (os.path.join(work, "grub.cfg"), "::/EFI/BOOT/grub.cfg"),
                  (os.path.join(work, "grub.cfg"), "::/EFI/ubuntu/grub.cfg"),
                  (os.path.join(SRV, "live", "vmlinuz"), "::/hn/vmlinuz"),
                  (os.path.join(SRV, "live", "initrd.img"), "::/hn/initrd.img"),
                  (os.path.join(work, "LIESMICH.txt"), "::/LIESMICH.txt")]
        for src, dst in copies:
            subprocess.run(["mcopy", "-i", img, src, dst], check=True, env=env, capture_output=True)
    os.replace(tmp, target)


# --- Ablauf ---------------------------------------------------------------------------------------

class Engine:
    """What happens with the tasks: waking, finishing, tidying up, warnings."""

    def __init__(self, store, archive, services):
        self.store = store
        self.archive = archive
        self.services = services
        self.tidying = set()

    def start(self, pc, action, origin="manual", after=None, run=None, parts=None, token=None, who=""):
        if action == "backup":
            self.archive.ensure(pc)
        job = self.store.arm(pc["id"], action, origin, after or pc["after"], run=run, parts=parts, token=token, who=who)
        if pc.get("wake", True) and not token:
            wake(pc["macs"])
        return job

    def job_for_live(self, pc_id, job):
        pc = self.store.find(pc_id)
        ip = self.services.server_ip()
        return {"action": job["action"], "pc": pc["id"], "pc_name": pc["name"], "run": job["run"],
                "run_label": run_label(job["run"]), "after": job["after"], "parts": job["parts"],
                "skip_disks": pc.get("skip_disks", []), "compression": self.store.settings.get("compression", "max"),
                "repository": f"rest:http://{pc['id']}:{pc['rest_password']}@{ip}:{REST_PORT}/{pc['id']}/",
                "password": pc["repo_password"]}

    def done(self, pc_id, ok, message, stats=None, log_text="", result=None):
        job = self.store.finish(pc_id, result or ("ok" if ok else "failed"), message, stats, log_text)
        if not job:
            return
        pc = self.store.find(pc_id)
        if not pc:
            return
        entry = job["entry"]
        self.archive.cache.pop(pc_id, None)
        link = f"pc/{pc_id}"
        if job["action"] == "backup":
            if entry["result"] == "ok":
                added = entry["added"]
                spool_event("backup_ok", f"{pc['name']} gesichert",
                            f"Neu auf dem Server: {human(added)}", category=pc["category"], link=link)
                if self.store.last(pc_id, "backup") and self.was_failing(pc_id):
                    spool_event("backup_recovered", f"Sicherung von {pc['name']} klappt wieder",
                                "Die letzte Sicherung ist gelungen.", category=pc["category"], link=link)
                self.store.clear_sent(f"overdue:{pc_id}")
                self.tidy_later(pc)
            elif entry["result"] == "missed":
                spool_event("pc_not_reached", f"{pc['name']} nicht gestartet",
                            "Der PC hat nicht über das Netz gestartet. War er eingeschaltet, oder fehlt Wake-on-LAN "
                            "oder der Netzwerkstart?", "high", pc["category"], link)
            elif entry["result"] != "cancelled":
                spool_event("backup_failed", f"Sicherung von {pc['name']} fehlgeschlagen", message, "high",
                            pc["category"], link)
        else:
            if entry["result"] == "ok":
                spool_event("restore_done", f"{pc['name']} wiederhergestellt",
                            f"Stand {run_label(job['run'])} ist zurückgeschrieben.", category=pc["category"], link=link)
            elif entry["result"] != "cancelled":
                spool_event("restore_failed", f"Wiederherstellung von {pc['name']} fehlgeschlagen", message, "high",
                            pc["category"], link)

    def was_failing(self, pc_id):
        items = [e for e in self.store.history(pc_id) if e["action"] == "backup"]
        return len(items) > 1 and items[0]["result"] == "ok" and items[1]["result"] in ("failed", "missed")

    def tidy_later(self, pc, also=()):
        def work():
            try:
                self.archive.tidy(pc, pc["keep"], also)
            except Exception as exc:  # tidying failing must not stop the service; the next run tries again
                log(f"Aufräumen bei {pc['name']} fehlgeschlagen: {exc}")
            finally:
                self.tidying.discard(pc["id"])

        if pc["id"] not in self.tidying:
            self.tidying.add(pc["id"])
            threading.Thread(target=work, daemon=True).start()

    def tick(self, now=None):
        now = now or time.time()
        for pc_id, why in self.store.expired(now):
            if why == "missed":
                self.done(pc_id, False, "Der PC hat nicht über das Netz gestartet.", result="missed")
            else:
                self.done(pc_id, False, "Keine Rückmeldung mehr vom PC.")
        for pc in self.store.take_due(now):
            if self.store.job(pc["id"]):
                continue
            try:
                self.start(pc, "backup", origin="auto", after=pc["after"], who="Zeitplan")
            except Exception as exc:
                self.store.arm(pc["id"], "backup", "auto", pc["after"])
                self.done(pc["id"], False, f"Sicherung nicht gestartet: {exc}")
        self.check_overdue(now)
        self.check_disk()

    def check_overdue(self, now):
        for pc in self.store.all_pcs():
            days = OVERDUE_DAYS.get(pc["schedule"])
            if not days or self.store.job(pc["id"]):
                continue
            last = self.store.last(pc["id"], "backup", "ok")
            since = last["end"] if last else pc["created"]
            if now - since > days * 86400 and self.store.once_a_day(f"overdue:{pc['id']}", now):
                age = int((now - since) / 86400)
                spool_event("backup_overdue", f"Sicherung von {pc['name']} überfällig",
                            f"Die letzte gelungene Sicherung ist {age} Tage her." if last else
                            "Es gibt noch keine gelungene Sicherung.", "high", pc["category"], f"pc/{pc['id']}")

    def check_disk(self):
        try:
            st = os.statvfs(BACKUPS)
        except OSError:
            return
        if st.f_blocks and st.f_bavail / st.f_blocks < DISK_LOW and self.store.once_a_day("disk"):
            spool_event("disk_low", "Speicherplatz für PC-Sicherungen knapp",
                        f"Frei sind noch {human(st.f_bavail * st.f_frsize)}.", "high")

    sizes = {}
    stick_lock = threading.Lock()

    def overview(self, scopes, manager):
        rows = [self.row(pc) for pc in sorted(self.store.all_pcs(scopes), key=lambda p: (p["category"].casefold(),
                                                                                        p["name"].casefold()))]
        try:
            st = os.statvfs(BACKUPS)
            disk = {"avail": st.f_bavail * st.f_frsize, "total": st.f_blocks * st.f_frsize}
        except OSError:
            disk = {"avail": 0, "total": 0}
        return {"pcs": rows, "waiting": self.store.waiting_list(scopes) if manager else [],
                "unknown": self.store.seen_unknown() if manager else [], "manager": manager,
                "bytes": sum(r["bytes"] for r in rows), "disk": disk, "netboot": self.services.netboot_state(),
                "stick": f"http://{self.services.server_ip()}:{BOOT_PORT}/stick.img"}


    def row(self, pc):
        job = self.store.job(pc["id"])
        last = self.store.last(pc["id"], "backup")
        last_ok = self.store.last(pc["id"], "backup", "ok")
        return {"id": pc["id"], "name": pc["name"], "category": pc["category"], "schedule": pc["schedule"],
                "job": public_job(job), "status": last["result"] if last else "", "message": last["message"] if last else "",
                "ok_time": last_ok["end"] if last_ok else 0, "bytes": self.size_of(pc["id"]),
                "next": next_due(pc, time.time())}


    def size_of(self, pc_id):
        cached = self.sizes.get(pc_id)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        value = self.archive.size(pc_id)
        self.sizes[pc_id] = (time.time(), value)
        return value


    def detail(self, pc, manager):
        out = self.row(pc)
        try:
            runs = self.archive.runs(pc)
            error = ""
        except Exception as exc:
            runs, error = [], str(exc)
        history = self.store.history(pc["id"])
        added = {e["run"]: e for e in history if e["action"] == "backup" and e["result"] == "ok"}
        out.update(runs=[{"id": r["id"], "label": r["label"], "complete": r["complete"],
                          "added": added.get(r["id"], {}).get("added", 0)} for r in runs],
                   archive_error=error, history=[{k: v for k, v in e.items() if manager or k != "log"} for e in history],
                   macs=pc["macs"], hour=pc["hour"], weekday=pc.get("weekday", 6), keep=pc["keep"], after=pc["after"],
                   wake=pc.get("wake", True), notes=pc.get("notes", ""), manager=manager,
                   skip_disks=pc.get("skip_disks", []))
        return out


    def assign(self, short, body, scopes, who):
        """Gives a PC that waits after starting from the stick its task."""
        token = self.store.waiting_token(short)
        waiting = self.store.waiting(token) if token else None
        if not waiting:
            raise LookupError
        if body.get("pc"):
            pc = self.store.find(str(body["pc"]), scopes)
            if not pc:
                raise LookupError
            if waiting["mac"] and waiting["mac"] not in pc["macs"] and body.get("add_mac", True):
                pc = self.store.update(pc["id"], {"macs": pc["macs"] + [waiting["mac"]]}, scopes)
        else:
            pc = self.store.create(dict(body.get("new") or {}, macs=[waiting["mac"]]), scopes)
            self.services.users_changed()
        action = body.get("action", "backup")
        if action not in ("backup", "restore"):
            raise ValueError("Ungültige Aktion")
        run, parts = None, []
        if action == "restore":
            run = str(body.get("run", ""))
            runs = {r["id"]: r for r in self.archive.runs(pc, fresh=True)}
            if run not in runs or not runs[run]["complete"]:
                raise ValueError("Diese Sicherung gibt es nicht oder sie ist unvollständig.")
            parts = [str(p) for p in body.get("parts") or [] if re.fullmatch(r"d\d+p\d+", str(p))]
        after = body.get("after", "reboot")
        self.start(pc, action, after=after if after in AFTER else "reboot", run=run, parts=parts, token=token, who=who)
        self.store.take_waiting(token)
        return {"id": pc["id"]}


    def stick(self):
        with self.stick_lock:
            target = os.path.join(RUN_DIR, "stick.img")
            stamp = os.path.join(RUN_DIR, "stick.ip")
            ip = self.services.server_ip()
            source = os.path.getmtime(os.path.join(SRV, "live", "initrd.img"))
            try:
                fresh = open(stamp).read() == ip and os.path.getmtime(target) > source
            except OSError:
                fresh = False
            if not fresh:
                build_stick(ip, target)
                with open(stamp, "w") as f:
                    f.write(ip)
            return target


    def drop_stick(self):
        for name in ("stick.img", "stick.ip"):
            try:
                os.unlink(os.path.join(RUN_DIR, name))
            except OSError:
                pass


def human(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(n or 0)
    i = 0
    while n >= 1024 and i < 4:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}".replace(".", ",") if i > 1 else f"{int(n)} {units[i]}"


def ticker(engine):
    while True:
        try:
            engine.services.check()
            engine.tick()
        except Exception as exc:  # the service must keep running
            log(f"Ablauf: {exc!r}")
        time.sleep(30)


# --- HTTP ----------------------------------------------------------------------------------------

class Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    engine = None

    @property
    def store(self):
        return self.engine.store

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.client_ip()} {fmt % args}\n")

    def client_ip(self):
        addr = self.client_address[0] if isinstance(self.client_address, tuple) and self.client_address else "socket"
        return self.headers.get("X-Forwarded-For", addr).split(",")[-1].strip() if self.headers else addr

    def send_json(self, code, payload):
        self.send_body(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_body(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 1024 * 1024)) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}


class BootHandler(Base):
    """What the PCs reach while starting: no login, only the one-time password of their task."""

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        match = re.fullmatch(r"/boot/(grub|ipxe)/([0-9a-fA-F:]{17})", path)
        if match:
            self.send_body(200, self.boot_script(match.group(2), match.group(1)).encode(), "text/plain")
        elif path in ("/boot/vmlinuz", "/boot/initrd.img", "/stick.img"):
            self.send_file(path)
        elif path == "/live/job":
            self.live_job()
        elif path == "/live/clock":
            self.send_json(200, {"time": int(time.time()), "tz": posix_tz()})
        elif path == "/health":
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"detail": "Nicht gefunden"})

    do_HEAD = do_GET

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        body = self.read_body()
        if path == "/live/hello":
            self.send_json(200, {"token": self.store.hello(body)})
        elif path == "/live/progress":
            self.live_progress(body)
        elif path == "/live/done":
            self.live_done(body)
        else:
            self.send_json(404, {"detail": "Nicht gefunden"})

    def boot_script(self, mac, kind):
        """GRUB (UEFI) or iPXE (BIOS) script: the boot system if a task waits for this PC, else its own disk."""
        local, live = (grub_local, grub_live) if kind == "grub" else (ipxe_local, ipxe_live)
        pc = self.store.by_mac(mac)
        if not pc:
            self.store.saw_mac(mac)
            return local()
        job = self.store.job(pc["id"])
        if not job or job["state"] not in ("armed", "booting"):
            return local()
        self.store.touch(pc["id"], state="booting", mac=normal_mac(mac))
        return live(self.engine.services.server_ip(), job["token"])

    def send_file(self, path):
        if path == "/stick.img":
            try:
                file = self.engine.stick()
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"Stick-Abbild nicht erstellt: {exc!r}")
                self.send_json(500, {"detail": "Das Stick-Abbild ließ sich nicht erstellen."})
                return
        else:
            file = os.path.join(SRV, "live", os.path.basename(path))
        size = os.path.getsize(file)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        if path == "/stick.img":
            self.send_header("Content-Disposition", 'attachment; filename="home-net-pc-sicherung.img"')
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(file, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1024 * 1024)

    def token_job(self):
        token = self.headers.get("X-Token", "")
        pc_id, job = self.store.job_by_token(token)
        return token, pc_id, job

    def live_job(self):
        token, pc_id, job = self.token_job()
        if job:
            if job["state"] in ("armed", "booting"):
                self.store.touch(pc_id, state="running", started=time.time())
            self.send_json(200, self.engine.job_for_live(pc_id, job))
            return
        waiting = self.store.waiting(token)
        if waiting:
            self.send_json(200, {"action": "none" if waiting["dismissed"] else "wait"})
            return
        self.send_json(200, {"action": "none"})

    def live_progress(self, body):
        _token, pc_id, job = self.token_job()
        if not job:
            self.send_json(200, {"cancel": True})
            return
        progress = {k: body.get(k) for k in ("step", "step_now", "step_total", "percent", "bytes")}
        progress["log"] = str(body.get("log", ""))[-2000:]
        self.store.touch(pc_id, state="running", progress=progress)
        self.send_json(200, {"cancel": bool(job.get("cancel"))})

    def live_done(self, body):
        _token, pc_id, job = self.token_job()
        if not job:
            self.send_json(200, {"ok": True})
            return
        ok = bool(body.get("ok"))
        message = str(body.get("message", ""))[:500]
        result = "cancelled" if job.get("cancel") and not ok else None
        stats = body.get("stats") if isinstance(body.get("stats"), dict) else {}
        self.engine.done(pc_id, ok, message, stats, str(body.get("log", "")), result=result)
        self.send_json(200, {"ok": True})


class ApiHandler(Base):
    """Web interface and app, only through the gateway."""

    def scopes(self):
        header = self.headers.get("X-Home-Net-Categories")
        if not header:
            return ["*"]
        try:
            return json.loads(base64.b64decode(header))
        except ValueError:
            return []

    def is_manager(self):
        return self.headers.get("X-Home-Net-Role", "owner") in ("owner", "admin")

    def who(self):
        try:
            return base64.b64decode(self.headers.get("X-Home-Net-User", "")).decode() or "Besitzer"
        except ValueError:
            return ""

    def route(self):
        path = urllib.parse.urlsplit(self.path).path
        body = self.read_body() if self.command in ("POST", "PUT", "DELETE") else {}
        try:
            self.dispatch(self.command, path, body)
        except ValueError as exc:
            self.send_json(400, {"detail": str(exc)})
        except PermissionError as exc:
            self.send_json(403, {"detail": str(exc) or "Das darf nur ein Admin"})
        except LookupError:
            self.send_json(404, {"detail": "Nicht gefunden"})
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            self.send_json(502, {"detail": str(exc)})

    do_GET = do_POST = do_PUT = do_DELETE = route

    def need_manager(self):
        if not self.is_manager():
            raise PermissionError("Das darf nur ein Admin")

    def pc(self, pc_id):
        pc = self.store.find(pc_id, self.scopes())
        if not pc:
            raise LookupError
        return pc

    def dispatch(self, method, path, body):
        engine, store = self.engine, self.store
        if method == "GET" and path == "/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/":
            with open(PAGE, "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/overview":
            return self.send_json(200, engine.overview(self.scopes(), self.is_manager()))
        if path == "/api/settings":
            self.need_manager()
            if method == "PUT":
                before = dict(store.settings)
                store.set_settings(body)
                if before.get("server_ip") != store.settings.get("server_ip") or \
                        before.get("netboot") != store.settings.get("netboot"):
                    engine.services.restart_netboot()
                    engine.drop_stick()
            return self.send_json(200, dict(store.settings, netboot_state=engine.services.netboot_state()))
        if method == "POST" and path == "/api/pcs":
            self.need_manager()
            pc = store.create(body, self.scopes())
            engine.services.users_changed()
            engine.archive.ensure(pc)
            return self.send_json(201, {"id": pc["id"]})
        match = re.fullmatch(r"/api/waiting/([\w-]{12})(/assign)?", path)
        if match:
            self.need_manager()
            if method == "DELETE":
                if not store.dismiss(match.group(1)):
                    raise LookupError
                return self.send_json(200, {"ok": True})
            if method == "POST" and match.group(2):
                return self.send_json(200, engine.assign(match.group(1), body, self.scopes(), self.who()))
        match = re.fullmatch(r"/api/pcs/([0-9a-f]{12})(?:/(backup|restore|cancel|wake|config|runs|parts))?(?:/([\w-]+))?", path)
        if not match:
            return self.send_json(404, {"detail": "Nicht gefunden"})
        pc_id, action, arg = match.groups()
        pc = self.pc(pc_id)
        if method == "GET" and not action:
            return self.send_json(200, engine.detail(pc, self.is_manager()))
        if method == "GET" and action == "config":
            self.need_manager()
            return self.send_json(200, {k: v for k, v in pc.items() if not k.endswith("password")})
        if method == "PUT" and not action:
            self.need_manager()
            store.update(pc_id, body, self.scopes())
            return self.send_json(200, {"ok": True})
        if method == "DELETE" and not action:
            self.need_manager()
            if store.job(pc_id):
                raise ValueError("Bitte zuerst den laufenden Auftrag abbrechen.")
            store.delete(pc_id)
            engine.services.users_changed()
            if body.get("data"):
                shutil.rmtree(engine.archive.path(pc_id), ignore_errors=True)
            return self.send_json(200, {"ok": True})
        if method == "POST" and action == "backup":
            after = body.get("after", pc["after"])
            if after not in AFTER:
                raise ValueError("Ungültige Auswahl")
            engine.start(pc, "backup", after=after, who=self.who())
            return self.send_json(202, {"ok": True})
        if method == "POST" and action == "restore":
            self.need_manager()
            run = str(body.get("run", ""))
            runs = {r["id"]: r for r in engine.archive.runs(pc, fresh=True)}
            if run not in runs or not runs[run]["complete"]:
                raise ValueError("Diese Sicherung gibt es nicht oder sie ist unvollständig.")
            parts = [str(p) for p in body.get("parts") or [] if re.fullmatch(r"d\d+p\d+", str(p))]
            engine.start(pc, "restore", after=body.get("after", "reboot") if body.get("after") in AFTER else "reboot",
                         run=run, parts=parts, who=self.who())
            return self.send_json(202, {"ok": True})
        if method == "POST" and action == "cancel":
            job = store.job(pc_id)
            if not job:
                raise ValueError("Es läuft kein Auftrag.")
            if job["state"] == "armed" or (job["state"] == "booting" and time.time() - job["seen"] > 300):
                engine.done(pc_id, False, "Abgebrochen in Home-Net", result="cancelled")
            else:
                store.touch(pc_id, cancel=True)
            return self.send_json(202, {"ok": True})
        if method == "POST" and action == "wake":
            wake(pc["macs"])
            return self.send_json(202, {"ok": True})
        if method == "GET" and action == "parts" and arg:
            return self.send_json(200, engine.archive.parts(pc, arg))
        if method == "DELETE" and action == "runs" and arg:
            self.need_manager()
            job = store.job(pc_id)
            if job and job["run"] == arg:
                raise ValueError("Diese Sicherung läuft gerade.")
            engine.archive.tidy(pc, 10**6, also=[arg])
            return self.send_json(200, {"ok": True})
        self.send_json(405, {"detail": "Nicht erlaubt"})


def public_job(job):
    if not job:
        return None
    return {k: job.get(k) for k in ("action", "state", "run", "after", "parts", "progress", "created", "origin", "cancel")} | \
        {"run_label": run_label(job["run"])}



class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_engine():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(RUN_DIR, exist_ok=True)
    os.makedirs(BACKUPS, exist_ok=True)
    store = Store(DATA)
    return Engine(store, Archive(BACKUPS), Services(store))


def main():
    engine = make_engine()
    Base.engine = engine
    engine.services.check()
    threading.Thread(target=ticker, args=(engine,), daemon=True).start()
    boot = ThreadingHTTPServer(("0.0.0.0", BOOT_PORT), BootHandler)
    threading.Thread(target=boot.serve_forever, daemon=True).start()
    if API_PORT:
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", API_PORT), ApiHandler).serve_forever,
                         daemon=True).start()
    if LISTEN_SOCKET:
        try:
            os.unlink(LISTEN_SOCKET)
        except OSError:
            pass
        server = UnixHTTPServer(LISTEN_SOCKET, ApiHandler)
        os.chmod(LISTEN_SOCKET, 0o666)
        server.serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
