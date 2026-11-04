from __future__ import annotations

import pytest

from networkmonitor.config import Settings, apply_overrides
from networkmonitor.lanscan import LanScan, home_networks, read_arp
from networkmonitor.monitor import BlockNotSupported, Monitor
from networkmonitor.store import Status, Store

ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "br0\t00000000\t01B2A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "br0\t00B2A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    "wg0\t0000080A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
)
ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.178.20   0x1         0x2         AA:BB:CC:00:00:01     *        br0\n"
    "192.168.178.21   0x1         0x0         00:00:00:00:00:00     *        br0\n"
    "192.168.178.22   0x1         0x0         aa:bb:cc:00:00:03     *        br0\n"
    "172.17.0.2       0x1         0x2         02:42:ac:11:00:02     *        docker0\n"
)


@pytest.fixture
def scan(tmp_path, monkeypatch):
    (tmp_path / "route").write_text(ROUTES)
    (tmp_path / "arp").write_text(ARP)
    s = LanScan(routes=tmp_path / "route", table=tmp_path / "arp", wait=0)
    monkeypatch.setattr(s, "_knock", lambda networks: None)
    monkeypatch.setattr(s, "_name", lambda ip: {"192.168.178.20": "handy"}.get(ip, ""))
    return s


def test_home_networks_skip_docker_vpn_and_default(tmp_path):
    (tmp_path / "route").write_text(ROUTES)
    assert [(i, str(n)) for i, n in home_networks(tmp_path / "route")] == [
        ("br0", "192.168.178.0/24")
    ]


def test_read_arp_skips_incomplete_without_mac(tmp_path):
    (tmp_path / "arp").write_text(ARP)
    assert [r[0] for r in read_arp(tmp_path / "arp")] == [
        "192.168.178.20",
        "192.168.178.22",
        "172.17.0.2",
    ]


def test_lists_only_home_devices(scan):
    hosts = scan.list_hosts()
    assert [(h.mac, h.ip, h.hostname, h.active) for h in hosts] == [
        ("aa:bb:cc:00:00:01", "192.168.178.20", "handy", True),
        ("aa:bb:cc:00:00:03", "192.168.178.22", "", False),
    ]


def test_monitor_reports_but_cannot_block(scan):
    store = Store(":memory:")
    monitor = Monitor(scan, store, Settings(db_path=":memory:", router="scan"))
    monitor.poll_once()
    assert store.get("aa:bb:cc:00:00:01") is not None
    assert not monitor.can_block
    with pytest.raises(BlockNotSupported):
        monitor.set_status("aa:bb:cc:00:00:01", Status.BLOCKED)
    assert monitor.set_status("aa:bb:cc:00:00:01", Status.ALLOWED).status == Status.ALLOWED


def test_router_choice_is_checked():
    assert apply_overrides(Settings(), {"router": "scan"}).router == "scan"
    with pytest.raises(ValueError):
        apply_overrides(Settings(), {"router": "openwrt"})


def test_api_tells_app_that_blocking_is_off(scan):
    from fastapi.testclient import TestClient

    from networkmonitor.api import create_app

    settings = Settings(db_path=":memory:", ui_password="geheim-123", router="scan")
    store = Store(":memory:")
    Monitor(scan, store, settings).poll_once()
    app = create_app(settings, router=scan, store=store, start_poller=False)
    with TestClient(app) as c:
        auth = ("app", "geheim-123")
        assert c.get("/api/me", auth=auth).json()["can_block"] is False
        r = c.post("/api/devices/aa:bb:cc:00:00:01/block", auth=auth)
        assert r.status_code == 409 and "FritzBox" in r.json()["detail"]
