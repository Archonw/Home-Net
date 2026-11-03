#!/usr/bin/env python3
"""Takes the screenshots for the README: the app in an emulator against the real gateway with made-up data.

Started by the CI workflow Screenshots. Pocket ID, WireGuard, Pi-hole and PC-Sicherung run as their real services (with
stand-ins for Pocket ID, WireGuard-Easy, the Pi-holes and the backup archive); the other modules answer with fixed example data. Nothing personal appears.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app_probe as probe  # noqa: E402
from app_probe import REPO, adb, find, fill, tap  # noqa: E402

OUT = Path("docs/screenshots")
NOW = int(time.time())
GB = 1024 ** 3
TB = 1000 ** 4


def iso(minutes_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - minutes_ago * 60))


DEVICES = [
    {"mac": "52:54:00:00:00:01", "name": "Fritzbox", "hostname": "fritz.box", "ip": "192.168.1.1", "interface": "lan",
     "status": "allowed", "online": True, "first_seen": iso(90000), "last_seen": iso(0), "watched": True},
    {"mac": "52:54:00:00:00:02", "name": "Server", "hostname": "tower", "ip": "192.168.1.10", "interface": "lan",
     "status": "allowed", "online": True, "first_seen": iso(90000), "last_seen": iso(0), "watched": True},
    {"mac": "52:54:00:00:00:03", "name": "Handy Anna", "hostname": "pixel", "ip": "192.168.1.31", "interface": "wlan",
     "status": "allowed", "online": True, "first_seen": iso(40000), "last_seen": iso(1)},
    {"mac": "52:54:00:00:00:04", "name": "Laptop Ben", "hostname": "ben-laptop", "ip": "192.168.1.32",
     "interface": "wlan", "status": "allowed", "online": False, "first_seen": iso(30000), "last_seen": iso(300)},
    {"mac": "52:54:00:00:00:05", "name": "Fernseher", "hostname": "tv", "ip": "192.168.1.40", "interface": "lan",
     "status": "allowed", "online": True, "first_seen": iso(80000), "last_seen": iso(0)},
    {"mac": "da:54:00:00:00:06", "name": "", "hostname": "", "ip": "192.168.1.77", "interface": "wlan",
     "status": "unknown", "online": True, "random_mac": True, "first_seen": iso(12), "last_seen": iso(0)},
]

PIS = {
    "pis": [
        {"id": "a1", "name": "Wohnzimmer", "category": "Familie", "status": "ok", "ok_time": NOW - 86400, "images": 3,
         "bytes": 9 * GB, "schedule": "weekly", "online": True, "card": "32 GB", "temp": "48 °C"},
        {"id": "a2", "name": "Küche", "category": "Familie", "status": "ok", "ok_time": NOW - 3 * 86400, "images": 3,
         "bytes": 12 * GB, "schedule": "weekly", "online": True, "card": "64 GB", "temp": "52 °C"},
        {"id": "a3", "name": "Werkstatt", "category": "Firma", "status": "ok", "ok_time": NOW - 5 * 86400,
         "images": 2, "bytes": 6 * GB, "schedule": "", "online": False, "card": "16 GB", "temp": ""},
    ],
    "images": 8, "images_bytes": 27 * GB, "mirror_bytes": 20 * GB, "avail_bytes": 3 * TB, "total_bytes": 8 * TB,
    "running": 0, "attention": [], "errors": [],
}


def disk(name, kind, temp, size_tb, free_tb, ssd=False):
    return {"name": name, "kind": kind, "model": "SSD 1TB" if ssd else "HDD 10TB", "status_text": "Normal", "ok": True,
            "temp": temp, "spundown": temp is None, "ssd": ssd, "errors": 0, "size": int(size_tb * TB),
            "free": int(free_tb * TB), "mounted": kind != "parity"}


UNRAID = {
    "available": True, "name": "Tower", "version": "7.3.4", "writable": True, "manager": True, "owner": True,
    "uptime": 12 * 86400 + 5 * 3600, "load": [0.42, 0.5, 0.48], "cpus": 8,
    "memory": {"total": 32 * GB, "available": 19 * GB},
    "array": {"state": "STARTED", "started": True, "missing": 0, "mover": False,
              "check": {"running": False, "paused": False, "percent": None, "last": NOW - 9 * 86400, "last_errors": 0,
                        "last_exit": 0}},
    "disks": [disk("parity", "parity", 38, 10, 0), disk("disk1", "array", 41, 10, 4.2),
              disk("disk2", "array", None, 10, 7.9), disk("cache", "pool", 35, 1, 0.6, ssd=True)],
    "notifications": [{"time": NOW - 7200, "subject": "Paritätsprüfung fertig", "description": "0 Fehler",
                       "importance": "normal"}],
    "temps": {"hot": 48, "max": 55, "hotssd": 60, "maxssd": 70}, "api": True, "userscripts": True,
}


class Canned(BaseHTTPRequestHandler):
    """Fixed answers for the modules without their own stand-in."""
    answers = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        body = self.answers.get(path)
        if path.startswith("/api/devices/"):
            mac = urllib.parse.unquote(path.rsplit("/", 1)[1])
            body = next((d for d in DEVICES if d["mac"] == mac), None)
        data = json.dumps(body if body is not None else {"detail": "Nicht gefunden"}).encode()
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def canned(answers):
    handler = type("Answers", (Canned,), {"answers": answers})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def start_pocket_id():
    """The real module against the stand-in Pocket ID from its tests, filled with an example family."""
    sys.path.insert(0, str(REPO / "modules" / "pocket-id" / "service"))
    sys.path.insert(0, str(REPO / "modules" / "pocket-id" / "tests"))
    import pocketid
    import server as pid_server
    from test_service import KEY, FakePocketId

    clients = {f"c-{n.lower()}": {"name": n, "restricted": True} for n in
               ("Nextcloud", "Immich", "Paperless", "Audiobookshelf")}
    groups = {f"g-{n.lower()}": {"name": f"{n.lower()}_users", "clients": [f"c-{n.lower()}"]} for n in
              ("Nextcloud", "Immich", "Paperless", "Audiobookshelf")}

    def user(uid, first, last, apps, keys, admin=False, email=None):
        return {"id": uid, "username": first.lower(), "firstName": first, "lastName": last,
                "displayName": f"{first} {last}".strip(), "email": email, "isAdmin": admin, "disabled": False,
                "keys": keys, "customClaims": [{"key": "nextcloud_username", "value": first.lower()}],
                "groups": [f"g-{a.lower()}" for a in apps]}

    FakePocketId.state = {"calls": [], "clients": clients, "groups": groups, "users": {
        "u1": user("u1", "Anna", "Muster", ["Nextcloud", "Immich", "Paperless", "Audiobookshelf"], 2, admin=True,
                   email="anna@example.org"),
        "u2": user("u2", "Ben", "Muster", ["Nextcloud", "Immich", "Audiobookshelf"], 1, email="ben@example.org"),
        "u3": user("u3", "Clara", "Muster", ["Nextcloud", "Immich"], 1),
        "u4": user("u4", "Gisela", "Muster", ["Immich"], 0),
    }}
    fake = ThreadingHTTPServer(("127.0.0.1", 0), FakePocketId)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    folder = tempfile.mkdtemp()
    pid_server.ApiHandler.settings = pid_server.Settings(folder)
    pid_server.ApiHandler.settings.update({"url": f"http://127.0.0.1:{fake.server_address[1]}", "api_key": KEY})
    pid_server.ApiHandler.pid = pocketid.PocketId(pid_server.ApiHandler.settings.get)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), pid_server.ApiHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def load(name, path):
    """Loads a module file under its own name (several modules call their files server.py and test_service.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def start_pihole():
    """The real module against two stand-in Pi-holes from its tests, with made-up numbers."""
    folder = REPO / "modules" / "pihole"
    sys.path.insert(0, str(folder / "service"))
    import pihole
    ph_server = load("pihole_server", folder / "service" / "server.py")
    ph_tests = load("pihole_tests", folder / "tests" / "test_service.py")

    def state(total, blocked, clients):
        s = ph_tests.fake_state(total, blocked, clients=clients)
        s["blocked"] = [{"domain": d, "count": blocked * share // 100} for d, share in
                        (("ads.example.com", 31), ("tracker.example.net", 22), ("telemetry.example.org", 14),
                         ("pixel.example.com", 9), ("metrics.example.io", 6))]
        s["permitted"] = [{"domain": d, "count": total * share // 100} for d, share in
                          (("example.org", 18), ("cdn.example.net", 12), ("mail.example.com", 7))]
        for i, h in enumerate(s["history"]):  # quiet at night, busy in the evening, a little uneven
            hour = (h["timestamp"] % 86400) / 3600 + 1
            factor = (0.2 + 1.4 * max(0.0, math.sin((hour - 6) / 17 * math.pi)) ** 1.5) * (0.85 + 0.3 * ((i * 37) % 11) / 10)
            h["total"] = int(h["total"] * factor)
            h["blocked"] = int(h["total"] * (0.15 + 0.1 * ((i * 13) % 7) / 6))
        return s

    pis = [ph_tests.start_fake(state(18342, 3987, [{"ip": "192.168.1.31", "name": "Handy Anna", "count": 6120},
                                                   {"ip": "192.168.1.40", "name": "Fernseher", "count": 4380},
                                                   {"ip": "192.168.1.32", "name": "Laptop Ben", "count": 2950}])),
           ph_tests.start_fake(state(2210, 412, [{"ip": "192.168.1.31", "name": "Handy Anna", "count": 800}]))]
    settings = ph_server.Settings(tempfile.mkdtemp())
    settings.update({"instances": [
        {"name": "Pi im Flur", "url": f"http://127.0.0.1:{pis[0].server_address[1]}", "password": ph_tests.PASSWORD},
        {"name": "Ersatz auf dem Server", "url": f"http://127.0.0.1:{pis[1].server_address[1]}",
         "password": ph_tests.PASSWORD}]})
    handler = type("PiholeApi", (ph_server.ApiHandler,), {"settings": settings, "piholes": pihole.Piholes(settings.get)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def start_wireguard():
    """The real module against the stand-in WireGuard-Easy from its tests, with made-up devices and keys."""
    folder = REPO / "modules" / "wireguard"
    # "server" and "test_service" are already the ones of Pocket ID, so these are loaded with them set aside
    saved = {name: sys.modules.pop(name, None) for name in ("server", "test_service")}
    sys.path[:0] = [str(folder / "tests"), str(folder / "service")]
    try:
        import test_service as wg_tests
        import server as wg_server
        import wgeasy
    finally:
        del sys.path[:2]
        for name, module in saved.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module

    def client(cid, name, minutes, down=0, up=0, enabled=True):
        return {"id": cid, "name": name, "enabled": enabled, "ipv4Address": f"10.8.0.{cid + 1}",
                "createdAt": wg_tests.iso(86400 * 20 * cid), "expiresAt": None,
                "latestHandshakeAt": wg_tests.iso(minutes * 60) if minutes is not None else None,
                "transferRx": up, "transferTx": down}

    fake = ThreadingHTTPServer(("127.0.0.1", 0), wg_tests.FakeWgEasy)
    url = f"http://127.0.0.1:{fake.server_address[1]}"
    wg_tests.FakeWgEasy.state = {"calls": [], "origin": url, "clients": {
        1: client(1, "Handy Anna", 1, down=1_240_000_000, up=86_000_000),
        2: client(2, "Handy Ben", 300, down=420_000_000, up=12_000_000),
        3: client(3, "Laptop Anna", 4000, down=3_100_000_000, up=640_000_000),
        4: client(4, "Tablet Clara", None),
    }}
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    folder = tempfile.mkdtemp()
    wg_server.ApiHandler.settings = wg_server.Settings(folder)
    wg_server.ApiHandler.settings.update({"url": url, "username": wg_tests.USER, "password": wg_tests.PASSWORD})
    wg_server.ApiHandler.wg = wgeasy.WgEasy(wg_server.ApiHandler.settings.get)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), wg_server.ApiHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def shot(name):
    time.sleep(2)
    png = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, check=True, timeout=60).stdout
    (OUT / f"{name}.png").write_bytes(png)
    probe.report("notice", f"Bild {name}")


def main():
    apk = sys.argv[1]
    OUT.mkdir(exist_ok=True)
    probe.OUT.mkdir(exist_ok=True)
    config = tempfile.mkdtemp()
    spool = tempfile.mkdtemp()
    sys.path.insert(0, str(REPO / "core"))
    import home_net

    Path(config, "auth.json").write_text(json.dumps(home_net.password_record("geheim123", rounds=1000)))
    modules = home_net.available_modules()
    pc_url = probe.start_pc_backup()
    upstreams = {
        "netzwerkmonitor": canned({"/api/devices": DEVICES, "/api/me": {"can_manage": True}}),
        "pi-wartung": canned({"/api/overview": PIS}),
        "pc-sicherung": pc_url,
        "unraid": canned({"/api/overview": UNRAID}),
        "pocket-id": start_pocket_id(),
        "wireguard": start_wireguard(),
        "pihole": start_pihole(),
    }
    catalog = [{"id": m, "name": modules[m]["name"], "description": "d", "icon": "", "upstream": url,
                "events": modules[m].get("events", [])} for m, url in upstreams.items()]
    Path(config, "gateway.json").write_text(json.dumps({"owner": "anna", "modules": catalog}))
    os.environ["CONFIG_DIR"] = config
    os.environ["EVENTS_DIR"] = spool
    sys.path.insert(0, str(REPO / "core" / "gateway"))
    import importlib.util
    # "server" is already the Pocket ID module, so the gateway is loaded under its own name
    spec = importlib.util.spec_from_file_location("gateway_server", REPO / "core" / "gateway" / "server.py")
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)

    httpd = ThreadingHTTPServer(("0.0.0.0", probe.PORT), gateway.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    store = gateway.security()
    owner = store.user_by_name("anna")
    code = store.new_code("device", owner["id"])

    adb("install", "-r", apk)
    PKG = probe.PKG
    adb("shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS")
    adb("shell", "settings", "put", "system", "time_12_24", "24")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    find("Adresse im Heimnetz", 60)
    fill("Adresse im Heimnetz", f"http://10.0.2.2:{probe.PORT}")
    fill("Benutzername", "anna")
    fill("Passwort", "geheim123")
    fill("Kopplungscode (optional)", code)
    tap("Speichern und testen")
    find("Pocket ID", 40)
    find("4 Benutzer", 40)
    shot("startseite")

    tap("Netzwerk")
    find("Handy Anna", 30)
    shot("netzwerk")
    tap("Zurück")

    tap("Pi-Wartung")
    find("Wohnzimmer", 30)
    shot("pi-wartung")
    tap("Zurück")

    tap("PC-Sicherung")
    find("Büro-PC", 30)
    shot("pc-sicherung")
    tap("Zurück")

    tap("Unraid")
    find("Tower", 30)
    shot("unraid")
    tap("Zurück")

    tap("Pocket ID", scroll=True)
    find("Gisela Muster", 30)
    shot("pocket-id")
    tap("Ben Muster")
    find("Einladungslink", 30)
    shot("pocket-id-benutzer")
    tap("Einladungslink")
    find("Einladung für Ben", 30)
    shot("pocket-id-einladung")
    tap("Fertig")
    tap("Zurück")
    tap("Zurück")

    tap("WireGuard", scroll=True)
    find("Tablet Clara", 30)
    shot("wireguard")
    tap("Handy Anna")
    find("QR-Code zeigen", 30)
    tap("QR-Code zeigen")
    find("QR-Code für Handy Anna", 30)
    find("Als Text teilen", 30)
    shot("wireguard-qr")
    tap("Fertig")
    tap("Zurück")
    tap("Zurück")

    tap("Pi-hole", scroll=True)
    find("Pausieren für", 30)
    shot("pihole")
    tap("5 min")
    find("Wieder einschalten", 30)
    shot("pihole-pausiert")
    tap("Wieder einschalten")
    find("Werbefilter an", 30)
    tap("Statistik")
    find("Am häufigsten geblockt", 30)
    shot("pihole-statistik")
    probe.report("notice", "Alle Bilder aufgenommen")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        probe.report("error", repr(exc))
        probe.fail("Abbruch")
