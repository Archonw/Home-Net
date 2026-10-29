"""Benachrichtigungen über neue Geräte: Telegram und Home Assistant."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Protocol

from .config import Settings
from .store import Device, Status

log = logging.getLogger(__name__)


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 35) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def describe(device: Device) -> str:
    lines = [
        "Neues Gerät im Netz",
        f"Name: {device.hostname or 'unbekannt'}",
        f"MAC: {device.mac}" + (" (zufällige MAC)" if device.random_mac else ""),
        f"IP: {device.ip or 'keine'}",
        f"Verbindung: {device.interface or '?'}",
    ]
    return "\n".join(lines)


def device_url(public_url: str, mac: str) -> str:
    """Direktlink auf die Seite eines einzelnen Geräts."""
    return f"{public_url.rstrip('/')}/device/{mac}"


class Notifier(Protocol):
    def new_device(self, device: Device) -> None: ...


class TelegramNotifier:
    """Schickt eine Nachricht mit Buttons und verarbeitet die Antworten per getUpdates."""

    def __init__(self, token: str, chat_id: str, public_url: str, post=_post_json) -> None:
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.public_url = public_url
        self._post = post
        self._offset = 0

    def new_device(self, device: Device) -> None:
        keyboard = [
            [
                {"text": "✅ Erlauben", "callback_data": f"allow:{device.mac}"},
                {"text": "⛔ Sperren", "callback_data": f"block:{device.mac}"},
            ]
        ]
        link = device_url(self.public_url, device.mac)
        if link.startswith("https://"):
            # Telegram akzeptiert URL-Buttons nur mit öffentlich gültigen Adressen
            keyboard.append([{"text": "Öffnen", "url": link}])
        text = describe(device)
        if not link.startswith("https://"):
            text += f"\n\n{link}"
        self._post(
            f"{self.api}/sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )

    def process_updates(self, handle: Callable[[str, Status], Device | None]) -> None:
        """Holt Button-Klicks ab (Long-Polling) und führt sie aus."""
        result = self._post(
            f"{self.api}/getUpdates",
            {"offset": self._offset, "timeout": 25, "allowed_updates": ["callback_query"]},
        )
        for update in result.get("result", []):
            self._offset = update["update_id"] + 1
            query = update.get("callback_query")
            if query:
                self._handle_callback(query, handle)

    def _handle_callback(self, query: dict, handle: Callable[[str, Status], Device | None]) -> None:
        chat_id = str(query.get("message", {}).get("chat", {}).get("id", ""))
        action, _, mac = (query.get("data") or "").partition(":")
        if chat_id != self.chat_id or action not in {"allow", "block"}:
            self._answer(query["id"], "Nicht erlaubt")
            return
        status = Status.ALLOWED if action == "allow" else Status.BLOCKED
        try:
            device = handle(mac, status)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram-Aktion fehlgeschlagen: %s", exc)
            self._answer(query["id"], f"Fehler: {exc}")
            return
        if device is None:
            self._answer(query["id"], "Gerät nicht gefunden")
            return
        label = "erlaubt" if status == Status.ALLOWED else "gesperrt"
        self._answer(query["id"], f"{device.hostname or device.mac} {label}")
        message = query.get("message", {})
        self._post(
            f"{self.api}/editMessageText",
            {
                "chat_id": self.chat_id,
                "message_id": message.get("message_id"),
                "text": f"{message.get('text', '')}\n\n→ {label}",
            },
        )

    def _answer(self, query_id: str, text: str) -> None:
        self._post(f"{self.api}/answerCallbackQuery", {"callback_query_id": query_id, "text": text})


class HomeAssistantNotifier:
    """Benachrichtigung über einen notify-Dienst von Home Assistant (z. B. die Companion-App)."""

    def __init__(
        self, url: str, token: str, service: str, public_url: str, post=_post_json
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.service = service.removeprefix("notify.")
        self.public_url = public_url
        self._post = post

    def new_device(self, device: Device) -> None:
        link = device_url(self.public_url, device.mac)
        self._post(
            f"{self.url}/api/services/notify/{self.service}",
            {
                "title": "Neues Gerät im Netz",
                "message": describe(device).split("\n", 1)[1],
                "data": {
                    "url": link,
                    "clickAction": link,
                    "actions": [{"action": "URI", "title": "Öffnen", "uri": link}],
                },
            },
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10,
        )


APP_LINK = "netmon://device/{mac}"


class NtfyNotifier:
    """Veröffentlicht über einen ntfy-Server. Antippen öffnet das Gerät in der Android-App."""

    def __init__(self, url: str, topic: str, token: str = "", post=_post_json) -> None:
        self.url = url.rstrip("/")
        self.topic = topic
        self.token = token
        self._post = post

    def new_device(self, device: Device) -> None:
        link = APP_LINK.format(mac=device.mac)
        self._post(
            self.url,
            {
                "topic": self.topic,
                "title": "Neues Gerät im Netz",
                "message": describe(device).split("\n", 1)[1],
                "tags": ["warning"],
                "priority": 4,
                "click": link,
                "actions": [{"action": "view", "label": "Prüfen", "url": link}],
            },
            headers={"Authorization": f"Bearer {self.token}"} if self.token else None,
            timeout=10,
        )


def build_notifiers(settings: Settings) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for name in settings.notify:
        if name == "telegram":
            if settings.telegram_bot_token and settings.telegram_chat_id:
                notifiers.append(
                    TelegramNotifier(
                        settings.telegram_bot_token, settings.telegram_chat_id, settings.public_url
                    )
                )
            else:
                log.warning("NOTIFY=telegram, aber TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID fehlen")
        elif name in {"homeassistant", "ha"}:
            if settings.ha_url and settings.ha_token and settings.ha_notify_service:
                notifiers.append(
                    HomeAssistantNotifier(
                        settings.ha_url,
                        settings.ha_token,
                        settings.ha_notify_service,
                        settings.public_url,
                    )
                )
            else:
                log.warning("NOTIFY=homeassistant, aber HA_URL/HA_TOKEN/HA_NOTIFY_SERVICE fehlen")
        elif name == "ntfy":
            if settings.ntfy_url and settings.ntfy_topic:
                notifiers.append(
                    NtfyNotifier(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token)
                )
            else:
                log.warning("NOTIFY=ntfy, aber NTFY_URL/NTFY_TOPIC fehlen")
        else:
            log.warning("Unbekannter Benachrichtigungskanal: %s", name)
    return notifiers


def main() -> int:
    """Testnachricht an alle eingestellten Kanäle schicken: networkmonitor-notify-test"""
    settings = Settings.from_env()
    notifiers = build_notifiers(settings)
    if not notifiers:
        print("Keine Benachrichtigung eingerichtet (NOTIFY und Zugangsdaten prüfen).")
        return 1
    test = Device(
        mac="02:00:00:00:00:00",
        name="",
        hostname="Testgerät",
        ip="0.0.0.0",
        interface="Test",
        status=Status.UNKNOWN,
        online=True,
        random_mac=True,
        notes="",
        first_seen="",
        last_seen="",
    )
    ok = True
    for notifier in notifiers:
        try:
            notifier.new_device(test)
            print(f"OK: {type(notifier).__name__}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"FEHLER: {type(notifier).__name__}: {exc}")
    return 0 if ok else 1
