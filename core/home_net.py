#!/usr/bin/env python3
"""Installs and manages Home-Net: chooses modules, writes the compose file, starts everything.

Usage:
  home-net install [--data DIR] [--images DIR] [--pc-dir DIR] [--modules a,b] [--port N] [--password PW] [--no-start]
  home-net add <modul> [--dir DIR]   home-net remove <modul>
  home-net status            home-net modules
  home-net update            home-net password <konto> [neues Passwort]
  home-net heimnetz [netze|auto|alle]   Welche Netze nur das Passwort brauchen
  home-net apply             Erstellt alles neu aus dem vorhandenen Programmstand
  home-net backup            home-net restore <archiv>
"""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINTER = Path.home() / ".config" / "home-net" / "location"
# Where program, releases, images and app come from. A fork installs with HOME_NET_REPO=<owner>/<repo> (or --repo);
# the installation keeps it as "repo" in home-net.json, so updates and the app stay with that fork.
DEFAULT_REPO = "Archonw/Home-Net"
REPO_FORMAT = re.compile(r"[\w.-]+/[\w.-]+")


def set_repo(name):
    """Points every download at one GitHub repository."""
    global GITHUB_REPO, SOURCE, TAG_SOURCE, RELEASES, REGISTRY, RAW_ICONS
    if not REPO_FORMAT.fullmatch(name or ""):
        raise HomeNetError(f"Repository bitte als Besitzer/Name angeben, z. B. {DEFAULT_REPO}")
    GITHUB_REPO = name
    SOURCE = f"https://github.com/{name}/archive/refs/heads/main.tar.gz"
    TAG_SOURCE = f"https://github.com/{name}/archive/refs/tags/{{tag}}.tar.gz"
    RELEASES = f"https://api.github.com/repos/{name}/releases?per_page=100"
    # Finished images of every release, built by .github/workflows/release.yml
    REGISTRY = f"ghcr.io/{name.lower()}"
    RAW_ICONS = f"https://raw.githubusercontent.com/{name}/main/"


# stabil: the newest release with finished images. main: the newest state of the code, built on this computer.
CHANNELS = ("stabil", "main")
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PBKDF2_ROUNDS = 200_000
GATEWAY = "home-net-gateway"
DEFAULT_PORT = 8093
# The folders of the Unraid Docker tab (plugin folder.view2)
UNRAID_FOLDERS = Path("/boot/config/plugins/folder.view2/docker.json")
FOLDER_NAME = "Home-Net"


class HomeNetError(Exception):
    pass


set_repo(os.environ.get("HOME_NET_REPO") or DEFAULT_REPO)


def available_modules(repo=REPO):
    out = {}
    for path in sorted((repo / "modules").glob("*/module.json")):
        data = json.loads(path.read_text())
        out[data["id"]] = data
    return out


