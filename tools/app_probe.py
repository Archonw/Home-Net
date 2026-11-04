#!/usr/bin/env python3
"""Runs the app in an emulator against the real gateway: pairs, receives a message, loses the device key.

Started by the CI workflow App-Probelauf. The emulator reaches this machine at 10.0.2.2.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = "de.archonw.netmon"
OUT = Path("probe")
PORT = 8093


def load_words():
    """English word lists as the app has them; the emulator runs in English, so the app shows these."""
    words = {}
    for path in sorted((REPO / "i18n" / "en").glob("*.json")):
        words.update(json.loads(path.read_text(encoding="utf-8")))
    templates = []
    for german, english in words.items():
        if "{" in german:
            parts = re.split(r"(\{\d\})", german)
            regex = "".join(f"(?P<p{p[1]}>.+?)" if re.fullmatch(r"\{\d\}", p) else re.escape(p) for p in parts)
            templates.append((re.compile(regex), english))
    templates.sort(key=lambda t: -len(t[0].pattern))  # longest first, like the app
    return words, templates


WORDS, TEMPLATES = load_words()


def english(text):
    if text in WORDS:
        return WORDS[text]
    for regex, target in TEMPLATES:
        match = regex.fullmatch(text)
        if match:
            return re.sub(r"\{(\d)\}", lambda m: english(match.group(f"p{m.group(1)}")), target)
    if "\n" in text:
        return "\n".join(english(line) for line in text.split("\n"))
    return text


def shown(label, haystack):
    """True if the label appears in German or in its English translation."""
    return label in haystack or english(label) in haystack


def adb(*args, check=True):
    return subprocess.run(["adb", *args], check=check, capture_output=True, text=True, timeout=120).stdout


def report(kind, text):
    print(f"::{kind}::{text.replace(chr(10), ' | ')[:1500]}", flush=True)


def fail(text):
    (OUT / "logcat.txt").write_text(adb("logcat", "-d", check=False))
    (OUT / "screen.xml").write_text(screen_dump())
    hints = [line[-160:] for line in (OUT / "logcat.txt").read_text().splitlines()
             if re.search(r"NotificationService|BootReceiver|ForegroundService|netmon.*(Denial|not allowed)", line)]
    report("warning", "Protokoll: " + " | ".join(hints[-8:]))
    crash = re.findall(r"FATAL EXCEPTION.*?(?=\n\S+ \S+ +\d+ +\d+ [^E])", (OUT / "logcat.txt").read_text(), re.S)
    report("error", f"{text}. Bildschirm: {texts()[:40]}. Absturz: {crash[:1]}")
    sys.exit(1)


def screen_dump():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", check=False)
    return adb("exec-out", "cat", "/sdcard/ui.xml", check=False)


def nodes():
    try:
        return list(ET.fromstring(screen_dump()).iter("node"))
    except ET.ParseError:
        return []


def texts():
    return [t for t in (n.get("text") or n.get("content-desc") for n in nodes()) if t]


def center(node):
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
    return (x1 + x2) // 2, (y1 + y2) // 2


def find(label, timeout=30, scroll=False):
    """A whole label wins over text that merely mentions it, such as an explanation above a field."""
    end = time.time() + timeout
    while time.time() < end:
        found = nodes()
        names = {label, english(label)}
        for wanted in (lambda n: (n.get("text") or n.get("content-desc")) in names,
                       lambda n: any(shown(label, n.get(key) or "") for key in ("text", "content-desc"))):
            for node in found:
                if wanted(node):
                    return node
        if scroll:
            width, height = map(int, re.findall(r"\d+", adb("shell", "wm", "size"))[:2])
            x = str(width // 2)
            adb("shell", "input", "swipe", x, str(height * 3 // 4), x, str(height // 4), "400")
        time.sleep(1)
    fail(f"„{label}“ erscheint nicht")


def tap(label, scroll=False):
    x, y = center(find(label, scroll=scroll))
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(1)


def fill(label, value):
    tap(label)
    adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
    adb("shell", "input", "keyevent", *["KEYCODE_DEL"] * 12)
    adb("shell", "input", "text", value)
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)


def wait_for(condition, what, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return
        time.sleep(1)
    fail(what)


def alive():
    return bool(adb("shell", "pidof", PKG, check=False).strip())


def service_running():
    return "NotificationService" in adb("shell", "dumpsys", "activity", "services", PKG, check=False)


def notifications():
    return adb("shell", "dumpsys", "notification", "--noredact", check=False)


class ProbeArchive:
    """Stands in for restic: one finished backup, nothing on disk."""

    def __init__(self):
        self.cache = {}

    def ensure(self, pc):
        pass

    def runs(self, pc, fresh=False):
        return [{"id": "2026-11-01_030000", "label": "01.11.26, 03:00", "complete": True}]

    def size(self, pc_id):
        return 5 * 1024 ** 3

    def path(self, pc_id):
        return "/nonexistent/" + pc_id


class ProbeServices:
    def server_ip(self):
        return "10.0.2.2"

    def users_changed(self):
        pass

    def netboot_state(self):
        return {"enabled": False, "running": False, "error": "", "server_ip": "10.0.2.2", "configured_ip": ""}


def start_pc_backup():
    """The real PC-Sicherung service with a stand-in archive; returns its address for the gateway."""
    import importlib.util
    path = REPO / "modules" / "pc-sicherung" / "service" / "server.py"
    spec = importlib.util.spec_from_file_location("pc_server", path)
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    pc.wake = lambda macs: None
    store = pc.Store(tempfile.mkdtemp())
    store.create({"name": "Büro-PC", "macs": "52:54:00:12:34:56", "category": "Familie"})
    pc.Base.engine = pc.Engine(store, ProbeArchive(), ProbeServices())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), pc.ApiHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def main():
    apk = sys.argv[1]
    OUT.mkdir(exist_ok=True)
    config = tempfile.mkdtemp()
    spool = tempfile.mkdtemp()
    os.makedirs(f"{spool}/pi-wartung")
    sys.path.insert(0, str(REPO / "core"))
    import home_net

    Path(config, "auth.json").write_text(json.dumps(home_net.password_record("geheim123", rounds=1000)))
    modules = home_net.available_modules()
    catalog = [{"id": "pi-wartung", "name": "Pi-Wartung", "description": "d", "icon": "", "upstream": "http://127.0.0.1:9",
                "events": modules["pi-wartung"]["events"]},
               {"id": "pc-sicherung", "name": "PC-Sicherung", "description": "d", "icon": "", "upstream": start_pc_backup(),
                "events": modules["pc-sicherung"]["events"]}]
    Path(config, "gateway.json").write_text(json.dumps({"owner": "chef", "modules": catalog}))
    os.environ["CONFIG_DIR"] = config
    os.environ["EVENTS_DIR"] = spool
    sys.path.insert(0, str(REPO / "core" / "gateway"))
    import server as gateway

    def serve():
        httpd = ThreadingHTTPServer(("0.0.0.0", PORT), gateway.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    httpd = serve()
    threading.Thread(target=gateway.scan_forever, daemon=True).start()
    store = gateway.security()
    owner = store.user_by_name("chef")
    code = store.new_code("device", owner["id"])

    adb("install", "-r", apk)
    adb("shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS")
    adb("logcat", "-c")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    find("Adresse im Heimnetz", 60)
    if not alive():
        fail("App läuft nach dem Start nicht")
    report("notice", "Start ohne Kopplung in Ordnung")

    fill("Adresse im Heimnetz", f"http://10.0.2.2:{PORT}")
    fill("Adresse für unterwegs (optional)", f"https://10.0.2.2:{PORT + 1}")
    fill("Benutzername", "chef")
    fill("Passwort", "geheim123")
    fill("Kopplungscode (optional)", code)
    tap("Speichern und testen")
    find("Pi-Wartung", 40)
    wait_for(service_running, "Hintergrunddienst läuft nach dem Koppeln nicht")
    report("notice", "Gekoppelt, Hintergrunddienst läuft")

    # Vor der ersten Meldung: Deren Einblendung läge sonst über der Kachel
    tap("Pi-Wartung")
    find("Zurück")
    if shown("Einstellungen", texts()):
        fail("Untere Leiste im Dienst noch sichtbar")
    tap("Zurück")
    find("Einstellungen")
    report("notice", "Im Dienst nur der Zurück-Pfeil, er führt zur Startseite")

    tap("PC-Sicherung")
    tap("Büro-PC")
    find("01.11.26, 03:00")
    tap("Jetzt sichern")
    tap("Starten")
    find("Sicherung wartet auf den Start des PCs")
    tap("Zurück")
    find("1 gerade in Arbeit")
    tap("Zurück")
    find(f"1 PC, 5,0 GB, {english('1 in Arbeit')}")  # the tile translates its parts
    report("notice", "PC-Sicherung: Liste, Detail und Sicherung starten in Ordnung")
    time.sleep(5)

    (Path(spool) / "pi-wartung" / "probe.json").write_text(json.dumps(
        {"type": "backup_failed", "title": "Sicherung von Probe fehlgeschlagen", "message": "Testfehler",
         "priority": "high", "category": "Familie", "link": "pi/a1"}))
    wait_for(lambda: shown("Sicherung von Probe fehlgeschlagen", notifications()), "Meldung kommt nicht als Benachrichtigung an")
    report("notice", "Meldung kam auf dem Gerät an")

    tap("Einstellungen")
    tap("Benachrichtigungen", scroll=True)
    find("Auf diesem Handy empfangen")
    find("Sicherung erfolgreich", scroll=True)
    find("Sicherung von Probe fehlgeschlagen", scroll=True)
    report("notice", "Seite Benachrichtigungen zeigt Auswahl und Verlauf")

    # Eine echte Neuinstallation wie beim App-Update
    adb("install", "-r", apk)
    wait_for(service_running, "Dienst kommt nach dem Update nicht wieder")
    report("notice", "Dienst kommt nach einem Update wieder")

    # Während der Unterbrechung Verpasstes muss nachkommen
    adb("shell", "am", "force-stop", PKG)
    gateway.inbox().add("pi-wartung", "backup_failed", "Nachgeliefert", "x", "high")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    wait_for(lambda: shown("Nachgeliefert", notifications()), "Verpasste Meldung kommt nicht nach")
    report("notice", "Verpasste Meldung wird nachgeliefert")

    # Echter Ausfall: Niemand nimmt mehr an, die offene Verbindung bricht ab
    httpd.shutdown()
    httpd.server_close()
    identify = gateway.Handler.identify
    gateway.Handler.identify = lambda self: (None, "")
    gateway.inbox().wake()
    time.sleep(3)
    gateway.Handler.identify = identify
    wait_for(lambda: shown("Home-Net nicht erreichbar", notifications()), "Keine Warnung beim Server-Ausfall", 150)
    report("notice", "Warnung beim Server-Ausfall kommt")
    httpd = serve()
    wait_for(lambda: shown("Home-Net wieder erreichbar", notifications()), "Keine Entwarnung", 330)
    report("notice", "Entwarnung kommt")

    adb("reboot")
    time.sleep(10)
    subprocess.run(["adb", "wait-for-device"], timeout=180, check=True)
    wait_for(lambda: adb("shell", "getprop", "sys.boot_completed", check=False).strip() == "1", "Emulator startet nicht", 240)
    wait_for(service_running, "Dienst kommt nach einem Neustart des Handys nicht wieder", 120)
    report("notice", "Dienst kommt nach einem Neustart des Handys wieder")

    device = store.account(owner)["devices"][0]
    store.remove_item("devices", device["id"], owner)
    gateway.inbox().wake()
    wait_for(lambda: not service_running(), "Dienst läuft nach dem Entfernen des Geräts weiter")
    report("notice", "Entferntes Gerät stoppt den Empfang")

    log = adb("logcat", "-d")
    (OUT / "logcat.txt").write_text(log)
    if "FATAL EXCEPTION" in log:
        fail("Absturz im Protokoll")
    report("notice", "Probelauf bestanden")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "stderr", "") or ""
        report("error", f"{traceback.format_exc()[-900:]} {detail[:300]}")
        fail("Abbruch")
