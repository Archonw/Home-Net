from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from networkmonitor.api import create_app
from networkmonitor.config import Settings
from networkmonitor.fritzbox import Host
from networkmonitor.monitor import Monitor
from networkmonitor.store import Status, Store, is_random_mac, normalize_mac


class FakeRouter:
    def __init__(self, hosts: list[Host]) -> None:
        self.hosts = hosts
        self.blocked: set[str] = set()
        self.calls: list[tuple[str, bool]] = []

    def list_hosts(self) -> list[Host]:
        return list(self.hosts)

    def get_internet_access(self, ip: str) -> bool:
        return ip not in self.blocked

    def set_internet_access(self, ip: str, allowed: bool) -> None:
        self.calls.append((ip, allowed))
        if allowed:
            self.blocked.discard(ip)
        else:
            self.blocked.add(ip)

    def read_device_log(self) -> list[str]:
        return []


PHONE = Host("aa:bb:cc:00:00:01", "192.168.178.20", "handy", "802.11", True)
LAPTOP = Host("AA-BB-CC-00-00-02", "192.168.178.21", "laptop", "Ethernet", True)
STRANGER = Host("de:ad:be:ef:00:03", "192.168.178.99", "fremd", "802.11", True)


def make(settings: Settings | None = None, hosts=None):
    router = FakeRouter(hosts or [PHONE, LAPTOP])
    store = Store(":memory:")
    monitor = Monitor(router, store, settings or Settings(db_path=":memory:"))
    return router, store, monitor


def test_normalize_and_random_mac():
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert is_random_mac("DE:AD:BE:EF:00:03")  # 0xDE hat Bit 1 gesetzt
    assert not is_random_mac("AC:BB:CC:00:00:01")


def test_initial_scan_trusts_existing_devices():
    router, store, monitor = make()
    result = monitor.poll_once()
    assert result.new_devices == []
    assert {d.status for d in store.list()} == {Status.ALLOWED}
    assert router.calls == []


def test_new_device_after_initial_scan_is_reported():
    router, store, monitor = make()
    monitor.poll_once()
    router.hosts.append(STRANGER)
    result = monitor.poll_once()
    assert [d.mac for d in result.new_devices] == ["DE:AD:BE:EF:00:03"]
    assert store.get(STRANGER.mac).status == Status.UNKNOWN
    # ohne Quarantäne bleibt das Internet an
    assert router.calls == []
    # zweite Abfrage meldet es nicht erneut
    assert monitor.poll_once().new_devices == []


def test_quarantine_blocks_unknown_until_allowed():
    router, store, monitor = make(Settings(db_path=":memory:", quarantine_unknown=True))
    monitor.poll_once()
    router.hosts.append(STRANGER)
    monitor.poll_once()
    assert STRANGER.ip in router.blocked
    monitor.set_status(STRANGER.mac, Status.ALLOWED)
    assert STRANGER.ip not in router.blocked


def test_block_and_reapply_after_ip_change():
    router, store, monitor = make()
    monitor.poll_once()
    monitor.set_status(PHONE.mac, Status.BLOCKED)
    assert PHONE.ip in router.blocked
    router.hosts[0] = Host(PHONE.mac, "192.168.178.40", "handy", "802.11", True)
    monitor.poll_once()
    assert "192.168.178.40" in router.blocked


def test_router_error_for_one_device_does_not_stop_poll():
    router, store, monitor = make()
    monitor.poll_once()
    monitor.set_status(PHONE.mac, Status.BLOCKED)

    def boom(ip, allowed):
        raise RuntimeError("TR-064 kaputt")

    router.set_internet_access = boom
    router.hosts[0] = Host(PHONE.mac, "192.168.178.41", "handy", "802.11", True)
    router.hosts.append(STRANGER)
    result = monitor.poll_once()
    assert len(result.errors) == 1
    assert store.get(STRANGER.mac) is not None


@pytest.fixture
def client():
    router = FakeRouter([PHONE, LAPTOP])
    store = Store(":memory:")
    settings = Settings(db_path=":memory:", ui_password="geheim")
    app = create_app(settings, router=router, store=store, start_poller=False)
    app.state.monitor.poll_once()
    router.hosts.append(STRANGER)
    app.state.monitor.poll_once()
    with TestClient(app) as c:
        yield c, router


