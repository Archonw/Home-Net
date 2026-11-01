#!/usr/bin/env python3
"""Installs and manages Home-Net: chooses modules, writes the compose file, starts everything.

Usage:
  home-net install [--data DIR] [--images DIR] [--modules a,b] [--port N] [--password PW] [--no-start]
  home-net add <modul>       home-net remove <modul>
  home-net status            home-net modules
  home-net update            home-net password [neues Passwort]
  home-net backup            home-net restore <archiv>
"""
import argparse
import getpass
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINTER = Path.home() / ".config" / "home-net" / "location"
RAW_ICONS = "https://raw.githubusercontent.com/Archonw/Home-Net/main/"
PBKDF2_ROUNDS = 200_000
GATEWAY = "home-net-gateway"
DEFAULT_PORT = 8093


class HomeNetError(Exception):
    pass


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


def icon_url(icon):
    return icon if icon.startswith("http") else RAW_ICONS + icon


def absolute_build(build, repo):
    if isinstance(build, str):
        return str(repo / build)
    return dict(build, context=str(repo / build["context"]))


def compose_document(modules, chosen, repo=REPO):
    """The compose file: gateway plus every service of every chosen module."""
    services = {
        GATEWAY: {
            "build": {"context": str(repo), "dockerfile": "core/gateway/Dockerfile"},
            "container_name": GATEWAY,
            "restart": "unless-stopped",
            "ports": ["${GATEWAY_PORT}:8080"],
            "volumes": ["${DATA_DIR}/gateway:/config"],
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "labels": {"net.unraid.docker.icon": icon_url("assets/icons/home-net.png")},
        }
    }
    for module_id in chosen:
        module = modules[module_id]
        for name, spec in module["services"].items():
            svc = dict(spec)
            if "build" in svc:
                svc["build"] = absolute_build(svc["build"], repo)
            svc["container_name"] = name
            svc["restart"] = "unless-stopped"
            svc["labels"] = {"net.unraid.docker.icon": icon_url(module["icon"])}
            services[name] = svc
    return {"name": "home-net", "services": services}


def gateway_config(modules, chosen, upstream_passwords):
    entries = []
    for module_id in chosen:
        module = modules[module_id]
        api = module.get("api", {})
        item = {"id": module_id, "name": module["name"], "description": module["description"],
                "icon": icon_url(module["icon"])}
        if api.get("external_port"):
            item["external_port"] = api["external_port"]
        else:
            host = "host.docker.internal" if api.get("host_network") else api["service"]
            item.update(upstream=f"http://{host}:{api['port']}", health=api.get("health", "/health"),
                        public=api.get("public", []), web=bool(api.get("web")),
                        password=upstream_passwords.get(module_id, ""))
        entries.append(item)
    return {"modules": entries}


class Install:
    """One installation: everything lives in its data folder, so it can be saved and moved."""

    def __init__(self, data):
        self.data = Path(data)
        self.state_file = self.data / "home-net.json"
        self.compose_file = self.data / "docker-compose.yml"
        self.env_file = self.data / ".env"

    @classmethod
    def locate(cls):
        if not POINTER.exists():
            raise HomeNetError("Keine Installation gefunden. Bitte zuerst: home-net install")
        return cls(POINTER.read_text().strip())

    def load(self):
        return json.loads(self.state_file.read_text())

    def write_private(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)

    def apply(self, state, repo=REPO):
        """Writes all generated files from the state."""
        modules = available_modules(repo)
        unknown = [m for m in state["modules"] if m not in modules]
        if unknown:
            raise HomeNetError(f"Unbekannte Module: {', '.join(unknown)}")
        self.write_private(self.state_file, json.dumps(state, indent=2) + "\n")
        self.write_private(self.env_file, f"DATA_DIR={self.data}\nIMAGES_DIR={state['images']}\n"
                                          f"GATEWAY_PORT={state['port']}\nTZ={state.get('tz', 'Europe/Berlin')}\n")
        self.compose_file.write_text(json.dumps(compose_document(modules, state["modules"], repo), indent=2) + "\n")
        for module_id in state["modules"]:
            for svc in modules[module_id]["services"].values():
                for env in svc.get("env_file", []):
                    path = Path(env.replace("${DATA_DIR}", str(self.data)))
                    if not path.exists():
                        self.write_private(path, "")
        self.write_private(self.data / "gateway" / "gateway.json",
                           json.dumps(gateway_config(modules, state["modules"], state["upstream"]), indent=2) + "\n")
        self.write_private(self.data / "gateway" / "auth.json", json.dumps(state["auth"]) + "\n")
        if "pi-wartung" in state["modules"]:
            (self.data / "pi-wartung" / "ssh").mkdir(parents=True, exist_ok=True, mode=0o700)
            Path(state["images"]).mkdir(parents=True, exist_ok=True)
            self.write_private(self.data / "pi-wartung" / "config" / "auth.json",
                               json.dumps(password_record(state["upstream"]["pi-wartung"])) + "\n")

    def compose(self, *args, check=True):
        return subprocess.run(["docker", "compose", "--env-file", str(self.env_file), "-f", str(self.compose_file),
                               *args], check=check)

    def start(self):
        self.compose("up", "-d", "--build", "--remove-orphans")