def password_record(password, rounds=PBKDF2_ROUNDS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": rounds}


def base64_basic(password):
    return "Basic " + base64.b64encode(f"app:{password}".encode()).decode()


def icon_url(icon):
    return icon if icon.startswith("http") else RAW_ICONS + icon


def absolute_build(build, repo):
    if isinstance(build, str):
        return str(repo / build)
    return dict(build, context=str(repo / build["context"]))


def image_of(service, version):
    return f"{REGISTRY}/{service}:{version}"


def compose_document(modules, chosen, repo=REPO, version=None):
    """The compose file: gateway plus every service of every chosen module.

    With a version (a release such as v1.2.0) every container comes as a finished image, otherwise it is built here."""
    services = {
        GATEWAY: {
            "build": {"context": str(repo), "dockerfile": "core/gateway/Dockerfile"},
            "container_name": GATEWAY,
            "restart": "unless-stopped",
            "ports": ["${GATEWAY_PORT}:8080"],
            "volumes": ["${DATA_DIR}/gateway:/config", "${DATA_DIR}/events:/events"],
            "labels": {"net.unraid.docker.icon": icon_url("assets/icons/home-net.png")},
            # The app download follows the repository this installation comes from
            "environment": {"HOME_NET_REPO": GITHUB_REPO},
        }
    }
    for module_id in chosen:
        module = modules[module_id]
        if module.get("api", {}).get("socket"):
            folder = str(Path(module["api"]["socket"]).parent)
            services[GATEWAY]["volumes"].append(f"${{DATA_DIR}}/{module_id}/run:{folder}")
        for name, spec in module["services"].items():
            svc = dict(spec)
            if "build" in svc:
                svc["build"] = absolute_build(svc["build"], repo)
            svc["container_name"] = name
            svc["restart"] = "unless-stopped"
            svc["labels"] = {"net.unraid.docker.icon": icon_url(module["icon"])}
            services[name] = svc
    if version:
        for name, svc in services.items():
            if svc.pop("build", None) is not None:
                svc["image"] = image_of(name, version)
    return {"name": "home-net", "services": services}


def sync_unraid_folder(containers, path=None):
    """Keeps the folder Home-Net in the Unraid Docker tab holding exactly the containers of Home-Net.

    Only an existing folder of that name is changed, the others and its settings stay as they are."""
    path = Path(path or UNRAID_FOLDERS)
    if not path.exists():
        return False
    try:
        folders = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    changed = False
    for folder in folders.values():
        if isinstance(folder, dict) and folder.get("name") == FOLDER_NAME and folder.get("containers") != containers:
            folder["containers"] = list(containers)
            changed = True
    if changed:
        fresh = path.with_name(path.name + ".neu")
        fresh.write_text(json.dumps(folders, indent=4))
        fresh.replace(path)
    return changed


def gateway_config(modules, chosen, upstream_passwords, owner="admin", lan=None):
    entries = []
    for module_id in chosen:
        module = modules[module_id]
        api = module.get("api", {})
        item = {"id": module_id, "name": module["name"], "description": module["description"],
                "icon": icon_url(module["icon"])}
        if api.get("external_port"):
            item["external_port"] = api["external_port"]
        else:
            upstream = f"unix:{api['socket']}" if api.get("socket") else f"http://{api['service']}:{api['port']}"
            if module.get("events"):
                item["events"] = module["events"]
            if api.get("tile"):
                item["tile"] = api["tile"]
            item.update(upstream=upstream, health=api.get("health", "/health"),
                        public=api.get("public", []), web=bool(api.get("web")),
                        password=upstream_passwords.get(module_id, ""))
        entries.append(item)
    return {"owner": owner, "modules": entries, "lan": list(lan or [])}


VIRTUAL_INTERFACES = ("lo", "docker", "br-", "veth", "virbr", "vnet", "shim-", "tap", "dummy")
VPN_IMAGES = ("wg-easy", "wireguard", "openvpn", "pivpn")


def host_networks(route_file="/proc/net/route"):
    """The IPv4 networks this computer is directly connected to, without Docker's and the VMs' own."""
    out = []
    try:
        lines = Path(route_file).read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[0].startswith(VIRTUAL_INTERFACES) or fields[2] != "00000000":
            continue
        dest = ipaddress.IPv4Address(bytes.fromhex(fields[1])[::-1])
        mask = ipaddress.IPv4Address(bytes.fromhex(fields[7])[::-1])
        if int(mask) == 0:
            continue
        net = ipaddress.IPv4Network(f"{dest}/{mask}", strict=False)
        if net.is_private and str(net) not in out:
            out.append(str(net))
    return out


def docker_out(*args):
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def network_info(container):
    """(subnet, gateway) of every IPv4 network the container is in."""
    out = []
    for net in docker_out("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}", container).split():
        for row in docker_out("network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}}|{{.Gateway}} {{end}}",
                              net).split():
            subnet, _, gateway = row.partition("|")
            if not subnet or ":" in subnet:
                continue
            try:
                network = ipaddress.ip_network(subnet, strict=False)
            except ValueError:
                continue
            out.append((str(network), gateway or str(next(network.hosts()))))
    return out


def vpn_networks():
    """Where users of a VPN container on this computer show up at Home-Net.

    In the container's own network they keep its address. A container in another Docker network reaches the published
    port of Home-Net through Docker, which hands the request over with the gateway address of Home-Net's network; on
    Unraid with WireGuard-Easy in the bridge network that is e.g. 172.19.0.1. Only that one address is added, so other
    containers count as home network too, but proxies and tunnels are still recognized by their headers."""
    out = []
    for row in docker_out("ps", "--format", "{{.Names}}\t{{.Image}}").splitlines():
        name, _, image = row.partition("\t")
        if name.endswith("-verwaltung") or not any(word in image.lower() or word in name.lower() for word in VPN_IMAGES):
            continue
        out += [subnet for subnet, _ in network_info(name) if subnet not in out]
    if out:
        out += [f"{gateway}/32" for _, gateway in network_info(GATEWAY) if f"{gateway}/32" not in out]
    return out


def detect_home_networks():
    return host_networks() + [n for n in vpn_networks() if n not in host_networks()]


class Install:
    """One installation: everything lives in its data folder, so it can be saved and moved."""

    def __init__(self, data):
        self.data = Path(data)
        self.state_file = self.data / "home-net.json"
        self.compose_file = self.data / "docker-compose.yml"
        self.env_file = self.data / ".env"

    @classmethod
    def locate(cls):
        if os.environ.get("HOME_NET_DATA"):
            return cls(os.environ["HOME_NET_DATA"])
        # Beside the program: survives where the home folder does not, as on Unraid after a restart
        beside = REPO.parent / "data"
        if (beside / "home-net.json").exists() and not POINTER.exists():
            return cls(beside)
        if not POINTER.exists():
            raise HomeNetError("Keine Installation gefunden. Bitte zuerst: home-net install")
        return cls(POINTER.read_text().strip())

    def load(self):
        state = json.loads(self.state_file.read_text())
        if state.get("repo"):
            set_repo(state["repo"])
        return state

    def write_private(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)

    def apply(self, state, repo=REPO):
        """Writes all generated files from the state."""
        if "lan" not in state:
            # Once, at the install or the first update after it came: which addresses count as home network.
            state["lan"] = detect_home_networks()
            if state["lan"]:
                print(f"Heimnetz: {', '.join(state['lan'])}. Ändern mit: home-net heimnetz")
        modules = available_modules(repo)
        unknown = [m for m in state["modules"] if m not in modules]
        if unknown:
            raise HomeNetError(f"Unbekannte Module: {', '.join(unknown)}")
        self.write_private(self.state_file, json.dumps(state, indent=2) + "\n")
        self.write_private(self.env_file, f"DATA_DIR={self.data}\nIMAGES_DIR={state['images']}\n"
                                          f"PC_DIR={pc_dir(state)}\n"
                                          f"GATEWAY_PORT={state['port']}\nTZ={state.get('tz') or host_timezone()}\n"
                                          f"POCKET_ID_DATA={pool_path(self.data) / 'pocket-id-server' / 'data'}\n"
                                          f"WG_EASY_DATA={pool_path(self.data) / 'wireguard-server' / 'data'}\n")
        document = compose_document(modules, state["modules"], repo, release_of(state))
        self.compose_file.write_text(json.dumps(document, indent=2) + "\n")
        if sync_unraid_folder([svc["container_name"] for svc in document["services"].values()]):
            print(f"Unraid: Gruppe {FOLDER_NAME} im Docker-Tab angepasst.")
        for module_id in state["modules"]:
            for svc in modules[module_id]["services"].values():
                for env in svc.get("env_file", []):
                    path = Path(env.replace("${DATA_DIR}", str(self.data)))
                    if not path.exists():
                        self.write_private(path, "")
        self.write_private(self.data / "gateway" / "gateway.json",
                           json.dumps(gateway_config(modules, state["modules"], state["upstream"], state.get("owner", "admin"),
                                                     state.get("lan")),
                                      indent=2) + "\n")
        self.write_private(self.data / "gateway" / "auth.json", json.dumps(state["auth"]) + "\n")
        self.move_app_settings()
        if "pi-wartung" in state["modules"]:
            (self.data / "pi-wartung" / "ssh").mkdir(parents=True, exist_ok=True, mode=0o700)
            Path(state["images"]).mkdir(parents=True, exist_ok=True)
            self.write_private(self.data / "pi-wartung" / "config" / "auth.json",
                               json.dumps(password_record(state["upstream"]["pi-wartung"])) + "\n")
        if "pc-sicherung" in state["modules"]:
            Path(pc_dir(state)).mkdir(parents=True, exist_ok=True)
            (self.data / "pc-sicherung" / "config").mkdir(parents=True, exist_ok=True, mode=0o700)
        for module_id in ("unraid", "pocket-id", "wireguard", "pihole", "proxy-manager", "nextcloud"):
            if module_id in state["modules"]:
                (self.data / module_id / "config").mkdir(parents=True, exist_ok=True, mode=0o700)
        if "pocket-id-server" in state["modules"]:
            self.pocket_id_server(state)
        if "wireguard-server" in state["modules"]:
            self.wireguard_server(state)

    def pocket_id_server(self, state):
        """Pocket ID installed by Home-Net: its keys are made once, and the module pocket-id gets the API key."""
        secrets_dir = self.data / "pocket-id-server" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, make in (("encryption-key", lambda: secrets.token_urlsafe(32)),
                           ("api-key", lambda: secrets.token_urlsafe(32))):
            if not (secrets_dir / name).exists():
                self.write_private(secrets_dir / name, make() + "\n")
        self.write_private(self.data / "pocket-id-server.env", f"APP_URL={state.get('pocket_id_url', '')}\n")
        if "pocket-id" in state["modules"]:
            key = self.data / "pocket-id" / "config" / "api-key"
            if not key.exists():
                self.write_private(key, (secrets_dir / "api-key").read_text())

    def wireguard_server(self, state):
        """WireGuard-Easy installed by Home-Net: its first account is made at the first start from the env file, and the
        module wireguard gets the same login. The password is made once."""
        secrets_dir = self.data / "wireguard-server" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.data / "wireguard-server" / "data").mkdir(parents=True, exist_ok=True)
        password_file = secrets_dir / "password"
        if not password_file.exists():
            self.write_private(password_file, secrets.token_urlsafe(24) + "\n")
        password = password_file.read_text().strip()
        self.write_private(self.data / "wireguard-server.env",
                           "INIT_ENABLED=true\nINIT_USERNAME=home-net\n"
                           f"INIT_PASSWORD={password}\nINIT_HOST={state.get('wireguard_host', '')}\nINIT_PORT=51820\n")
        if "wireguard" in state["modules"]:
            login = self.data / "wireguard" / "config" / "login.json"
            self.write_private(login, json.dumps({"url": "http://wireguard-server:51821", "username": "home-net",
                                                  "password": password}) + "\n")

    def move_app_settings(self):
        """App updates moved from the network monitor into the gateway: take over its repository, token and APK once."""
        target = self.data / "gateway" / "app.json"
        if target.exists():
            return
        found = {}
        env = self.data / "netzwerkmonitor.env"
        if env.exists():
            for line in env.read_text().splitlines():
                key, _, value = line.partition("=")
                if key in ("APP_UPDATE_REPO", "APP_UPDATE_TOKEN") and value.strip():
                    found[key[11:].lower()] = value.strip()
        db = self.data / "netzwerkmonitor" / "networkmonitor.db"
        if db.exists():
            try:
                import sqlite3
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    rows = con.execute("SELECT key, value FROM meta WHERE key IN "
                                       "('cfg.app_update_repo', 'cfg.app_update_token')").fetchall()
                finally:
                    con.close()
                for key, value in rows:
                    found[key[15:]] = json.loads(value)
            except Exception as exc:  # the update must go on; the setting can be entered again on the page
                print(f"Hinweis: App-Einstellungen des Monitors nicht übernommen ({exc}). "
                      "Repository und Token bitte auf der Home-Net-Seite unter Einstellungen eintragen.")
        apks = sorted((self.data / "netzwerkmonitor" / "app").glob("netzwerkmonitor-*.apk"), key=lambda p: p.stat().st_mtime)
        if apks and not any((self.data / "gateway" / "app").glob("*.apk")):
            (self.data / "gateway" / "app").mkdir(parents=True, exist_ok=True)
            shutil.copy2(apks[-1], self.data / "gateway" / "app" / apks[-1].name)
        if found:
            settings = {"repo": found.get("repo", GITHUB_REPO), "token": found.get("token", "")}
            self.write_private(target, json.dumps(settings) + "\n")
            print("App-Updates: Einstellungen aus dem Netzwerkmonitor übernommen.")

    def compose(self, *args, check=True):
        return subprocess.run(["docker", "compose", "--env-file", str(self.env_file), "-f", str(self.compose_file),
                               *args], check=check)

    def start(self):
        if release_of(self.load()):
            self.compose("pull", check=False)
            self.compose("up", "-d", "--remove-orphans")
        else:
            self.compose("up", "-d", "--build", "--remove-orphans")


