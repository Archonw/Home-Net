"""Updates Pi-hole on its machine over SSH, because Pi-hole's own API cannot do that.

Home-Net has its own key for this. On each Pi-hole it is entered with a fixed command, so the key can start the update
of Pi-hole and nothing else (see key_line). Pi-holes are updated one after the other, so the others keep answering.
"""
import os
import re
import subprocess
import threading
import time
import urllib.parse

UPDATE_TIMEOUT = 20 * 60
LOG_LINES = 60
# The only thing the key may do on the Pi-hole: "check" looks for updates, anything else runs the update.
FORCED = ('command="case \\"$SSH_ORIGINAL_COMMAND\\" in check) pihole -up --check-only;; *) pihole -up;; esac",'
          'no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding')
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


class Updater:
    def __init__(self, folder, run=subprocess.run):
        self.folder = os.path.join(folder, "ssh")
        self.key = os.path.join(self.folder, "id_ed25519")
        self.known = os.path.join(self.folder, "known_hosts")
        self.run = run
        self.lock = threading.Lock()
        self.jobs = {}  # url -> {"state": running|ok|failed, "started", "finished", "log": [...], "name"}

    # --- key -------------------------------------------------------------------------------------

    def ensure_key(self):
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        if not os.path.exists(self.key):
            self.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "home-net-pihole", "-f", self.key],
                     check=True, capture_output=True, timeout=30)
        return self.public()

    def public(self):
        try:
            with open(self.key + ".pub") as f:
                return f.read().strip()
        except OSError:
            return ""

    def key_line(self):
        pub = self.public()
        return f"{FORCED} {pub}" if pub else ""

    # --- running ---------------------------------------------------------------------------------

    def target(self, instance):
        host = urllib.parse.urlsplit(instance["url"]).hostname or ""
        return f"{instance.get('ssh_user') or 'root'}@{host}"

    def ssh(self, instance, what, timeout):
        cmd = ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={self.known}",
               "-o", "IdentitiesOnly=yes", "-p", str(int(instance.get("ssh_port") or 22)), self.target(instance), what]
        return self.run(cmd, capture_output=True, text=True, timeout=timeout)

    def status(self, url):
        with self.lock:
            job = self.jobs.get(url)
            return dict(job, log=list(job["log"])) if job else None

    def running(self):
        with self.lock:
            return any(j["state"] in ("waiting", "running") for j in self.jobs.values())

    def start(self, instances, on_done=None):
        """Queues the update of the given Pi-holes and works through them one by one in the background."""
        with self.lock:
            if any(j["state"] in ("waiting", "running") for j in self.jobs.values()):
                raise RuntimeError("Es läuft schon ein Update. Bitte warten, bis es fertig ist.")
            for inst in instances:
                self.jobs[inst["url"]] = {"name": inst["name"], "state": "waiting", "started": 0, "finished": 0,
                                          "log": []}
        threading.Thread(target=self.work, args=(instances, on_done), daemon=True).start()

    def work(self, instances, on_done):
        for inst in instances:
            job = self.jobs[inst["url"]]
            with self.lock:
                job.update(state="running", started=int(time.time()))
            try:
                out = self.ssh(inst, "update", UPDATE_TIMEOUT)
                text = (out.stdout or "") + (out.stderr or "")
                ok = out.returncode == 0
            except subprocess.TimeoutExpired:
                text, ok = "Das Update hat länger als 20 Minuten gedauert und wurde abgebrochen.", False
            except OSError as exc:
                text, ok = f"SSH ließ sich nicht starten: {exc}", False
            lines = [ln for ln in (ANSI.sub("", line).strip() for line in text.splitlines()) if ln]
            if not ok and any("Permission denied" in ln for ln in lines):
                lines.append("Der Schlüssel von Home-Net ist auf diesem Pi-hole noch nicht eingetragen.")
            with self.lock:
                job.update(state="ok" if ok else "failed", finished=int(time.time()), log=lines[-LOG_LINES:])
            if on_done:
                on_done(inst, ok, lines)
