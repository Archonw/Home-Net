"""App updates live in the gateway, so phones get them with or without the network monitor."""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "core" / "gateway"))

import appupdate  # noqa: E402
import home_net  # noqa: E402
import server as gateway  # noqa: E402
from appupdate import AppFolder, AppUpdates, RateLimited, ReleaseCache, update_from_github  # noqa: E402

APK = b"PK\x03\x04fake-apk"


class FakeResponse:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self.body


def release(number, **extra):
    return {"tag_name": f"app-{number}",
            "assets": [{"name": "netzwerkmonitor.apk", "url": f"https://api.github.com/asset/{number}"}], **extra}


def fake_github(requests):
    # Same order as GitHub: not sorted by number
    releases = [release(8), release(12), release(13, prerelease=True), release(10)]

    def urlopen(req, timeout):
        requests.append(req)
        if "/releases?" in req.full_url:
            return FakeResponse(json.dumps(releases).encode())
        return FakeResponse(APK)
    return mock.patch.object(appupdate.urllib.request, "urlopen", urlopen)


def limit_error(req):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 1200)}
    return urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", headers, None)


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_folder_picks_highest_version_and_save_removes_old(self):
        folder = AppFolder(self.dir)
        (self.dir / "netzwerkmonitor-0.1.9.apk").write_bytes(APK)
        (self.dir / "netzwerkmonitor-0.1.10.apk").write_bytes(APK)
        (self.dir / "anderes.apk").write_bytes(APK)
        self.assertEqual(folder.latest().version, "0.1.10")
        self.assertEqual(folder.latest().version_code, 10)
        folder.save("0.1.11", APK)
        self.assertEqual([f.version for f in folder.files()], ["0.1.11"])

    def test_update_from_github_loads_highest_release(self):
        requests = []
        folder = AppFolder(self.dir)
        folder.save("0.1.11", APK)
        with fake_github(requests):
            self.assertEqual(update_from_github(folder, "Archonw/Home-Net", "tok"), "Neue Version 0.1.12 geladen")
            self.assertEqual(folder.latest().version, "0.1.12")
            self.assertTrue(requests[0].full_url.startswith("https://api.github.com/repos/Archonw/Home-Net/releases?"))
            self.assertEqual(requests[1].full_url, "https://api.github.com/asset/12")
            # The token only goes to GitHub itself, not to the redirect to the download storage
            self.assertEqual(requests[1].unredirected_hdrs["Authorization"], "Bearer tok")
            self.assertNotIn("Authorization", requests[1].headers)
            requests.clear()
            self.assertEqual(update_from_github(folder, "Archonw/Home-Net", "tok"), "Aktuell: 0.1.12")
            self.assertEqual(len(requests), 1)

    def test_missing_repo_explains_token(self):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        with mock.patch.object(appupdate.urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(ValueError, "Token gespeichert"):
                update_from_github(AppFolder(self.dir), "Archonw/Home-Net")

    def test_rate_limit_waits_until_reset(self):
        calls = []

        def urlopen(req, timeout):
            calls.append(req)
            raise limit_error(req)
        cache = ReleaseCache()
        with mock.patch.object(appupdate.urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(RateLimited, "in etwa 20 Minuten"):
                update_from_github(AppFolder(self.dir), "Archonw/Home-Net", cache=cache)
            with self.assertRaises(RateLimited):
                update_from_github(AppFolder(self.dir), "Archonw/Home-Net", cache=cache)
        self.assertEqual(len(calls), 1)

    def test_other_403_is_no_rate_limit(self):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with mock.patch.object(appupdate.urllib.request, "urlopen", urlopen):
            with self.assertRaises(urllib.error.HTTPError):
                update_from_github(AppFolder(self.dir), "Archonw/Home-Net", cache=ReleaseCache())

    def test_unchanged_list_uses_etag(self):
        sent = []

        def urlopen(req, timeout):
            sent.append(req)
            if req.get_header("If-none-match") == '"abc"':
                raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
            return FakeResponse(json.dumps([release(12)]).encode(), {"ETag": '"abc"'})
        cache = ReleaseCache()
        with mock.patch.object(appupdate.urllib.request, "urlopen", urlopen):
            self.assertEqual(appupdate.fetch_latest_release("Archonw/Home-Net", cache=cache),
                             ("0.1.12", "https://api.github.com/asset/12"))
            self.assertEqual(appupdate.fetch_latest_release("Archonw/Home-Net", cache=cache)[0], "0.1.12")
        self.assertEqual(sent[1].get_header("If-none-match"), '"abc"')


class Settings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.apps = AppUpdates(self.dir)

    def test_defaults_and_saving(self):
        self.assertEqual(self.apps.public_settings(), {"repo": "Archonw/Home-Net", "token_set": False})
        self.assertEqual(self.apps.save({"token": " ghp_x "}), {"repo": "Archonw/Home-Net", "token_set": True})
        self.assertEqual(self.apps.settings()["token"], "ghp_x")
        self.assertEqual(oct(Path(self.dir, "app.json").stat().st_mode)[-3:], "600")
        # An empty field keeps the token, clear_token drops it
        self.apps.save({"repo": "Archonw/Home-Net", "token": ""})
        self.assertEqual(self.apps.settings()["token"], "ghp_x")
        self.apps.save({"clear_token": True})
        self.assertEqual(self.apps.settings()["token"], "")

    def test_repo_is_validated_and_empty_turns_search_off(self):
        with self.assertRaises(ValueError):
            self.apps.save({"repo": "https://evil/x"})
        self.apps.save({"repo": ""})
        self.assertEqual(self.apps.check()["message"], "Automatische Suche aus")

    def test_check_reports_limit_reuses_nothing_and_new_token_lifts_block(self):
        self.apps.folder.save("0.1.12", APK)
        calls = []

        def urlopen(req, timeout):
            calls.append(req)
            raise limit_error(req)
        with mock.patch.object(appupdate.urllib.request, "urlopen", urlopen):
            body = self.apps.check(manual=True)
            self.assertIs(body["ok"], False)
            self.assertIn("Abfrage-Limit", body["message"])
            self.assertEqual(body["version"], "0.1.12")
            self.apps.check(manual=True)
            self.assertEqual(len(calls), 1)
            self.apps.save({"token": "neu"})
            self.apps.check(manual=True)
            self.assertEqual(len(calls), 2)

    def test_manual_check_right_after_success_asks_github_once(self):
        requests = []
        with fake_github(requests), mock.patch.object(appupdate.time, "monotonic", lambda: 5.0):
            self.assertEqual(self.apps.check(manual=True)["message"], "Neue Version 0.1.12 geladen")
            n = len(requests)
            self.assertEqual(self.apps.check(manual=True)["version"], "0.1.12")
            self.assertEqual(len(requests), n)


class GatewayRoutes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        gateway.CONFIG_DIR = self.dir.name
        gateway.Handler.limiter = gateway.Limiter()
        Path(self.dir.name, "gateway.json").write_text(json.dumps({"owner": "chef", "modules": []}))
        Path(self.dir.name, "auth.json").write_text(json.dumps(home_net.password_record("geheim123", rounds=1000)))
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.addCleanup(self.gw.shutdown)
        self.base = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.folder = gateway.app_updates().folder

    def call(self, path, method="GET", user=None, body=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if user:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:geheim123".encode()).decode()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_download_needs_no_login(self):
        self.assertIn(b"noch keine App", self.call("/app")[1])
        self.assertEqual(self.call("/app/latest.json")[0], 404)
        self.folder.save("0.1.42", APK)
        self.assertIn(b'href="/app/netzwerkmonitor-0.1.42.apk"', self.call("/app")[1])
        self.assertEqual(json.loads(self.call("/app/latest.json")[1]),
                         {"version": "0.1.42", "version_code": 42, "url": "/app/netzwerkmonitor-0.1.42.apk"})
        status, data, headers = self.call("/app/netzwerkmonitor-0.1.42.apk")
        self.assertEqual((status, data), (200, APK))
        self.assertEqual(headers["Content-Type"], "application/vnd.android.package-archive")
        self.assertEqual(self.call("/app/netzwerkmonitor-0.1.2.apk")[0], 404)
        self.assertEqual(self.call("/app/..%2Fapp.json")[0], 404)

    def test_old_app_addresses_under_the_monitor_still_work(self):
        self.folder.save("0.1.42", APK)
        self.assertEqual(json.loads(self.call("/m/netzwerkmonitor/app/latest.json")[1])["version"], "0.1.42")
        self.assertEqual(self.call("/m/netzwerkmonitor/app/netzwerkmonitor-0.1.42.apk")[1], APK)
        with mock.patch.object(gateway.app_updates(), "check", lambda manual=False: {"ok": True}):
            self.assertEqual(self.call("/m/netzwerkmonitor/api/app/check", "POST", "chef")[0], 200)

    def test_check_and_settings_need_an_admin(self):
        store = gateway.security()
        owner = store.user_by_name("chef")
        store.create_user(owner, {"name": "kind", "password": "geheim123", "role": "user"})
        self.assertEqual(self.call("/api/app/check", "POST")[0], 401)
        self.assertEqual(self.call("/api/app/check", "POST", "kind")[0], 403)
        self.assertEqual(json.loads(self.call("/api/me", user="kind")[1])["can_manage"], False)
        self.assertEqual(json.loads(self.call("/api/me", user="chef")[1])["can_manage"], True)
        self.assertEqual(self.call("/api/app", user="kind")[0], 200)
        self.assertEqual(self.call("/auth/app", user="kind")[0], 403)
        status, data, _ = self.call("/auth/app", "PUT", "chef", {"token": "ghp_x"})
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["token_set"])
        self.assertNotIn(b"ghp_x", self.call("/auth/app", user="chef")[1])


if __name__ == "__main__":
    unittest.main()