def pool_path(path, mnt=Path("/mnt")):
    """On Unraid /mnt/user is a FUSE file system, bad for SQLite. The same folder on a pool (e.g. /mnt/cache) is used
    directly when it exists; elsewhere the path stays as it is."""
    path = Path(path)
    if path.parts[:3] != ("/", "mnt", "user"):
        return path
    rest = Path(*path.parts[3:]) if len(path.parts) > 3 else Path()
    try:
        pools = sorted((p for p in mnt.iterdir() if p.is_dir() and p.name not in ("user", "user0", "disks", "remotes",
                        "addons", "rootshare") and not p.name.startswith("disk")), key=lambda p: p.name != "cache")
    except OSError:
        return path
    for pool in pools:
        if (pool / rest).is_dir():
            return pool / rest
    return path


def pc_dir(state):
    """Where the PC backups go: chosen at installation, otherwise beside the Pi images."""
    return state.get("pc_dir") or str(Path(state["images"]).parent / "PC-Sicherung")


def host_timezone(etc=Path("/etc"), unraid_ident=Path("/boot/config/ident.cfg")):
    """The time zone of this computer, e.g. Europe/Berlin; UTC if none can be found."""
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"]
    try:
        name = (etc / "timezone").read_text().strip()
        if name:
            return name
    except OSError:
        pass
    link = etc / "localtime"
    if link.is_symlink() and "zoneinfo/" in os.readlink(link):
        return os.readlink(link).split("zoneinfo/", 1)[1]
    try:
        for line in unraid_ident.read_text().splitlines():
            if line.startswith("timeZone="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "UTC"


def new_state(data, images, chosen, port, password, tz=None, owner="admin"):
    upstream = {m: secrets.token_urlsafe(24) for m in chosen if m != "ntfy"}
    return {"modules": list(chosen), "images": str(images or Path(data) / "pi-wartung" / "images"), "port": port,
            "tz": tz or host_timezone(), "owner": owner, "auth": password_record(password), "upstream": upstream}


def machine():
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())


