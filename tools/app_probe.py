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


def adb(*args, check=True):
    return subprocess.run(["adb", *args], check=check, capture_output=True, text=True, timeout=120).stdout


def report(kind, text):
    print(f"::{kind}::{text.replace(chr(10), ' | ')[:1500]}", flush=True)


def fail(text):
    (OUT / "logcat.txt").write_text(adb("logcat", "-d", check=False))
    (OUT / "screen.xml").write_text(screen_dump())
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
        for wanted in (lambda n: label == (n.get("text") or n.get("content-desc")),
                       lambda n: label in (n.get("text") or "") or label in (n.get("content-desc") or "")):
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
                "events": modules["pi-wartung"]["events"]}]
    Path(config, "gateway.json").write_text(json.dumps({"owner": "chef", "modules": catalog}))
    os.environ["CONFIG_DIR"] = config
    os.environ["EVENTS_DIR"] = spool
    sys.path.insert(0, str(REPO / "core" / "gateway"))
    import server as gateway

    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", PORT), gateway.Handler).serve_forever, daemon=True).start()
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
    fill("Benutzername", "chef")
    fill("Passwort", "geheim123")
    fill("Kopplungscode (optional)", code)
    tap("Speichern und testen")
    find("Pi-Wartung", 40)
    wait_for(service_running, "Hintergrunddienst läuft nach dem Koppeln nicht")
    report("notice", "Gekoppelt, Hintergrunddienst läuft")
    time.sleep(5)

    (Path(spool) / "pi-wartung" / "probe.json").write_text(json.dumps(
        {"type": "backup_failed", "title": "Sicherung von Probe fehlgeschlagen", "message": "Testfehler",
         "priority": "high", "category": "Familie", "link": "pi/a1"}))
    wait_for(lambda: "Sicherung von Probe fehlgeschlagen" in notifications(), "Meldung kommt nicht als Benachrichtigung an")
    report("notice", "Meldung kam auf dem Gerät an")

    tap("Einstellungen")
    tap("Benachrichtigungen", scroll=True)
    find("Auf diesem Handy empfangen")
    find("Sicherung erfolgreich", scroll=True)
    find("Sicherung von Probe fehlgeschlagen", scroll=True)
    report("notice", "Seite Benachrichtigungen zeigt Auswahl und Verlauf")

    # Eine echte Neuinstallation wie beim App-Update; ein Neustart des Handys läuft über denselben Empfänger
    adb("shell", "am", "force-stop", PKG)
    adb("install", "-r", apk)
    wait_for(service_running, "Empfänger startet den Dienst nicht neu")
    report("notice", "Dienst kommt nach einem Update wieder")

    # Während der Unterbrechung Verpasstes muss nachkommen
    adb("shell", "am", "force-stop", PKG)
    gateway.inbox().add("pi-wartung", "backup_failed", "Nachgeliefert", "x", "high")
    adb("install", "-r", apk)
    wait_for(lambda: "Nachgeliefert" in notifications(), "Verpasste Meldung kommt nicht nach")
    report("notice", "Verpasste Meldung wird nachgeliefert")

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
