"""Meldungen an den Home-Net-Zugang: je Meldung eine Datei, der Zugang verteilt sie."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .notify import describe
from .store import Device

log = logging.getLogger(__name__)

# WLAN-Geräte schlafen oft kurz, erst nach dieser Zeit gilt ein Gerät wirklich als weg
OFFLINE_AFTER = 5 * 60
ROUTER_AFTER = 10 * 60


def _title(device: Device) -> str:
    return device.name or device.hostname or device.mac


class HubEvents:
    def __init__(self, spool: str, clock: Callable[[], float] = time.time) -> None:
        self.spool = spool
        self.clock = clock
        self.offline_since: dict[str, float] = {}
        self.reported: set[str] = set()
        self.first_check = True
        self.router_failing_since: float | None = None
        self.router_reported = False

    def send(
        self, kind: str, title: str, message: str = "", priority: str = "default", link: str = ""
    ) -> None:
        if not self.spool:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self.spool, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "type": kind,
                        "title": title,
                        "message": message,
                        "priority": priority,
                        "link": link,
                    },
                    f,
                )
            os.rename(tmp, Path(tmp).with_suffix(".json"))
        except OSError as exc:
            log.warning("Meldung %s nicht abgelegt: %s", kind, exc)

    def new_device(self, device: Device) -> None:
        self.send(
            "new_device",
            "Neues Gerät im Netz",
            describe(device).split("\n", 1)[1],
            "high",
            f"device/{device.mac}",
        )

    def check_devices(self, devices: Iterable[Device]) -> None:
        """Meldet Geräte im Blick, die länger weg sind, und wenn sie zurückkommen."""
        now = self.clock()
        watched = [d for d in devices if d.watched]
        macs = {d.mac for d in watched}
        self.offline_since = {m: t for m, t in self.offline_since.items() if m in macs}
        self.reported &= macs
        for device in watched:
            if device.online:
                self.offline_since.pop(device.mac, None)
                if device.mac in self.reported:
                    self.reported.discard(device.mac)
                    self.send(
                        "device_offline",
                        f"{_title(device)} ist wieder online",
                        link=f"device/{device.mac}",
                    )
                continue
            if self.first_check:
                # Schon vor dem Start offline: wurde bereits gemeldet oder ist nicht neu
                self.reported.add(device.mac)
            since = self.offline_since.setdefault(device.mac, now)
            if now - since >= OFFLINE_AFTER and device.mac not in self.reported:
                self.reported.add(device.mac)
                self.send(
                    "device_offline",
                    f"{_title(device)} ist offline",
                    f"Zuletzt gesehen: {device.last_seen}",
                    "high",
                    f"device/{device.mac}",
                )
        self.first_check = False

    def router_failed(self, error: str) -> None:
        now = self.clock()
        if self.router_failing_since is None:
            self.router_failing_since = now
        if now - self.router_failing_since >= ROUTER_AFTER and not self.router_reported:
            self.router_reported = True
            self.send("router_down", "FritzBox nicht erreichbar", error[:300], "high")

    def router_ok(self) -> None:
        if self.router_reported:
            self.send("router_down", "FritzBox wieder erreichbar")
        self.router_failing_since = None
        self.router_reported = False