def new_state(data, images, chosen, port, password, tz="Europe/Berlin"):
    upstream = {m: secrets.token_urlsafe(24) for m in chosen if m != "ntfy"}
    return {"modules": list(chosen), "images": str(images or Path(data) / "pi-wartung" / "images"), "port": port,
            "tz": tz, "auth": password_record(password), "upstream": upstream}


def ask_modules(modules):
    print("Verfügbare Module:")
    ids = list(modules)
    for i, module_id in enumerate(ids, 1):
        print(f"  {i}. {modules[module_id]['name']}: {modules[module_id]['description']}")
    answer = input("Welche installieren? Nummern mit Komma, leer für alle: ").strip()
    if not answer:
        return ids
    try:
        return [ids[int(part) - 1] for part in answer.split(",")]
    except (ValueError, IndexError):
        raise HomeNetError("Ungültige Auswahl")


def ask_password():
    while True:
        first = getpass.getpass("Passwort für Home-Net: ")
        if len(first) < 8:
            print("Bitte mindestens 8 Zeichen.")
            continue
        if first == getpass.getpass("Nochmal: "):
            return first
        print("Die Eingaben stimmen nicht überein.")


def cmd_install(args):
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0 and not args.no_start:
        raise HomeNetError("Docker mit Compose fehlt auf diesem Rechner")
    modules = available_modules()
    data = Path(args.data or input(f"Datenordner [{Path.home() / 'home-net'}]: ").strip() or Path.home() / "home-net")
    chosen = args.modules.split(",") if args.modules else ask_modules(modules)
    password = args.password or ask_password()
    inst = Install(data)
    state = new_state(data, args.images, chosen, args.port, password)
    inst.apply(state)
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(data) + "\n")
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
    state["modules"].append(args.module)
    if args.module != "ntfy":
        state["upstream"][args.module] = secrets.token_urlsafe(24)
    inst.apply(state)
    inst.start()


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


def cmd_update(_args):
    subprocess.run(["git", "-C", str(REPO), "pull", "--ff-only"], check=True)
    inst = Install.locate()
    inst.apply(inst.load())
    inst.compose("pull", "--ignore-buildable", check=False)
    inst.start()


def cmd_password(args):
    inst = Install.locate()
    state = inst.load()
    state["auth"] = password_record(args.password or ask_password())
    inst.apply(state)
    print("Passwort geändert.")


def cmd_backup(_args):
    inst = Install.locate()
    state = inst.load()
    target = inst.data.parent / f"home-net-sicherung-{time.strftime('%Y-%m-%d_%H%M')}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for path in inst.data.iterdir():
            if path.name != "pi-wartung":
                tar.add(path, arcname=path.name)
        pi = inst.data / "pi-wartung"
        for sub in ("ssh", "config"):
            if (pi / sub).exists():
                tar.add(pi / sub, arcname=f"pi-wartung/{sub}")
    target.chmod(0o600)
    print(f"Gesichert: {target}\nDie Images unter {state['images']} sind nicht enthalten.")


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
    p.add_argument("--modules")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--password", default=os.environ.get("HOME_NET_PASSWORD"))
    p.add_argument("--no-start", action="store_true")
    p.set_defaults(func=cmd_install)
    for name, func in (("add", cmd_add), ("remove", cmd_remove)):
        p = sub.add_parser(name)
        p.add_argument("module")
        p.set_defaults(func=func)
    for name, func in (("status", cmd_status), ("modules", cmd_modules), ("update", cmd_update), ("backup", cmd_backup)):
        sub.add_parser(name).set_defaults(func=func)
    p = sub.add_parser("password")
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
