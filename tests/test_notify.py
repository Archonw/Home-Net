from __future__ import annotations

from networkmonitor.config import Settings
from networkmonitor.notify import HomeAssistantNotifier, TelegramNotifier, build_notifiers
from networkmonitor.store import Status, Store


class FakePost:
    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.responses = responses or {}

    def __call__(self, url, payload, headers=None, timeout=35):
        self.calls.append((url, payload, headers))
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return {}


def make_device():
    store = Store(":memory:")
    device, _ = store.record_sighting(
        "de:ad:be:ef:00:03", ip="192.168.178.99", hostname="fremd", interface="802.11", online=True
    )
    return store, device


def test_telegram_sends_buttons():
    _, device = make_device()
    post = FakePost()
    TelegramNotifier("TOKEN", "42", "http://192.168.178.207:8085/", post=post).new_device(device)
    url, payload, _ = post.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload["chat_id"] == "42"
    assert "fremd" in payload["text"]
    assert "http://192.168.178.207:8085/device/DE:AD:BE:EF:00:03" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [
        "allow:DE:AD:BE:EF:00:03",
        "block:DE:AD:BE:EF:00:03",
    ]


def _callback(chat_id: int, data: str) -> dict:
    return {
        "result": [
            {
                "update_id": 7,
                "callback_query": {
                    "id": "q1",
                    "data": data,
                    "message": {"message_id": 5, "chat": {"id": chat_id}, "text": "Neues Gerät"},
                },
            }
        ]
    }


def test_telegram_button_blocks_device():
    store, device = make_device()
    post = FakePost({"getUpdates": _callback(42, f"block:{device.mac}")})
    notifier = TelegramNotifier("TOKEN", "42", "http://x/", post=post)
    notifier.process_updates(store.set_status)
    assert store.get(device.mac).status == Status.BLOCKED
    assert notifier._offset == 8
    assert any(url.endswith("editMessageText") for url, _, _ in post.calls)


def test_telegram_ignores_foreign_chat():
    store, device = make_device()
    post = FakePost({"getUpdates": _callback(666, f"allow:{device.mac}")})
    TelegramNotifier("TOKEN", "42", "http://x/", post=post).process_updates(store.set_status)
    assert store.get(device.mac).status == Status.UNKNOWN
    answer = [p for url, p, _ in post.calls if url.endswith("answerCallbackQuery")][0]
    assert answer["text"] == "Nicht erlaubt"


def test_home_assistant_notify():
    _, device = make_device()
    post = FakePost()
    HomeAssistantNotifier(
        "http://ha:8123/", "HATOKEN", "notify.mobile_app_jan", "http://nm/", post=post
    ).new_device(device)
    url, payload, headers = post.calls[0]
    assert url == "http://ha:8123/api/services/notify/mobile_app_jan"
    assert headers == {"Authorization": "Bearer HATOKEN"}
    assert payload["title"] == "Neues Gerät im Netz"
    assert payload["data"]["actions"][0]["uri"] == "http://nm/device/DE:AD:BE:EF:00:03"
    assert payload["data"]["clickAction"] == "http://nm/device/DE:AD:BE:EF:00:03"


def test_build_notifiers_selects_by_setting():
    full = dict(
        telegram_bot_token="t",
        telegram_chat_id="1",
        ha_url="http://ha",
        ha_token="h",
        ha_notify_service="mobile_app_jan",
    )
    assert build_notifiers(Settings(**full)) == []
    kinds = [
        type(n) for n in build_notifiers(Settings(notify=("telegram", "homeassistant"), **full))
    ]
    assert kinds == [TelegramNotifier, HomeAssistantNotifier]
    assert [type(n) for n in build_notifiers(Settings(notify=("ha",), **full))] == [
        HomeAssistantNotifier
    ]
    # fehlende Zugangsdaten → Kanal wird übersprungen statt abzustürzen
    assert build_notifiers(Settings(notify=("telegram",))) == []
