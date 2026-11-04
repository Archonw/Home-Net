from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_monitor import LAPTOP, PHONE, FakeRouter

from networkmonitor.api import create_app
from networkmonitor.config import Settings, apply_overrides, public_view
from networkmonitor.store import Store

AUTH = ("admin", "geheim")


@pytest.fixture
def env():
    store = Store(":memory:")
    settings = Settings(db_path=":memory:", ui_password="geheim", fritz_password="fb")
    app = create_app(settings, router=FakeRouter([PHONE, LAPTOP]), store=store, start_poller=False)
    with TestClient(app) as c:
        yield c, app, store


def test_public_view_hides_secrets():
    view = public_view(Settings(fritz_password="x", fritz_user="u"))
    assert "fritz_password" not in view["values"]
    assert view["secrets"]["fritz_password"] is True
    assert view["secrets"]["ui_password"] is False
    assert view["values"]["fritz_user"] == "u"


def test_settings_page_requires_login(env):
    c, _, _ = env
    assert c.get("/settings").status_code == 401
    assert c.get("/api/settings").status_code == 401
    assert c.get("/settings", auth=AUTH).status_code == 200


def test_update_applies_live_and_persists(env):
    c, app, store = env
    r = c.put(
        "/api/settings",
        auth=AUTH,
        json={
            "fritz_user": "monitor",
            "poll_interval": 60,
            "quarantine_unknown": True,
            "fritz_password": "",  # leer = unverändert
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"]["fritz_user"] == "monitor"
    rt = app.state.runtime
    assert rt.monitor.settings.quarantine_unknown is True
    # beim nächsten Start werden die gespeicherten Werte über die Umgebung gelegt
    restored = apply_overrides(Settings(), store.get_config())
    assert restored.poll_interval == 60 and restored.fritz_user == "monitor"


def test_secret_clear_and_password_change(env):
    c, app, _ = env
    c.put("/api/settings", auth=AUTH, json={"fritz_password": None})
    assert app.state.runtime.settings.fritz_password == ""
    c.put("/api/settings", auth=AUTH, json={"ui_password": "neu-geheim"})
    assert c.get("/api/settings", auth=AUTH).status_code == 401
    assert c.get("/api/settings", auth=("admin", "neu-geheim")).status_code == 200


def test_invalid_values_are_rejected(env):
    c, _, _ = env
    assert c.put("/api/settings", auth=AUTH, json={"poll_interval": 1}).status_code == 400
    assert c.put("/api/settings", auth=AUTH, json={"notify": ["sms"]}).status_code == 400
    assert c.put("/api/settings", auth=AUTH, json={"app_update_token": "x"}).status_code == 400
    assert c.put("/api/settings", auth=AUTH, json={"db_path": "/tmp/x"}).status_code == 400


def test_test_buttons(env):
    c, _, _ = env
    r = c.post("/api/settings/test-fritzbox", auth=AUTH).json()
    assert r["ok"] and "2 Geräte" in r["message"]
    assert c.post("/api/settings/test-notify", auth=AUTH).status_code in (404, 405)
