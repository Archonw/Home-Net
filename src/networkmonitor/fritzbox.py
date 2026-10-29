"""Anbindung an die FritzBox über TR-064 (fritzconnection)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import FritzConnectionException, FritzLookUpError
from fritzconnection.lib.fritzhosts import FritzHosts

log = logging.getLogger(__name__)

HOST_FILTER_SERVICE = "X_AVM-DE_HostFilter1"


class NotInHostFilter(Exception):
    """Die FritzBox führt die IP nicht in ihrer Kindersicherung (TR-064-Fehler 714).

    Passiert vor allem bei Geräten mit fest eingestellter IP, die ihre Adresse nicht
    per DHCP von der FritzBox bekommen. Solche Geräte kann die FritzBox nicht sperren.
    """


@dataclass(frozen=True)
class Host:
    mac: str
    ip: str
    hostname: str
    interface: str
    active: bool


class Router(Protocol):
    """Was der Monitor von einem Router braucht. Weitere Router können das später umsetzen."""

    def list_hosts(self) -> list[Host]: ...

    def get_internet_access(self, ip: str) -> bool: ...

    def set_internet_access(self, ip: str, allowed: bool) -> None: ...

    def read_device_log(self) -> list[str]: ...


class FritzBox:
    def __init__(self, address: str, user: str, password: str, timeout: float = 10) -> None:
        self._address = address
        self._user = user
        self._password = password
        self._timeout = timeout
        self._fc: FritzConnection | None = None

    @property
    def connection(self) -> FritzConnection:
        if self._fc is None:
            self._fc = FritzConnection(
                address=self._address,
                user=self._user or None,
                password=self._password or None,
                timeout=self._timeout,
            )
        return self._fc

    def reset(self) -> None:
        """Verbindung beim nächsten Zugriff neu aufbauen (z. B. nach FritzBox-Neustart)."""
        self._fc = None

    def list_hosts(self) -> list[Host]:
        hosts = FritzHosts(fc=self.connection).get_hosts_info()
        return [
            Host(
                mac=h["mac"] or "",
                ip=h["ip"] or "",
                hostname=h["name"] or "",
                interface=h["interface_type"] or "",
                active=bool(h["status"]),
            )
            for h in hosts
            if h.get("mac")
        ]

    def get_internet_access(self, ip: str) -> bool:
        try:
            result = self.connection.call_action(
                HOST_FILTER_SERVICE, "GetWANAccessByIP", NewIPv4Address=ip
            )
        except FritzLookUpError as exc:
            raise NotInHostFilter(ip) from exc
        return not bool(int(result.get("NewDisallow", 0)))

    def set_internet_access(self, ip: str, allowed: bool) -> None:
        try:
            self.connection.call_action(
                HOST_FILTER_SERVICE,
                "DisallowWANAccessByIP",
                NewIPv4Address=ip,
                NewDisallow=0 if allowed else 1,
            )
        except FritzLookUpError as exc:
            raise NotInHostFilter(ip) from exc

    def read_device_log(self) -> list[str]:
        result = self.connection.call_action("DeviceInfo1", "GetDeviceLog")
        return [line for line in (result.get("NewDeviceLog") or "").splitlines() if line.strip()]


__all__ = ["FritzBox", "FritzConnectionException", "Host", "NotInHostFilter", "Router"]
