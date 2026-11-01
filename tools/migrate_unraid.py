#!/usr/bin/env python3
"""Moves the running single containers on Unraid over to the Home-Net installation. Runs on the server.

The old containers stay stopped under a -alt name until everything answers, so a failure goes straight back.
The password comes in on stdin and is only stored as a hash.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(sys.argv[1])
DATA = Path(sys.argv[2])
IMAGES = "/mnt/user/backup1/Raspis/PiWartung"
PORT = 8093
OLD = ("networkmonitor", "ntfy", "pi-wartung-service")
TEMPLATES = Path("/boot/config/plugins/dockerMan/templates-user")
AUTOSTART = Path("/var/lib/docker/unraid-autostart")
FOLDERS = Path("/boot/config/plugins/folder.view2/docker.json")

sys.path.insert(0, str(REPO / "core"))
import home_net  # noqa: E402


def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check).stdout.strip()


def inspect(name):
    return json.loads(sh("docker", "inspect", name))[0]


def own_env(info):
    image = json.loads(sh("docker", "image", "inspect", info["Config"]["Image"]))[0]
    base = set(image["Config"].get("Env") or [])
    return [e for e in info["Config"].get("Env") or [] if e not in base]


def monitor_password(env, db):
    """The web password: one set in the web interface wins over the start value, then the module's default."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("select value from meta where key = 'cfg.ui_password'").fetchone()
    finally:
        conn.close()
    if row and row[0]:
        return json.loads(row[0])
    for entry in env:
        if entry.startswith("UI_PASSWORD=") and entry != "UI_PASSWORD=":
            return entry.split("=", 1)[1]
    return "netzwerkmonitor"


def private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def copy_data():
    monitor = inspect("networkmonitor")
    ntfy = inspect("ntfy")
    monitor_env = own_env(monitor)
    src_monitor = Path("/mnt/user/appdata/networkmonitor/data")
    password = monitor_password(monitor_env, src_monitor / "networkmonitor.db")
    private(DATA / "netzwerkmonitor.env", "\n".join(monitor_env) + "\n")
    private(DATA / "ntfy.env", "\n".join(own_env(ntfy)) + "\n")
    shutil.copytree(src_monitor, DATA / "netzwerkmonitor", dirs_exist_ok=True)
    shutil.copytree("/mnt/user/appdata/ntfy", DATA / "ntfy", dirs_exist_ok=True)
    ssh = DATA / "pi-wartung" / "ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    ssh.chmod(0o700)
    for key in Path("/root/.ssh").iterdir():
        if key.name in ("master", "dynaframe") or key.name.startswith("pi-wartung"):
            # /root/.ssh may lie on the flash drive, which cannot hold these permissions
            target = ssh / key.name
            target.write_bytes(key.read_bytes())
            target.chmod(0o600)
    return password


def healthy(password):
    token = home_net.base64_basic(password)
    for path in ("/m/netzwerkmonitor/api/devices", "/m/pi-wartung/api/overview"):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"Authorization": token})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status != 200:
                    return False
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {path}: {exc}")
            return False
    return True


def tidy_unraid():
    """Removes what the single containers needed: the templates also held passwords in plain text."""
    for name in ("my-networkmonitor.xml", "my-pi-wartung-service.xml", "my-Ntfy.xml", "my-ntfy.xml.pi-wartung.bak",
                 "my-networkmonitor.xml.pi-wartung.bak", "my-pi-wartung-service.xml.pi-wartung.bak"):
        (TEMPLATES / name).unlink(missing_ok=True)
    if AUTOSTART.exists():
        lines = [line for line in AUTOSTART.read_text().splitlines() if line.split(" ")[0] not in OLD]
        AUTOSTART.write_text("\n".join(lines) + "\n")
    if FOLDERS.exists():
        folders = json.loads(FOLDERS.read_text())
        for folder in folders.values():
            if folder.get("name") == "Home-Net":
                folder["containers"] = ["home-net-gateway", "networkmonitor", "ntfy", "pi-wartung-engine", "pi-wartung-service"]
        FOLDERS.write_text(json.dumps(folders, indent=4))
    print("Unraid aufgeräumt: alte Vorlagen und Autostart-Einträge entfernt, Gruppe aktualisiert.")


def main():
    password = sys.stdin.readline().rstrip("\n")
    if len(password) < 8:
        sys.exit("Das Passwort braucht mindestens 8 Zeichen.")
    missing = [n for n in OLD if not subprocess.run(["docker", "inspect", n], capture_output=True).returncode == 0]
    if missing:
        sys.exit(f"Container fehlen: {', '.join(missing)}. Es wurde nichts geändert.")
    print("Daten kopieren")
    monitor_pw = copy_data()
    inst = home_net.Install(DATA)
    state = home_net.new_state(DATA, IMAGES, ["netzwerkmonitor", "ntfy", "pi-wartung"], PORT, password)
    state["upstream"]["netzwerkmonitor"] = monitor_pw
    inst.apply(state, REPO)
    print("Abbilder bauen, das dauert einige Minuten")
    inst.compose("build", check=True)
    print("Umschalten")
    for name in OLD:
        sh("docker", "rename", name, f"{name}-alt")
        sh("docker", "stop", "-t", "15", f"{name}-alt")
    try:
        inst.start()
        for _ in range(12):
            time.sleep(5)
            if healthy(password):
                break
        else:
            raise RuntimeError("Dienste antworten nicht")
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Fehlgeschlagen ({exc}), zurück zu den alten Containern")
        inst.compose("down", check=False)
        for name in OLD:
            sh("docker", "rename", f"{name}-alt", name)
            sh("docker", "start", name)
        sys.exit(1)
    for name in OLD:
        sh("docker", "rm", f"{name}-alt")
    home_net.POINTER.parent.mkdir(parents=True, exist_ok=True)
    home_net.POINTER.write_text(str(DATA) + "\n")
    tidy_unraid()
    print(f"Fertig. Home-Net läuft auf diesem Server unter Port {PORT}.")


if __name__ == "__main__":
    main()
