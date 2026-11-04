"""Geräteliste ohne FritzBox: aus der Nachbartabelle des Rechners, auf dem der Monitor läuft.

Der Monitor läuft im Host-Netz. Damit jedes Gerät in der Tabelle steht, schickt er vor jedem
Lesen ein leeres UDP-Paket an jede Adresse der eigenen Heimnetze; dafür muss der Rechner die
Adresse per ARP auflösen. Dazu braucht es keine Rechte. Namen kommen aus dem DNS des Heimnetzes
(der Router kennt die Namen seiner DHCP-Geräte meist).

Sperren kann der Monitor so nicht, er meldet und merkt sich die Geräte nur.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .fritzbox import Host

log = logging.getLogger(__name__)

ARP_TABLE = Path("/proc/net/arp")
ROUTES = Path("/proc/net/route")
# Interfaces of containers, VMs and VPNs: their devices are not part of the home network
SKIP_PREFIXES = (
    "docker",
    "br-",
    "veth",
    "virbr",
    "vnet",
    "tap",
    "tun",
    "wg",
    "lo",
    "shim-",
    "vhost",
)
# A /22 at most, so a misconfigured huge network does not mean thousands of packets per poll
MAX_ADDRESSES = 1024
ARP_COMPLETE = 0x2
WAIT_FOR_REPLIES = 2.0


class BlockNotSupported(Exception):
    """Ohne FritzBox kann der Monitor keine Geräte vom Internet trennen."""


def home_networks(routes: Path = ROUTES) -> list[tuple[str, ipaddress.IPv4Network]]:
    """(interface, network) this machine sits in directly, without Docker, VMs and VPNs."""
    out = []
    try:
        lines = routes.read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 8 or parts[0].startswith(SKIP_PREFIXES):
            continue
        dest, gateway, mask = (
            struct.pack("<I", int(x, 16)) for x in (parts[1], parts[2], parts[7])
        )
        if gateway != b"\0\0\0\0" or mask == b"\0\0\0\0":
            continue  # only directly connected networks, no default route
        net = ipaddress.IPv4Network(
            f"{socket.inet_ntoa(dest)}/{socket.inet_ntoa(mask)}", strict=False
        )
        if net.is_private and not any(n == net for _, n in out):
            out.append((parts[0], net))
    return out


def read_arp(table: Path = ARP_TABLE) -> list[tuple[str, str, str, bool]]:
    """(ip, mac, interface, complete) from the kernel's ARP table."""
    out = []
    try:
        lines = table.read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) < 6:
            continue
        ip, flags, mac, device = parts[0], int(parts[2], 16), parts[3].lower(), parts[5]
        if mac == "00:00:00:00:00:00":
            continue
        out.append((ip, mac, device, bool(flags & ARP_COMPLETE)))
    return out


class LanScan:
    """Router ohne Router: findet Geräte selbst, kann aber nicht sperren."""

    can_block = False

    def __init__(
        self, routes: Path = ROUTES, table: Path = ARP_TABLE, wait: float = WAIT_FOR_REPLIES
    ) -> None:
        self._routes = routes
        self._table = table
        self._wait = wait
        self._names: dict[str, str] = {}

    def _knock(self, networks) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            for _, net in networks:
                for ip in list(net.hosts())[:MAX_ADDRESSES]:
                    try:
                        sock.sendto(b"", (str(ip), 9))  # discard port; only the ARP lookup matters
                    except OSError:
                        pass
        time.sleep(self._wait)

    def _name(self, ip: str) -> str:
        if ip not in self._names:
            try:
                self._names[ip] = socket.gethostbyaddr(ip)[0].split(".")[0]
            except OSError:
                self._names[ip] = ""
        return self._names[ip]

    def list_hosts(self) -> list[Host]:
        networks = home_networks(self._routes)
        if not networks:
            raise OSError("Kein Heimnetz gefunden. Läuft der Monitor im Host-Netz?")
        self._knock(networks)
        inside = [net for _, net in networks]
        rows = [
            r
            for r in read_arp(self._table)
            if any(ipaddress.IPv4Address(r[0]) in net for net in inside)
        ]
        with ThreadPoolExecutor(max_workers=16) as pool:
            names = list(pool.map(self._name, [r[0] for r in rows]))
        return [
            Host(mac=mac, ip=ip, hostname=name, interface="", active=complete)
            for (ip, mac, _dev, complete), name in zip(rows, names, strict=True)
        ]

    def get_internet_access(self, ip: str) -> bool:
        return True

    def set_internet_access(self, ip: str, allowed: bool) -> None:
        if not allowed:
            raise BlockNotSupported(ip)

    def read_device_log(self) -> list[str]:
        return []