def usable(module):
    """Some modules only work on a certain system, e.g. unraid only directly on an Unraid server."""
    need = module.get("requires")
    if not need:
        return True
    if need.get("path") and not Path(need["path"]).exists():
        return False
    return not need.get("arch") or machine() in need["arch"]


def release_of(state):
    """The release whose finished images this installation runs, or None when it builds them itself."""
    return state.get("version") if state.get("channel", "main") == "stabil" else None


def version_key(tag):
    match = VERSION_TAG.match(tag or "")
    return tuple(int(n) for n in match.groups()) if match else None


def fetch_text(url):
    if has_https():
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read().decode()
    if not shutil.which("curl"):
        raise HomeNetError("Python kann kein HTTPS und curl fehlt")
    out = subprocess.run(["curl", "-fsSL", "--max-time", "60", url], capture_output=True, text=True)
    if out.returncode != 0:
        raise HomeNetError(f"{url} nicht erreichbar (curl {out.returncode})")
    return out.stdout


def newest_release(releases):
    """The highest vX.Y.Z among published releases; the app's own releases (app-NNN) do not count."""
    tags = [r["tag_name"] for r in releases
            if not r.get("draft") and not r.get("prerelease") and version_key(r.get("tag_name"))]
    return max(tags, key=version_key) if tags else None