def test_api_requires_login(client):
    c, _ = client
    assert c.get("/api/devices").status_code == 401
    assert c.get("/api/devices", auth=("admin", "falsch")).status_code == 401
    assert c.get("/health").status_code == 200


def test_api_lists_and_blocks(client):
    c, router = client
    devices = c.get("/api/devices", auth=("admin", "geheim")).json()
    assert devices[0]["mac"] == "DE:AD:BE:EF:00:03"  # Unbekannte zuerst
    r = c.post("/api/devices/DE:AD:BE:EF:00:03/block", auth=("admin", "geheim"))
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert STRANGER.ip in router.blocked
    r = c.patch(
        "/api/devices/DE:AD:BE:EF:00:03", json={"name": "Nachbar"}, auth=("admin", "geheim")
    )
    assert r.json()["name"] == "Nachbar"
    missing = c.post("/api/devices/00:00:00:00:00:00/allow", auth=("admin", "geheim"))
    assert missing.status_code == 404
    assert c.get("/", auth=("admin", "geheim")).status_code == 200


def test_single_device_page(client):
    c, _ = client
    auth = ("admin", "geheim")
    assert c.get("/device/DE:AD:BE:EF:00:03").status_code == 401
    page = c.get("/device/DE:AD:BE:EF:00:03", auth=auth)
    assert page.status_code == 200 and "Erlauben" in page.text
    device = c.get("/api/devices/de-ad-be-ef-00-03", auth=auth).json()
    assert device["hostname"] == "fremd" and device["status"] == "unknown"
    assert c.get("/api/devices/00:00:00:00:00:00", auth=auth).status_code == 404


def test_new_devices_trigger_notifiers():
    router = FakeRouter([PHONE])
    sent = []

    class Recorder:
        def new_device(self, device):
            sent.append(device.mac)

    app = create_app(
        Settings(db_path=":memory:"),
        router=router,
        store=Store(":memory:"),
        start_poller=False,
        notifiers=[Recorder()],
    )
    app.state.monitor.poll_once()
    router.hosts.append(STRANGER)
    result = app.state.monitor.poll_once()
    app.state.notify_new(result.new_devices)
    assert sent == ["DE:AD:BE:EF:00:03"]


def test_remove_offline_keeps_blocked_and_reports_on_return():
    router, store, monitor = make()
    monitor.poll_once()
    router.hosts.append(STRANGER)
    monitor.poll_once()
    monitor.set_status(STRANGER.mac, Status.BLOCKED)
    # alle drei gehen offline, die FritzBox listet sie weiter als inaktiv
    router.hosts = [Host(h.mac, h.ip, h.hostname, h.interface, False) for h in router.hosts]
    monitor.poll_once()

    removed = store.remove_offline()
    assert {d.mac for d in removed} == {normalize_mac(PHONE.mac), normalize_mac(LAPTOP.mac)}
    assert [d.mac for d in store.list()] == [normalize_mac(STRANGER.mac)]

    # inaktive Einträge der FritzBox legen nichts neu an
    assert monitor.poll_once().new_devices == []
    assert len(store.list()) == 1

    # kommt das Handy wieder online, gilt es als neu und muss bestätigt werden
    router.hosts[0] = PHONE
    result = monitor.poll_once()
    assert [d.mac for d in result.new_devices] == [normalize_mac(PHONE.mac)]
    assert store.get(PHONE.mac).status == Status.UNKNOWN


def test_remove_offline_endpoint():
    router, store, monitor = make()
    monitor.poll_once()
    router.hosts = [PHONE, Host(LAPTOP.mac, LAPTOP.ip, "laptop", "Ethernet", False)]
    monitor.poll_once()
    settings = Settings(db_path=":memory:", ui_password="geheim")
    app = create_app(settings, router=router, store=store, start_poller=False)
    client = TestClient(app)
    client.auth = ("admin", "geheim")
    assert client.post("/api/devices/remove-offline").json() == {"removed": 1}
    assert [d["mac"] for d in client.get("/api/devices").json()] == [normalize_mac(PHONE.mac)]


def test_shared_assets_are_served(client):
    c, _ = client
    css = c.get("/assets/app.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    js = c.get("/assets/app.js")
    assert js.status_code == 200 and "NM" in js.text
    assert c.get("/assets/../api.py").status_code == 404
    assert c.get("/assets/geheim.txt").status_code == 404
