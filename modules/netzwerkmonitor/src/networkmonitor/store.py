"""Geräte-Datenbank (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    mac         TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    hostname    TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT '',
    interface   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'unknown',
    online      INTEGER NOT NULL DEFAULT 0,
    random_mac  INTEGER NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    mac       TEXT NOT NULL,
    type      TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Status(StrEnum):
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    BLOCKED = "blocked"


@dataclass
class Device:
    mac: str
    name: str
    hostname: str
    ip: str
    interface: str
    status: Status
    online: bool
    random_mac: bool
    notes: str
    first_seen: str
    last_seen: str
    # Nur für die Benachrichtigung über ein neues Gerät, wird nicht gespeichert
    hint: str = ""
    watched: bool = False


def normalize_mac(mac: str) -> str:
    cleaned = mac.replace("-", ":").replace(".", "").upper()
    if ":" not in cleaned and len(cleaned) == 12:
        cleaned = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    return cleaned


def is_random_mac(mac: str) -> bool:
    """Zufällige ("private") MAC-Adressen haben das locally-administered-Bit gesetzt."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def mac_tail(mac: str) -> str:
    """Die letzten drei Bytes; WLAN-Repeater lassen sie beim Umschreiben der MAC stehen."""
    return normalize_mac(mac)[-8:]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            columns = {row["name"] for row in self._db.execute("PRAGMA table_info(device)")}
            if "watched" not in columns:
                self._db.execute("ALTER TABLE device ADD COLUMN watched INTEGER NOT NULL DEFAULT 0")

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        return Device(
            mac=row["mac"],
            name=row["name"],
            hostname=row["hostname"],
            ip=row["ip"],
            interface=row["interface"],
            status=Status(row["status"]),
            online=bool(row["online"]),
            random_mac=bool(row["random_mac"]),
            notes=row["notes"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            watched=bool(row["watched"]),
        )

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_config(self) -> dict:
        rows = self._db.execute("SELECT key, value FROM meta WHERE key LIKE 'cfg.%'").fetchall()
        return {r["key"][4:]: json.loads(r["value"]) for r in rows}

    def set_config(self, values: dict) -> None:
        with self._lock, self._db:
            for key, value in values.items():
                if value is None:
                    self._db.execute("DELETE FROM meta WHERE key = ?", (f"cfg.{key}",))
                else:
                    self._db.execute(
                        "INSERT INTO meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (f"cfg.{key}", json.dumps(value)),
                    )

    def get(self, mac: str) -> Device | None:
        row = self._db.execute(
            "SELECT * FROM device WHERE mac = ?", (normalize_mac(mac),)
        ).fetchone()
        return self._row_to_device(row) if row else None

    def list(self) -> list[Device]:
        rows = self._db.execute(
            "SELECT * FROM device ORDER BY "
            "CASE status WHEN 'unknown' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, "
            "online DESC, last_seen DESC"
        ).fetchall()
        return [self._row_to_device(r) for r in rows]

    def relatives(self, mac: str) -> list[Device]:
        """Andere Geräte mit IP, deren MAC auf dieselben drei Bytes endet."""
        rows = self._db.execute(
            "SELECT * FROM device WHERE substr(mac, -8) = ? AND mac != ? AND ip != ''",
            (mac_tail(mac), normalize_mac(mac)),
        ).fetchall()
        return [self._row_to_device(r) for r in rows]

    def record_sighting(
        self,
        mac: str,
        *,
        ip: str,
        hostname: str,
        interface: str,
        online: bool,
        initial_status: Status = Status.UNKNOWN,
    ) -> tuple[Device, bool]:
        """Speichert eine Beobachtung. Gibt (Gerät, ist_neu) zurück."""
        mac = normalize_mac(mac)
        now = _now()
        with self._lock, self._db:
            existing = self._db.execute("SELECT mac FROM device WHERE mac = ?", (mac,)).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO device (mac, hostname, ip, interface, status, online, "
                    "random_mac, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mac,
                        hostname,
                        ip,
                        interface,
                        initial_status.value,
                        int(online),
                        int(is_random_mac(mac)),
                        now,
                        now,
                    ),
                )
                self._add_event(mac, "new", f"{hostname or '?'} ({ip or 'keine IP'})", now)
            else:
                self._db.execute(
                    "UPDATE device SET hostname = COALESCE(NULLIF(?, ''), hostname), "
                    "ip = COALESCE(NULLIF(?, ''), ip), "
                    "interface = COALESCE(NULLIF(?, ''), interface), online = ?, "
                    "last_seen = CASE WHEN ? THEN ? ELSE last_seen END WHERE mac = ?",
                    (hostname, ip, interface, int(online), int(online), now, mac),
                )
        device = self.get(mac)
        assert device is not None
        return device, existing is None

    def remove_offline(self) -> list[Device]:
        """Löscht alle Geräte, die gerade offline sind – außer gesperrten.

        Gesperrte bleiben, damit ihre Sperre beim Wiederauftauchen weiter gilt.
        """
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT * FROM device WHERE online = 0 AND status != ?", (Status.BLOCKED.value,)
            ).fetchall()
            removed = [self._row_to_device(r) for r in rows]
            now = _now()
            for device in removed:
                self._db.execute("DELETE FROM device WHERE mac = ?", (device.mac,))
                self._add_event(device.mac, "removed", device.name or device.hostname, now)
        return removed

    def set_status(self, mac: str, status: Status) -> Device | None:
        mac = normalize_mac(mac)
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE device SET status = ? WHERE mac = ?", (status.value, mac)
            )
            if cur.rowcount:
                self._add_event(mac, status.value, "", _now())
        return self.get(mac)

    def update_info(self, mac: str, *, name: str | None, notes: str | None) -> Device | None:
        mac = normalize_mac(mac)
        with self._lock, self._db:
            if name is not None:
                self._db.execute("UPDATE device SET name = ? WHERE mac = ?", (name, mac))
            if notes is not None:
                self._db.execute("UPDATE device SET notes = ? WHERE mac = ?", (notes, mac))
        return self.get(mac)

    def set_watched(self, mac: str, watched: bool) -> Device | None:
        mac = normalize_mac(mac)
        with self._lock, self._db:
            self._db.execute("UPDATE device SET watched = ? WHERE mac = ?", (int(watched), mac))
        return self.get(mac)

    def add_event(self, mac: str, type_: str, detail: str = "") -> None:
        with self._lock, self._db:
            self._add_event(normalize_mac(mac), type_, detail, _now())

    def _add_event(self, mac: str, type_: str, detail: str, timestamp: str) -> None:
        self._db.execute(
            "INSERT INTO event (mac, type, detail, timestamp) VALUES (?, ?, ?, ?)",
            (mac, type_, detail, timestamp),
        )

    def events(self, limit: int = 100) -> list[dict]:
        rows = self._db.execute(
            "SELECT mac, type, detail, timestamp FROM event ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