def latest_release():
    try:
        tag = newest_release(json.loads(fetch_text(RELEASES)))
    except (OSError, ValueError) as exc:
        raise HomeNetError(f"Liste der Versionen nicht geladen: {exc}") from exc
    if not tag:
        raise HomeNetError("Es gibt noch keine veröffentlichte Version. Mit home-net kanal main gibt es den neuesten Stand.")
    return tag


def check_usable(modules, chosen):
    for module_id in chosen:
        if module_id in modules and not usable(modules[module_id]):
            raise HomeNetError(modules[module_id]["requires"].get("message", f"{module_id} passt nicht zu diesem Rechner"))


def ask_modules(modules):
    print("Verfügbare Module:")
    ids = [m for m in modules if usable(modules[m])]
    for i, module_id in enumerate(ids, 1):
        print(f"  {i}. {modules[module_id]['name']}: {modules[module_id]['description']}")
    answer = input("Welche installieren? Nummern mit Komma, leer für alle: ").strip()
    if not answer:
        return ids
    try:
        return [ids[int(part) - 1] for part in answer.split(",")]
    except (ValueError, IndexError):
        raise HomeNetError("Ungültige Auswahl")


def ask_yes(question, default):
    answer = input(question).strip().lower()
    return default if not answer else answer in ("j", "ja", "y", "yes")


def ask_pocket_id_url(given=None):
    """Passkeys need the address under which people open Pocket ID, with https (via the reverse proxy)."""
    url = (given or input("Adresse, unter der Pocket ID erreichbar sein wird (z. B. https://id.example.org): ")).strip()
    if not url.startswith(("https://", "http://localhost")):
        raise HomeNetError("Pocket ID braucht eine https-Adresse, sonst funktionieren Passkeys nicht")
    return url.rstrip("/")


def ask_wireguard_host(given=None):
    """The address phones use from outside: the public name (DynDNS) or IP of the internet connection."""
    host = (given or input("Adresse, unter der dein Heimnetz von außen erreichbar ist (z. B. vpn.example.org): ")).strip()
    host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
    if not host or any(c in host for c in " /:@"):
        raise HomeNetError("Bitte nur den Namen oder die IP-Adresse angeben, z. B. vpn.example.org")
    return host


def ask_password():
    while True:
        first = getpass.getpass("Passwort für Home-Net: ")
        if len(first) < 8:
            print("Bitte mindestens 8 Zeichen.")
            continue
        if first == getpass.getpass("Nochmal: "):
            return first
        print("Die Eingaben stimmen nicht überein.")


def clone_repo(folder=REPO):
    """owner/name of the GitHub repository a git clone of Home-Net came from, so a fork keeps to itself."""
    if not (folder / ".git").exists():
        return None
    url = subprocess.run(["git", "-C", str(folder), "remote", "get-url", "origin"], capture_output=True, text=True).stdout
    match = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


def cmd_install(args):
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0 and not args.no_start:
        raise HomeNetError("Docker mit Compose fehlt auf diesem Rechner")
    modules = available_modules()
    data = Path(args.data or input(f"Datenordner [{Path.home() / 'home-net'}]: ").strip() or Path.home() / "home-net")
    chosen = args.modules.split(",") if args.modules else ask_modules(modules)
    check_usable(modules, chosen)
    owner = args.owner or input("Dein Benutzername [admin]: ").strip() or "admin"
    password = args.password or ask_password()
    inst = Install(data)
    state = new_state(data, args.images, chosen, args.port, password, owner=owner)
    if args.pc_dir:
        state["pc_dir"] = args.pc_dir
    if "pocket-id-server" in chosen:
        state["pocket_id_url"] = ask_pocket_id_url(args.pocket_id_url)
    if "wireguard-server" in chosen:
        state["wireguard_host"] = ask_wireguard_host(args.wireguard_host)
    repo = args.repo or os.environ.get("HOME_NET_REPO") or clone_repo()
    if repo:
        set_repo(repo)
    state["repo"] = GITHUB_REPO
    state["channel"] = args.channel
    if args.channel == "stabil":
        try:
            state["version"] = latest_release()
        except HomeNetError as exc:
            print(f"{exc}\nHome-Net nimmt darum den neuesten Stand (Kanal main).")
            state["channel"] = "main"
    if state["channel"] == "main":
        state["version"] = "main"
    inst.apply(state)
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(data) + "\n")
    if release_of(state) and not args.no_start:
        # Program and images of the same release: fetch the program of that version, it starts everything
        print(f"Lade Home-Net {state['version']} ...")
        cmd_update(argparse.Namespace(installed=False, version=state["version"], force=True))
        return
    if not args.no_start:
        inst.start()
    print(f"Fertig. Home-Net läuft unter http://<dieser-rechner>:{args.port}")


