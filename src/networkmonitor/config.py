"""Konfiguration aus Umgebungsvariablen."""

from __future__ import annotations

import os
from dataclasses import dataclass


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
