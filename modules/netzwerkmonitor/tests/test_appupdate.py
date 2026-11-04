from __future__ import annotations

import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient
from test_monitor import PHONE, FakeRouter

from networkmonitor import appupdate
from networkmonitor.api import create_app
from networkmonitor.appupdate import AppFolder, RateLimited, ReleaseCache, update_from_github
from networkmonitor.config import Settings
from networkmonitor.store import Store

AUTH = ("admin", "geheim")
APK = b"PK\x03\x04fake-apk"


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        db_path=":memory:", ui_password="geheim", fritz_password="fb", app_dir=str(tmp_path)
    )
    app = create_app(
        settings, router=FakeRouter([PHONE]), store=Store(":memory:"), start_poller=False
    )
    with TestClient(app) as c:
        yield c, AppFolder(tmp_path)


def test_folder_picks_highest_version_and_save_removes_old(tmp_path):
    folder = AppFolder(tmp_path)
    (tmp_path / "netzwerkmonitor-0.1.9.apk").write_bytes(APK)
    (tmp_path / "netzwerkmonitor-0.1.10.apk").write_bytes(APK)
    (tmp_path / "anderes.apk").write_bytes(APK)
    assert folder.latest().version == "0.1.10"
    assert folder.latest().version_code == 10
    folder.save("0.1.11", APK)
    assert [f.version for f in folder.files()] == ["0.1.11"]


def test_download_page_needs_no_login(env):
    c, folder = env
    assert "noch keine App" in c.get("/app").text
    assert c.get("/app/latest.json").status_code == 404
    folder.save("0.1.42", APK)
    page = c.get("/app").text
    assert 'href="/app/netzwerkmonitor-0.1.42.apk"' in page
    assert c.get("/app/latest.json").json() == {
        "version": "0.1.42",
        "version_code": 42,
        "url": "/app/netzwerkmonitor-0.1.42.apk",
    }
    r = c.get("/app/netzwerkmonitor-0.1.42.apk")
    assert r.status_code == 200 and r.content == APK
    assert r.headers["content-type"] == "application/vnd.android.package-archive"


def test_download_rejects_other_files(env):
    c, folder = env
    folder.save("0.1.1", APK)
    assert c.get("/app/netzwerkmonitor-0.1.2.apk").status_code == 404
    assert c.get("/app/..%2Fnetworkmonitor.db").status_code == 404


def test_status_requires_login(env):
    c, _ = env
    assert c.get("/api/app").status_code == 401
    assert c.post("/api/app/check").status_code == 401


def test_repo_setting_is_validated(env):
    c, _ = env
    bad = c.put("/api/settings", auth=AUTH, json={"app_update_repo": "https://evil/x"})
    assert bad.status_code == 400
    ok = c.put("/api/settings", auth=AUTH, json={"app_update_repo": ""})
    assert ok.status_code == 200
    assert c.post("/api/app/check", auth=AUTH).json()["message"] == "Automatische Suche aus"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self.body

    def __iter__(self):
        return iter([self.body])


def fake_github(monkeypatch, requests: list):
    def release(number: int, **extra) -> dict:
        return {
            "tag_name": f"app-{number}",
            "assets": [
                {"name": "netzwerkmonitor.apk", "url": f"https://api.github.com/asset/{number}"}
            ],
            **extra,
        }

    # Reihenfolge wie bei GitHub: nicht nach Nummer sortiert
    releases = [release(8), release(12), release(13, prerelease=True), release(10)]

    def urlopen(req, timeout):
        requests.append(req)
        if "/releases?" in req.full_url:
            return FakeResponse(json.dumps(releases).encode())
        return FakeResponse(APK)

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)