def cmd_add(args):
    inst = Install.locate()
    state = inst.load()
    if args.module in state["modules"]:
        raise HomeNetError(f"{args.module} ist schon installiert")
    if args.module not in available_modules():
        raise HomeNetError(f"Unbekanntes Modul: {args.module}")
    check_usable(available_modules(), [args.module])
    state["modules"].append(args.module)
    if args.module != "ntfy":
        state["upstream"][args.module] = secrets.token_urlsafe(24)
    if args.module == "pc-sicherung":
        state["pc_dir"] = args.dir or input(f"Ordner für die PC-Sicherungen [{pc_dir(state)}]: ").strip() or pc_dir(state)
    if args.module == "pocket-id" and "pocket-id-server" not in state["modules"]:
        if args.with_server or (not args.existing and ask_yes(
                "Läuft schon ein Pocket ID? Mit Nein installiert Home-Net Pocket ID gleich mit. [J/n]: ", True) is False):
            state["modules"].append("pocket-id-server")
    if "pocket-id-server" in state["modules"] and not state.get("pocket_id_url"):
        state["pocket_id_url"] = ask_pocket_id_url(args.url)
    if args.module == "wireguard" and "wireguard-server" not in state["modules"]:
        if args.with_server or (not args.existing and ask_yes(
                "Läuft schon WireGuard-Easy ab Version 15? Mit Nein installiert Home-Net es gleich mit. [J/n]: ", True) is False):
            state["modules"].append("wireguard-server")
    if "wireguard-server" in state["modules"] and not state.get("wireguard_host"):
        state["wireguard_host"] = ask_wireguard_host(args.host)
    inst.apply(state)
    inst.start()
    if args.module in ("pocket-id", "pocket-id-server") and "pocket-id-server" in state["modules"]:
        print(f"Pocket ID läuft auf Port 1411. Über deinen Reverse-Proxy unter {state['pocket_id_url']} erreichbar machen "
              f"und dort unter {state['pocket_id_url']}/setup den ersten Admin mit Passkey anlegen.")
    if args.module == "proxy-manager":
        print("Im Proxy Manager unter Benutzer einen eigenen Benutzer für Home-Net anlegen (Rechte: alle Einträge, nur "
              "ansehen) und E-Mail und Passwort in Home-Net beim Modul Proxy Manager unter Einstellungen eintragen.")
    if args.module in ("wireguard", "wireguard-server") and "wireguard-server" in state["modules"]:
        print("WireGuard-Easy läuft. Im Router den Port 51820 (UDP) an diesen Rechner weiterleiten, sonst kommen die "
              "Handys von außen nicht hinein. Die Zugänge legst du in der App von Home-Net an.")
    elif args.module == "wireguard":
        print("In Home-Net im Browser beim Modul WireGuard unter Einstellungen die Adresse und die Anmeldung von "
              "WireGuard-Easy eintragen.")
    if args.module == "pihole":
        print("Pi-hole: In Home-Net im Browser beim Modul Pi-hole unter Einstellungen die Adressen und Passwörter "
              "deiner Pi-holes eintragen (Pi-hole 6 nötig).")


def cmd_remove(args):
    inst = Install.locate()
    state = inst.load()
    if args.module not in state["modules"]:
        raise HomeNetError(f"{args.module} ist nicht installiert")
    state["modules"].remove(args.module)
    inst.apply(state)
    inst.start()
    print("Das Modul ist gestoppt, seine Daten bleiben im Datenordner.")


def cmd_status(_args):
    inst = Install.locate()
    state = inst.load()
    print(f"Datenordner: {inst.data}\nPort: {state['port']}\nModule: {', '.join(state['modules'])}")
    inst.compose("ps", check=False)


def cmd_modules(_args):
    for module_id, module in available_modules().items():
        print(f"{module_id}: {module['description']}")


def has_https():
    try:
        import ssl  # noqa: F401
    except ImportError:
        return False
    return True


def extract_release(stream, fresh):
    with tarfile.open(fileobj=stream, mode="r|gz") as tar:
        for member in tar:
            # GitHub puts everything below one folder named after the repository and branch
            parts = Path(member.name).parts[1:]
            if parts:
                member.name = str(Path(*parts))
                tar.extract(member, fresh, filter="data")


def download_release(source, fresh):
    """Python on Unraid is built without SSL; curl, which every Unraid has, then does the download."""
    if has_https():
        with urllib.request.urlopen(source, timeout=120) as resp:
            extract_release(resp, fresh)
        return
    if not shutil.which("curl"):
        raise HomeNetError("Python kann kein HTTPS und curl fehlt")
    curl = subprocess.Popen(["curl", "-fsSL", "--max-time", "600", source], stdout=subprocess.PIPE)
    try:
        extract_release(curl.stdout, fresh)
    finally:
        curl.stdout.close()
        code = curl.wait()
    if code != 0:
        raise OSError(f"curl Fehlercode {code}")


