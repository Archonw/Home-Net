from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_monitor import PHONE, FakeRouter

from networkmonitor.api import DEFAULT_PASSWORD, create_app
from networkmonitor.config import Settings
from networkmonitor.store import Store

DEFAULT = ("admin", DEFAULT_PASSWORD)


@pytest.fixture
def fresh():
    """Neuinstallation: keine .env, nichts in der Datenbank."""
    store = Store(":memory:")
    app = create_app(Settings(db_path=":memory:"), store=store, start_poller=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c, app, store


def test_default_password_only_opens_setup(fresh):
    c, _, _ = fresh
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("admin", "falsch")).status_code == 401
    r = c.get("/", auth=DEFAULT)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert c.get("/settings", auth=DEFAULT).headers["location"] == "/setup"
    assert c.get("/api/devices", auth=DEFAULT).status_code == 403
    assert c.put("/api/settings", auth=DEFAULT, json={"fritz_user": "x"}).status_code == 403
    page = c.get("/setup", auth=DEFAULT)
    assert page.status_code == 200 and "Neues Passwort" in page.text


def test_setup_password_rules(fresh):
    c, _, _ = fresh
    assert c.post("/api/setup/password", json={"password": "neu"}).status_code == 401
    short = c.post("/api/setup/password", auth=DEFAULT, json={"password": "kurz"})
    assert short.status_code == 400
    same = c.post("/api/setup/password", auth=DEFAULT, json={"password": DEFAULT_PASSWORD})
    assert same.status_code == 400


def test_setup_then_settings(fresh):
    c, app, store = fresh
    r = c.post("/api/setup/password", auth=DEFAULT, json={"password": "mein-passwort"})
    assert r.status_code == 200
    auth = ("admin", "mein-passwort")
    # Standardpasswort gilt nicht mehr, das neue ist gespeichert
    assert c.get("/settings", auth=DEFAULT).status_code == 401
    assert c.get("/settings", auth=auth).status_code == 200
    assert store.get_config()["ui_password"] == "mein-passwort"
    assert c.get("/setup", auth=auth).headers["location"] == "/settings"
    again = c.post("/api/setup/password", auth=auth, json={"password": "noch-eins-1"})
    assert again.status_code == 409

    # FritzBox fehlt noch → keine Abfrage; nach dem Eintragen ist sie eingerichtet
    rt = app.state.runtime
    assert rt.fritzbox_missing
    c.put("/api/settings", auth=auth, json={"fritz_user": "nm", "fritz_password": "fb"})
    assert not rt.fritzbox_missing


def test_password_cannot_be_removed_or_too_short():
    store = Store(":memory:")
    settings = Settings(db_path=":memory:", ui_password="geheim12")
    app = create_app(settings, router=FakeRouter([PHONE]), store=store, start_poller=False)
    c = TestClient(app)
    c.auth = ("admin", "geheim12")
    assert c.put("/api/settings", json={"ui_password": None}).status_code == 400
    assert c.put("/api/settings", json={"ui_password": "kurz"}).status_code == 400
    assert c.get("/api/settings").status_code == 200


def test_env_password_skips_setup():
    app = create_app(
        Settings(db_path=":memory:", ui_password="aus-env"),
        router=FakeRouter([PHONE]),
        store=Store(":memory:"),
        start_poller=False,
    )
    c = TestClient(app, follow_redirects=False)
    assert c.get("/", auth=("admin", "aus-env")).status_code == 200
    assert c.get("/setup", auth=("admin", "aus-env")).status_code == 303
