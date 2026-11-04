"""Konfiguration aus Umgebungsvariablen."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass(frozen=True)
class Settings:
    fritz_address: str = "192.168.178.1"
    fritz_user: str = ""
    fritz_password: str = ""
    ui_password: str = ""
    db_path: str = "/data/networkmonitor.db"
    poll_interval: int = 30
    trust_initial_devices: bool = True
    quarantine_unknown: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            fritz_address=env.get("FRITZ_ADDRESS", cls.fritz_address),
            fritz_user=env.get("FRITZ_USER", ""),
            fritz_password=env.get("FRITZ_PASSWORD", ""),
            ui_password=env.get("UI_PASSWORD", ""),
            db_path=env.get("DB_PATH", cls.db_path),
            poll_interval=int(env.get("POLL_INTERVAL", cls.poll_interval)),
            trust_initial_devices=_bool(env.get("TRUST_INITIAL_DEVICES"), True),
            quarantine_unknown=_bool(env.get("QUARANTINE_UNKNOWN"), False),
        )


# Felder, die über die Einstellungsseite geändert werden dürfen (Feld → Art)
EDITABLE: dict[str, str] = {
    "fritz_address": "str",
    "fritz_user": "str",
    "fritz_password": "secret",
    "ui_password": "secret",
    "poll_interval": "int",
    "trust_initial_devices": "bool",
    "quarantine_unknown": "bool",
}


def coerce(field: str, value) -> object:
    """Wandelt einen Wert aus Formular/Datenbank in den Typ des Feldes um."""
    kind = EDITABLE[field]
    if kind == "int":
        number = int(value)
        if field == "poll_interval" and not 10 <= number <= 3600:
            raise ValueError("Abfrageintervall muss zwischen 10 und 3600 Sekunden liegen")
        return number
    if kind == "bool":
        return value if isinstance(value, bool) else _bool(str(value), False)
    return str(value).strip()


def apply_overrides(settings: Settings, overrides: dict) -> Settings:
    """Legt gespeicherte Einstellungen über die Werte aus der Umgebung."""
    known = {f.name for f in fields(Settings)}
    values = {k: coerce(k, v) for k, v in overrides.items() if k in EDITABLE and k in known}
    return replace(settings, **values)


def public_view(settings: Settings) -> dict:
    """Einstellungen für die Oberfläche; Geheimnisse nur als "gesetzt"/"nicht gesetzt"."""
    values, secret_set = {}, {}
    for field, kind in EDITABLE.items():
        value = getattr(settings, field)
        if kind == "secret":
            secret_set[field] = bool(value)
        else:
            values[field] = value
    return {"values": values, "secrets": secret_set}