def replace_program(repo, source=SOURCE):
    """Swaps the program folder for the published version; only a complete download touches the old one."""
    fresh, old = repo.with_name(repo.name + ".neu"), repo.with_name(repo.name + ".alt")
    for folder in (fresh, old):
        shutil.rmtree(folder, ignore_errors=True)
    fresh.mkdir()
    try:
        download_release(source, fresh)
        if not (fresh / "home-net").exists():
            raise HomeNetError("Der Download enthält kein Home-Net")
        repo.rename(old)
        fresh.rename(repo)
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(fresh, ignore_errors=True)
        raise HomeNetError(f"Neue Version nicht geladen, die alte bleibt: {exc}") from exc
    shutil.rmtree(old, ignore_errors=True)


def cmd_update(args):
    inst = Install.locate()
    state = inst.load()
    if not args.installed:
        channel = state.get("channel", "main")
        if args.version:
            if not version_key(args.version):
                raise HomeNetError("Version bitte als vX.Y.Z angeben, z. B. v1.2.0")
            target = args.version
            channel = "stabil"
        elif channel == "stabil":
            target = latest_release()
            if target == state.get("version") and not args.force:
                print(f"Home-Net {target} ist aktuell.")
                return
        else:
            target = "main"
        if (REPO / ".git").exists():
            git = ["git", "-C", str(REPO)]
            if target == "main":
                subprocess.run([*git, "checkout", "-q", "main"], check=True)
                subprocess.run([*git, "pull", "--ff-only"], check=True)
            else:
                subprocess.run([*git, "fetch", "-q", "origin", f"refs/tags/{target}:refs/tags/{target}"], check=True)
                subprocess.run([*git, "checkout", "-q", target], check=True)
        else:
            source = SOURCE if target == "main" else TAG_SOURCE.format(tag=target)
            replace_program(REPO, source)
        print(f"Home-Net {target} geladen.")
        # The rest belongs to the new version
        os.chdir(REPO.parent)
        os.execv(sys.executable, [sys.executable, str(REPO / "home-net"), "update", "--installed",
                                  "--target", target, "--channel", channel])
    if args.target:
        state["channel"] = args.channel or state.get("channel", "main")
        state["version"] = args.target
    inst.apply(state)
    if not release_of(state):
        inst.compose("pull", "--ignore-buildable", check=False)
    inst.start()


def cmd_version(_args):
    state = Install.locate().load()
    channel = state.get("channel", "main")
    print(f"Version: {state.get('version') or 'unbekannt'}   Kanal: {channel}")
    if channel == "stabil":
        try:
            newest = latest_release()
            print("Aktuell." if newest == state.get("version") else f"Neu verfügbar: {newest} (home-net update)")
        except HomeNetError as exc:
            print(exc)


def cmd_kanal(args):
    inst = Install.locate()
    state = inst.load()
    if not args.channel:
        print(f"Kanal: {state.get('channel', 'main')}")
        print("stabil = geprüfte Versionen mit fertigen Containern, main = neuester Stand, wird hier gebaut")
        return
    state["channel"] = args.channel
    inst.write_private(inst.state_file, json.dumps(state, indent=2) + "\n")
    print(f"Kanal {args.channel}. Wirksam mit: home-net update --force")


def cmd_apply(_args):
    inst = Install.locate()
    inst.apply(inst.load())
    inst.start()


def cmd_heimnetz(args):
    """Shows or sets the networks whose devices log in with the password alone."""
    inst = Install.locate()
    state = inst.load()
    if args.networks:
        if args.networks == ["auto"]:
            state["lan"] = detect_home_networks()
        elif args.networks == ["alle"]:
            state["lan"] = []
        else:
            try:
                state["lan"] = [str(ipaddress.ip_network(n.strip(), strict=False))
                                for part in args.networks for n in part.split(",") if n.strip()]
            except ValueError as exc:
                raise HomeNetError(f"Kein gültiges Netz: {exc}")
        inst.apply(state)
    current = state.get("lan") or []
    if current:
        print("Als Heimnetz gelten (Anmeldung nur mit Passwort):")
        for net in current:
            print(f"  {net}")
    else:
        print("Als Heimnetz gilt jede private Adresse (10.x, 172.16-31.x, 192.168.x).")
    print("Alles andere und alles über einen Proxy oder Tunnel braucht zusätzlich einen Passkey.")
    if not args.networks:
        print("Ändern: home-net heimnetz <netz,netz>  |  home-net heimnetz auto  |  home-net heimnetz alle")


def cmd_password(args):
    """Sets an account's password; the way back in if it is lost."""
    sys.path.insert(0, str(REPO / "core" / "gateway"))
    from security import Security

    inst = Install.locate()
    store = Security(str(inst.data / "gateway"))
    user = store.user_by_name(args.user)
    if not user:
        raise HomeNetError(f"Das Konto {args.user} gibt es nicht")
    owner = {"role": "owner"}
    store.update_user(owner, user["id"], {"password": args.password or ask_password()})
    print("Passwort geändert. Läuft der Zugang, wird das Konto beim nächsten Neustart überall abgemeldet.")


