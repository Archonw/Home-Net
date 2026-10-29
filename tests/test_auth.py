from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from networkmonitor.auth import LOCK, MAX_FAILURES, LoginGuard

from networkmonitor.api import DEFAULT_PASSWORD, create_app
from networkmonitor.config import Settings
from networkmonitor.store import Store

AUTH = ("app", "geheim-123")
EXTERN = {"X-Forwarded-For": "203.0.113.7"}


@pytest.fixture
def client():
    settings = Settings(db_path=":memory:", ui_password=AUTH[1])
    app = create_app(settings, store=Store(":memory:"), start_poller=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_lockout_after_too_many_failures(client):
    for _ in range(MAX_FAILURES - 1):
        assert client.get("/api/devices", auth=("app", "falsch"), headers=EXTERN).status_code == 401
    assert client.get("/api/devices", auth=("app", "falsch"), headers=EXTERN).status_code == 401
    # Jetzt hilft auch das richtige Passwort nicht mehr
    locked = client.get("/api/devices", auth=AUTH, headers=EXTERN)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    # Andere Absender (z. B. das Heimnetz) sind nicht betroffen
    assert client.get("/api/devices", auth=AUTH).status_code == 200
    assert (
        client.get("/api/devices", auth=AUTH, headers={"X-Forwarded-For": "1.2.3.4"}).status_code
        == 200
    )


def test_proxy_entry_counts_not_spoofed_prefix(client):
    for i in range(MAX_FAILURES):
        spoofed = {"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"}
        client.get("/api/devices", auth=("app", "falsch"), headers=spoofed)
    assert client.get("/api/devices", auth=AUTH, headers=EXTERN).status_code == 429


def test_missing_credentials_are_no_failure(client):
    for _ in range(MAX_FAILURES * 2):
        assert client.get("/", headers=EXTERN).status_code == 401
    assert client.get("/api/devices", auth=AUTH, headers=EXTERN).status_code == 200


def test_success_resets_counter(client):
    for _ in range(MAX_FAILURES - 1):
        client.get("/api/devices", auth=("app", "falsch"), headers=EXTERN)
    assert client.get("/api/devices", auth=AUTH, headers=EXTERN).status_code == 200
    client.get("/api/devices", auth=("app", "falsch"), headers=EXTERN)
    assert client.get("/api/devices", auth=AUTH, headers=EXTERN).status_code == 200


def test_lock_expires():
    now = [1000.0]
    guard = LoginGuard(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        guard.failed("x")
    assert guard.retry_after("x") > 0
    now[0] += LOCK + 1
    assert guard.retry_after("x") == 0


def test_first_setup_not_from_outside():
    app = create_app(Settings(db_path=":memory:"), store=Store(":memory:"), start_poller=False)
    with TestClient(app, follow_redirects=False) as c:
        default = ("admin", DEFAULT_PASSWORD)
        assert c.get("/setup", auth=default, headers=EXTERN).status_code == 403
        r = c.post("/api/setup/password", auth=default, headers=EXTERN, json={"password": "x" * 10})
        assert r.status_code == 403
        assert c.get("/setup", auth=default).status_code == 200
