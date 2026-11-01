from __future__ import annotations

import json

from networkmonitor.events import OFFLINE_AFTER, ROUTER_AFTER, HubEvents
from networkmonitor.store import Store


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def setup(tmp_path):
    clock = Clock()
    return HubEvents(str(tmp_path), clock), clock


def sent(tmp_path) -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted(tmp_path.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    ]


def watched_device(online=True):
    store = Store(":memory:")
    store.record_sighting(
        "de:ad:be:ef:00:03", ip="1.2.3.4", hostname="kamera", interface="802.11", online=online
    )
    return store, store.set_watched("de:ad:be:ef:00:03", True)


def test_watched_flag_survives_and_defaults_off():
    store, device = watched_device()
    assert device.watched is True
    assert (
        store.record_sighting("aa:ad:be:ef:00:04", ip="", hostname="", interface="", online=True)[
            0
        ].watched
        is False
    )


def test_short_absence_stays_silent_long_one_reports_once_and_recovery(tmp_path):
    hub, clock = setup(tmp_path)
    store, device = watched_device()
    hub.check_devices([device])
    device.online = False
    hub.check_devices([device])
    clock.now += OFFLINE_AFTER - 1
    hub.check_devices([device])
    assert sent(tmp_path) == []
    clock.now += 2
    hub.check_devices([device])
    hub.check_devices([device])
    assert [e["title"] for e in sent(tmp_path)] == ["kamera ist offline"]
    assert sent(tmp_path)[0]["link"] == f"device/{device.mac}"
    device.online = True
    hub.check_devices([device])
    assert len(sent(tmp_path)) == 2
    assert {e["title"] for e in sent(tmp_path)} == {
        "kamera ist offline",
        "kamera ist wieder online",
    }


def test_unwatched_device_and_offline_at_start_stay_silent(tmp_path):
    hub, clock = setup(tmp_path)
    _, device = watched_device(online=False)
    hub.check_devices([device])
    clock.now += OFFLINE_AFTER * 2
    hub.check_devices([device])
    device.watched = False
    device.online = True
    hub.check_devices([device])
    assert sent(tmp_path) == []


def test_router_reported_after_a_while_and_when_back(tmp_path):
    hub, clock = setup(tmp_path)
    hub.router_failed("weg")
    clock.now += ROUTER_AFTER - 1
    hub.router_failed("weg")
    hub.router_ok()
    hub.router_failed("weg")
    assert sent(tmp_path) == []
    clock.now += ROUTER_AFTER
    hub.router_failed("weg")
    hub.router_failed("weg")
    assert [e["title"] for e in sent(tmp_path)] == ["FritzBox nicht erreichbar"]
    hub.router_ok()
    assert len(sent(tmp_path)) == 2


def test_without_spool_nothing_happens():
    HubEvents("").send("router_down", "x")