def test_update_from_github_loads_highest_release(tmp_path, monkeypatch):
    requests: list = []
    fake_github(monkeypatch, requests)
    folder = AppFolder(tmp_path)
    folder.save("0.1.11", APK)
    assert (
        update_from_github(folder, "Archonw/Networkmonitor", "tok") == "Neue Version 0.1.12 geladen"
    )
    assert folder.latest().version == "0.1.12"
    assert requests[0].full_url.startswith(
        "https://api.github.com/repos/Archonw/Networkmonitor/releases?"
    )
    assert requests[1].full_url == "https://api.github.com/asset/12"
    # Token nur an GitHub selbst, nicht an die Weiterleitung zum Download
    assert requests[1].unredirected_hdrs["Authorization"] == "Bearer tok"
    assert "Authorization" not in requests[1].headers

    requests.clear()
    assert update_from_github(folder, "Archonw/Networkmonitor", "tok") == "Aktuell: 0.1.12"
    assert len(requests) == 1


def test_missing_repo_explains_token(tmp_path, monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)
    with pytest.raises(ValueError, match="Token gespeichert"):
        update_from_github(AppFolder(tmp_path), "Archonw/Networkmonitor")


def limit_error(req):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 1200)}
    return urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", headers, None)


def test_rate_limit_waits_until_reset(tmp_path, monkeypatch):
    calls: list = []

    def urlopen(req, timeout):
        calls.append(req)
        raise limit_error(req)

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)
    cache = ReleaseCache()
    with pytest.raises(RateLimited, match="in etwa 20 Minuten"):
        update_from_github(AppFolder(tmp_path), "Archonw/Home-Net", cache=cache)
    # Bis zum Ende der Sperre fragt der Monitor GitHub nicht noch einmal
    with pytest.raises(RateLimited):
        update_from_github(AppFolder(tmp_path), "Archonw/Home-Net", cache=cache)
    assert len(calls) == 1


def test_other_403_is_no_rate_limit(tmp_path, monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        update_from_github(AppFolder(tmp_path), "Archonw/Home-Net", cache=ReleaseCache())


def test_unchanged_list_uses_etag(tmp_path, monkeypatch):
    releases = [
        {
            "tag_name": "app-12",
            "assets": [{"name": "netzwerkmonitor.apk", "url": "https://api.github.com/asset/12"}],
        }
    ]
    sent: list = []

    def urlopen(req, timeout):
        sent.append(req)
        if req.get_header("If-none-match") == '"abc"':
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        resp = FakeResponse(json.dumps(releases).encode())
        resp.headers = {"ETag": '"abc"'}
        return resp

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)
    cache = ReleaseCache()
    assert appupdate.fetch_latest_release("Archonw/Home-Net", cache=cache) == (
        "0.1.12",
        "https://api.github.com/asset/12",
    )
    assert appupdate.fetch_latest_release("Archonw/Home-Net", cache=cache)[0] == "0.1.12"
    assert sent[1].get_header("If-none-match") == '"abc"'


def test_check_endpoint_reports_limit_and_reuses_recent_result(env, monkeypatch):
    c, folder = env
    folder.save("0.1.12", APK)
    calls: list = []

    def urlopen(req, timeout):
        calls.append(req)
        raise limit_error(req)

    monkeypatch.setattr(appupdate.urllib.request, "urlopen", urlopen)
    body = c.post("/api/app/check", auth=AUTH).json()
    assert body["ok"] is False and "Abfrage-Limit" in body["message"]
    assert body["version"] == "0.1.12"
    c.post("/api/app/check", auth=AUTH)
    assert len(calls) == 1
    # Ein neues Token hebt die Sperre auf
    assert c.put("/api/settings", auth=AUTH, json={"app_update_token": "neu"}).status_code == 200
    c.post("/api/app/check", auth=AUTH)
    assert len(calls) == 2


def test_manual_check_right_after_success_asks_github_once(env, monkeypatch):
    c, folder = env
    requests: list = []
    fake_github(monkeypatch, requests)
    assert c.post("/api/app/check", auth=AUTH).json()["message"] == "Neue Version 0.1.12 geladen"
    n = len(requests)
    assert c.post("/api/app/check", auth=AUTH).json()["version"] == "0.1.12"
    assert len(requests) == n
