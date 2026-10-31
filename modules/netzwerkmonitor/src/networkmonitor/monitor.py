"""Abgleich Router ↔ Datenbank und Durchsetzung der Sperren."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Settings
from .fritzbox import Host, NotInHostFilter, Router
from .store import Device, Status, Store, is_random_mac

log = logging.getLogger(__name__)

INITIAL_SCAN_DONE = "initial_scan_done"


@dataclass
class PollResult:
    new_devices: list[Device] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BlockNotPossible(Exception):
    """Gerät soll gesperrt sein, die FritzBox kann es aber nicht sperren."""

    def __init__(self, device: Device) -> None:
        super().__init__(
            f"{device.mac} ({device.ip}) lässt sich nicht sperren: Die FritzBox führt diese IP "
            "nicht in ihrer Kindersicherung. Das passiert meist bei Geräten mit fest "
            "eingestellter IP statt DHCP von der FritzBox."
        )


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

        # Geräte mit IP zuerst, damit Repeater-Kopien ihr Original schon in der Datenbank finden
        for host in sorted(self.router.list_hosts(), key=lambda h: not h.ip):
            if not first_scan and not host.active and self.store.get(host.mac) is None:
                # alte, inaktive FritzBox-Einträge nicht neu anlegen
                continue
            if self._is_repeater_copy(host):
                log.debug("Repeater-Kopie übersprungen: %s", host.mac)
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
                device.hint = self._blocked_relative_hint(device)
                result.new_devices.append(device)
                log.info("Neues Gerät: %s %s (%s)", device.mac, device.hostname, device.ip)
            if host.active and host.ip:
                try:
                    self.enforce(device)
                except BlockNotPossible as exc:
                    log.warning("%s", exc)
                    result.errors.append(str(exc))
                except Exception as exc:  # noqa: BLE001 – ein Gerät darf den Lauf nicht stoppen
                    msg = f"Internet-Sperre für {device.mac} ({device.ip}) nicht abgleichbar: {exc}"
                    log.warning(msg)
                    result.errors.append(msg)

        if first_scan:
            self.store.set_meta(INITIAL_SCAN_DONE, "1")
        return result

    def _is_repeater_copy(self, host: Host) -> bool:
        """WLAN-Repeater melden bekannte Geräte auch unter umgeschriebener MAC und ohne IP.

        Ohne IP lässt sich so ein Eintrag weder nutzen noch sperren. Sobald die FritzBox ihm eine
        IP zuordnet, gilt er als normales neues Gerät und wird gemeldet.
        """
        return (
            not host.ip
            and is_random_mac(host.mac)
            and self.store.get(host.mac) is None
            and bool(self.store.relatives(host.mac))
        )

    def _blocked_relative_hint(self, device: Device) -> str:
        for other in self.store.relatives(device.mac):
            if other.status == Status.BLOCKED:
                label = other.name or other.hostname or other.mac
                return f"Könnte das gesperrte Gerät {label} über einen Repeater sein"
        return ""

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
        try:
            if self.router.get_internet_access(device.ip) == wanted:
                return
            self.router.set_internet_access(device.ip, wanted)
        except NotInHostFilter:
            if wanted:
                # nicht in der Kindersicherung heißt auch: nicht gesperrt – alles in Ordnung
                return
            raise BlockNotPossible(device) from None
            self.store.add_event(device.mac, "internet_on" if wanted else "internet_off", device.ip)

    def set_status(self, mac: str, status: Status) -> Device | None:
        device = self.store.set_status(mac, status)
        if device and device.online and device.ip:
            self.enforce(device)
        return device