def cmd_backup(_args):
    inst = Install.locate()
    state = inst.load()
    target = inst.data.parent / f"home-net-sicherung-{time.strftime('%Y-%m-%d_%H%M')}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for path in inst.data.iterdir():
            if path.name not in ("pi-wartung", "pc-sicherung"):
                tar.add(path, arcname=path.name)
        pc = inst.data / "pc-sicherung" / "config"
        for name in ("pcs.json", "state.json"):
            if (pc / name).exists():
                tar.add(pc / name, arcname=f"pc-sicherung/config/{name}")
        pi = inst.data / "pi-wartung"
        for sub in ("ssh", "config"):
            if (pi / sub).exists():
                tar.add(pi / sub, arcname=f"pi-wartung/{sub}")
    target.chmod(0o600)
    print(f"Gesichert: {target}\nDie Images unter {state['images']} und die PC-Sicherungen sind nicht enthalten.")


def cmd_restore(args):
    data = Path(args.data or input(f"Datenordner [{Path.home() / 'home-net'}]: ").strip() or Path.home() / "home-net")
    data.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.archive, "r:gz") as tar:
        tar.extractall(data, filter="data")
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(data) + "\n")
    inst = Install(data)
    inst.apply(inst.load())
    inst.start()
    print("Wiederhergestellt.")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="home-net", description="Home-Net-Baukasten")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("--data")
    p.add_argument("--images")
    p.add_argument("--pc-dir")
    p.add_argument("--modules")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--owner")
    p.add_argument("--password", default=os.environ.get("HOME_NET_PASSWORD"))
    p.add_argument("--no-start", action="store_true")
    p.add_argument("--channel", choices=CHANNELS, default="stabil",
                   help="stabil: neueste geprüfte Version mit fertigen Containern (Standard), main: neuester Stand")
    p.add_argument("--repo", help=f"GitHub-Repository für Programm, Container und App (Standard {DEFAULT_REPO}, "
                                  "für einen eigenen Fork)")
    p.add_argument("--pocket-id-url", help="Adresse von Pocket ID, wenn pocket-id-server mitinstalliert wird")
    p.add_argument("--wireguard-host", help="Adresse des Heimnetzes von außen, wenn wireguard-server mitinstalliert wird")
    p.set_defaults(func=cmd_install)
    for name, func in (("add", cmd_add), ("remove", cmd_remove)):
        p = sub.add_parser(name)
        p.add_argument("module")
        if name == "add":
            p.add_argument("--dir", help="Ordner für die Daten des Moduls, bei pc-sicherung die Sicherungen")
            p.add_argument("--url", help="bei pocket-id-server: Adresse, unter der Pocket ID erreichbar ist")
            p.add_argument("--host", help="bei wireguard-server: Adresse des Heimnetzes von außen (DynDNS oder IP)")
            server = p.add_mutually_exclusive_group()
            server.add_argument("--with-server", action="store_true",
                                help="bei pocket-id und wireguard: den Server mitinstallieren")
            server.add_argument("--existing", action="store_true",
                                help="bei pocket-id und wireguard: den vorhandenen Server nutzen")
        p.set_defaults(func=func)
    for name, func in (("status", cmd_status), ("modules", cmd_modules), ("apply", cmd_apply), ("backup", cmd_backup)):
        sub.add_parser(name).set_defaults(func=func)
    p = sub.add_parser("update")
    p.add_argument("--version", help="eine bestimmte Version, z. B. v1.2.0, auch eine ältere zum Zurückgehen")
    p.add_argument("--force", action="store_true", help="auch neu laden, wenn die Version schon läuft")
    p.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--target", help=argparse.SUPPRESS)
    p.add_argument("--channel", choices=CHANNELS, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_update)
    p = sub.add_parser("heimnetz", help="Zeigt oder setzt, welche Netze als Heimnetz gelten")
    p.add_argument("networks", nargs="*", help="z. B. 192.168.178.0/24, oder auto, oder alle")
    p.set_defaults(func=cmd_heimnetz)
    sub.add_parser("version", help="Zeigt Version und Kanal").set_defaults(func=cmd_version)
    p = sub.add_parser("kanal", help="stabil (geprüfte Versionen) oder main (neuester Stand)")
    p.add_argument("channel", nargs="?", choices=CHANNELS)
    p.set_defaults(func=cmd_kanal)
    p = sub.add_parser("password")
    p.add_argument("user")
    p.add_argument("password", nargs="?")
    p.set_defaults(func=cmd_password)
    p = sub.add_parser("restore")
    p.add_argument("archive")
    p.add_argument("--data")
    p.set_defaults(func=cmd_restore)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except HomeNetError as exc:
        sys.exit(f"Fehler: {exc}")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Fehler beim Aufruf von {shlex.join(map(str, exc.cmd))}")


if __name__ == "__main__":
    main()
