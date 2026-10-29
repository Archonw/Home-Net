"""Abgleich Router ↔ Datenbank und Durchsetzung der Sperren."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Settings
from .fritzbox import Router
from .store import Device, Status, Store

log = logging.getLogger(__name__)

INITIAL_SCAN_DONE = "initial_scan_done"


@dataclass
class PollResult:
    new_devices: list[Device] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Monitor:
    def __init__(self, router: Router, store: Store, settings: Settings) -> None:
        self.router = router
        self.store = store
        self.settings = settings

    def poll_once(self) -> PollResult:
        result = PollResult()
        first_scan = self.store.get_meta(INITIAL_SCAN_DONE) is None
        initial_status = (
            Status.ALLOWED if first_scan and self.settings.trust_initial_devices else Status.UNKNOWN
        )

        for host in self.router.list_hosts():
            if not first_scan and not host.active and self.store.get(host.mac) is None:
                # alte, inaktive FritzBox-Einträge nicht neu anlegen
                continue
            device, is_new = self.store.record_sighting(
                host.mac,
                ip=host.ip,
                hostname=host.hostname,
                interface=host.interface,
                online=host.active,
                initial_status=initial_status,
            )
            if is_new and device.status == Status.UNKNOWN:
                result.new_devices.append(device)
                log.info("Neues Gerät: %s %s (%s)", device.mac, device.hostname, device.ip)
            if host.active and host.ip:
                try:
                    self.enforce(device)
                except Exception as exc:  # noqa: BLE001 – ein Gerät darf den Lauf nicht stoppen
                    msg = f"Sperre für {device.mac} ({device.ip}) fehlgeschlagen: {exc}"
                    log.warning(msg)
                    result.errors.append(msg)

        if first_scan:
            self.store.set_meta(INITIAL_SCAN_DONE, "1")
        return result

    def should_have_internet(self, device: Device) -> bool:
        if device.status == Status.BLOCKED:
            return False
        if device.status == Status.UNKNOWN:
            return not self.settings.quarantine_unknown
        return True

    def enforce(self, device: Device) -> None:
        """Bringt die Internet-Sperre der FritzBox mit dem Gerätestatus in Einklang."""
        if not device.ip:
            return
        wanted = self.should_have_internet(device)
        if self.router.get_internet_access(device.ip) != wanted:
            self.router.set_internet_access(device.ip, wanted)
            self.store.add_event(device.mac, "internet_on" if wanted else "internet_off", device.ip)

    def set_status(self, mac: str, status: Status) -> Device | None:
        device = self.store.set_status(mac, status)
        if device and device.online and device.ip:
            self.enforce(device)
        return device
