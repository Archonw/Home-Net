"""Prüft, was die FritzBox über TR-064 kann.

Aufruf (im Container):  networkmonitor-probe
Mit Sperr-Test:          networkmonitor-probe --test-block 192.168.178.50
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from .config import Settings
from .fritzbox import HOST_FILTER_SERVICE, FritzBox

INTERESTING = re.compile(r"mac|filter|access|allow|deny|block", re.IGNORECASE)
LOG_PATTERN = re.compile(r"wlan|mac|gesperrt|abgelehnt|neues gerät|unbekannt", re.IGNORECASE)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FritzBox-Fähigkeiten prüfen")
    parser.add_argument("--test-block", metavar="IP", help="Internet für diese IP kurz sperren")
    parser.add_argument("--log-lines", type=int, default=40, help="Anzahl Protokollzeilen")
    parser.add_argument("--all-actions", action="store_true", help="Alle Dienste/Aktionen zeigen")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    box = FritzBox(settings.fritz_address, settings.fritz_user, settings.fritz_password)

    section("Verbindung")
    try:
        fc = box.connection
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: keine Verbindung zu {settings.fritz_address}: {exc}")
        return 1
    print(f"Modell:   {fc.modelname}")
    print(f"FritzOS:  {fc.system_version}")

    section("Hostliste")
    try:
        hosts = box.list_hosts()
        print(f"OK – {len(hosts)} Geräte bekannt, {sum(h.active for h in hosts)} aktiv")
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: {exc}")

    section("Internet-Sperre (X_AVM-DE_HostFilter)")
    if HOST_FILTER_SERVICE in fc.services:
        print("OK – Aktionen:", ", ".join(sorted(fc.services[HOST_FILTER_SERVICE].actions)))
    else:
        print("NICHT VORHANDEN – Internet-Sperre per Schnittstelle nicht möglich")

    section("WLAN-Dienste: Aktionen zu MAC/Filter/Zugang")
    found_mac_filter = False
    for name in sorted(fc.services):
        actions = sorted(fc.services[name].actions)
        if args.all_actions:
            print(f"{name}: {', '.join(actions)}")
            continue
        hits = [a for a in actions if INTERESTING.search(a)]
        if hits:
            print(f"{name}: {', '.join(hits)}")
            if name.startswith("WLANConfiguration") and any("mac" in a.lower() for a in hits):
                found_mac_filter = True
    print(
        "\n→ WLAN-MAC-Liste per TR-064 pflegbar: "
        + ("WAHRSCHEINLICH (siehe oben)" if found_mac_filter else "NEIN, keine passende Aktion")
    )

    section(f"Ereignisprotokoll (relevante Zeilen, max. {args.log_lines})")
    try:
        lines = [line for line in box.read_device_log() if LOG_PATTERN.search(line)]
        for line in lines[: args.log_lines]:
            print(line)
        if not lines:
            print("(keine passenden Einträge)")
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: {exc}")

    if args.test_block:
        section(f"Sperr-Test für {args.test_block}")
        ip = args.test_block
        try:
            print("vorher Internet:", box.get_internet_access(ip))
            box.set_internet_access(ip, False)
            print("gesperrt, Internet jetzt:", box.get_internet_access(ip))
            time.sleep(5)
        finally:
            box.set_internet_access(ip, True)
            print("wieder freigegeben, Internet jetzt:", box.get_internet_access(ip))

    print("\nBitte die komplette Ausgabe (ohne Passwörter) zurückschicken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
