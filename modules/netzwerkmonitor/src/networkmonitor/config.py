"""Konfiguration aus Umgebungsvariablen."""

from __future__ import annotations

import os
import re
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
    # Benachrichtigungskanäle, z. B. ("telegram",) oder ("telegram", "homeassistant")
    notify: tuple[str, ...] = ()
    public_url: str = "http://localhost:8085/"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ha_url: str = ""
    ha_token: str = ""
    ha_notify_service: str = ""
    ntfy_url: str = ""
    ntfy_topic: str = ""
    ntfy_token: str = ""
    # Android-App zum Herunterladen (Obtainium): Ordner und GitHub-Quelle für neue Versionen
    app_dir: str = "/data/app"
    app_update_repo: str = "Archonw/Home-Net"
    app_update_token: str = ""

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
            notify=tuple(n.strip().lower() for n in env.get("NOTIFY", "").split(",") if n.strip()),
            public_url=env.get("PUBLIC_URL", cls.public_url),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            ha_url=env.get("HA_URL", ""),
            ha_token=env.get("HA_TOKEN", ""),
            ha_notify_service=env.get("HA_NOTIFY_SERVICE", ""),
            ntfy_url=env.get("NTFY_URL", ""),
            ntfy_topic=env.get("NTFY_TOPIC", ""),
            ntfy_token=env.get("NTFY_TOKEN", ""),
            app_dir=env.get("APP_DIR", cls.app_dir),
            app_update_repo=env.get("APP_UPDATE_REPO", cls.app_update_repo),
            app_update_token=env.get("APP_UPDATE_TOKEN", ""),
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
    "notify": "list",
    "public_url": "str",
    "ntfy_url": "str",
    "ntfy_topic": "str",
    "ntfy_token": "secret",
    "telegram_bot_token": "secret",
    "telegram_chat_id": "str",
    "ha_url": "str",
    "ha_token": "secret",
    "ha_notify_service": "str",
    "app_update_repo": "str",
    "app_update_token": "secret",
}
NOTIFY_CHANNELS = ("ntfy", "telegram", "homeassistant")


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
    if kind == "list":
        items = value if isinstance(value, list | tuple) else str(value).split(",")
        channels = tuple(str(i).strip().lower() for i in items if str(i).strip())
        unknown = [c for c in channels if c not in NOTIFY_CHANNELS]
        if unknown:
            raise ValueError(f"Unbekannter Benachrichtigungskanal: {', '.join(unknown)}")
        return channels
    text = str(value).strip()
    if field == "app_update_repo" and text and not re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        raise ValueError("Bitte als Besitzer/Repository angeben, z. B. Archonw/Home-Net")
    return text


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
        elif kind == "list":
            values[field] = list(value)
        else:
            values[field] = value
    return {"values": values, "secrets": secret_set}
