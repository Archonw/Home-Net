import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import server  # noqa: E402
import wgeasy  # noqa: E402

USER, PASSWORD = "admin", "geheim:mit-doppelpunkt"


def iso(seconds_ago):
    return datetime.fromtimestamp(time.time() - seconds_ago, timezone.utc).isoformat().replace("+00:00", "Z")


class FakeWgEasy(BaseHTTPRequestHandler):
    """Enough of the WireGuard-Easy API (v15.5) for the module."""
    state = None

    def log_message(self, *args):
        pass

    def reply(self, code, payload=None, raw=None, content_type="application/json"):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        s = self.state
        split = urllib.parse.urlsplit(self.path)
        path, query = split.path, dict(urllib.parse.parse_qsl(split.query))
        if path == "/api/information":
            return self.reply(200, {"currentRelease": "v15.5.0", "latestRelease": {"version": "v15.6.0"},
                                    "updateAvailable": True, "insecure": True})
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            return self.reply(401, {"statusMessage": "Session failed"})
        if self.command != "GET" and self.headers.get("Origin") != s["origin"]:
            return self.reply(403, {"statusMessage": "Invalid request origin"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        s["calls"].append((self.command, path, body))
        parts = path.split("/")
        if path == "/api/client" and self.command == "GET":
            items = sorted(s["clients"].values(), key=lambda c: c["id"])
            if s.get("old"):
                return self.reply(200, items)
            page, size = int(query.get("page", 1)), int(query.get("pageSize", 25))
            return self.reply(200, {"clients": items[(page - 1) * size:page * size], "total": len(items),
                                    "page": page, "pageSize": size})
        if path == "/api/client" and self.command == "POST":
            new_id = max(s["clients"]) + 1
            s["clients"][new_id] = {"id": new_id, "name": body["name"], "enabled": True, "ipv4Address": f"10.8.0.{new_id + 1}",
                                    "createdAt": iso(0), "expiresAt": body["expiresAt"], "latestHandshakeAt": None,
                                    "transferRx": None, "transferTx": None}
            return self.reply(200, {"success": True, "clientId": new_id})
        if len(parts) >= 4 and parts[2] == "client":
            cid = int(parts[3])
            if cid not in s["clients"]:
                return self.reply(404, {"statusMessage": "Client not found"})
            rest = parts[4] if len(parts) > 4 else ""
            if self.command == "DELETE" and not rest:
                del s["clients"][cid]
                return self.reply(200, {"success": True})
            if self.command == "POST" and rest in ("enable", "disable"):
                s["clients"][cid]["enabled"] = rest == "enable"
                return self.reply(200, {"success": True})
            if self.command == "GET" and rest == "configuration":
                return self.reply(200, raw=f"[Interface]\nPrivateKey = test-{cid}\n\n[Peer]\nEndpoint = vpn.example.org:51820\n".encode(),
                                  content_type="application/octet-stream")
            if self.command == "GET" and rest == "qrcode.svg":
                return self.reply(200, raw=b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml")
        return self.reply(404, {"statusMessage": "Page not found"})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


def fresh_state(origin):
    clients = {
        1: {"id": 1, "name": "Handy Jan", "enabled": True, "ipv4Address": "10.8.0.2", "createdAt": iso(86400 * 30),
            "expiresAt": None, "latestHandshakeAt": iso(20), "transferRx": 1_500_000, "transferTx": 42_000_000},
        2: {"id": 2, "name": "laptop", "enabled": False, "ipv4Address": "10.8.0.3", "createdAt": iso(86400),
            "expiresAt": "2020-01-01", "latestHandshakeAt": iso(7200), "transferRx": 0, "transferTx": 0},
    }
    return {"calls": [], "origin": origin, "clients": clients}


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeWgEasy)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.fake_url = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.tmp = tempfile.TemporaryDirectory()
        server.ApiHandler.settings = server.Settings(cls.tmp.name)
        server.ApiHandler.wg = wgeasy.WgEasy(server.ApiHandler.settings.get)
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.api.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        cls.api.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        FakeWgEasy.state = fresh_state(self.fake_url)
        server.ApiHandler.settings.update({"url": self.fake_url, "username": USER, "password": PASSWORD})

    def call(self, method, path, body=None, role="owner", raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Home-Net-Role": role, "X-Home-Net-User": "Jan",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req) as resp:
                content = resp.read()
                return resp.status, content if raw else json.loads(content or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_overview(self):
        code, data = self.call("GET", "/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(data["error"], "")
        self.assertEqual(data["version"], "v15.5.0")
        self.assertTrue(data["update"])
        jan, laptop = data["clients"]
        self.assertEqual(jan["name"], "Handy Jan")
        self.assertTrue(jan["online"])
        self.assertEqual(jan["tx"], 42_000_000)
        self.assertFalse(laptop["online"])
        self.assertFalse(laptop["enabled"])
        self.assertTrue(laptop["expired"])
        self.assertNotIn("privateKey", json.dumps(data))

    def test_paging_and_old_list(self):
        for i in range(3, 160):
            FakeWgEasy.state["clients"][i] = dict(FakeWgEasy.state["clients"][2], id=i, name=f"c{i:03}")
        self.assertEqual(len(self.call("GET", "/api/overview")[1]["clients"]), 159)
        FakeWgEasy.state["old"] = True
        self.assertEqual(len(self.call("GET", "/api/overview")[1]["clients"]), 159)

    def test_create_and_config(self):
        code, data = self.call("POST", "/api/clients", {"name": "  Handy   Anna "})
        self.assertEqual(code, 201)
        self.assertEqual(data["name"], "Handy Anna")
        self.assertIn(("POST", "/api/client", {"name": "Handy Anna", "expiresAt": None}), FakeWgEasy.state["calls"])
        code, conf = self.call("GET", f"/api/clients/{data['id']}/config")
        self.assertEqual(code, 200)
        self.assertIn("[Interface]", conf["config"])
        code, svg = self.call("GET", f"/api/clients/{data['id']}/qrcode.svg", raw=True)
        self.assertEqual(code, 200)
        self.assertTrue(svg.startswith(b"<svg"))

    def test_duplicate_and_empty_names(self):
        self.assertEqual(self.call("POST", "/api/clients", {"name": "handy jan"})[0], 400)
        self.assertEqual(self.call("POST", "/api/clients", {"name": " "})[0], 400)

    def test_enable_disable_delete(self):
        self.assertEqual(self.call("PUT", "/api/clients/2", {"enabled": True})[0], 200)
        self.assertTrue(FakeWgEasy.state["clients"][2]["enabled"])
        self.assertEqual(self.call("PUT", "/api/clients/1", {"enabled": "nein"})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/clients/1")[0], 200)
        self.assertNotIn(1, FakeWgEasy.state["clients"])
        code, data = self.call("DELETE", "/api/clients/99")
        self.assertEqual(code, 400)
        self.assertIn("nicht gefunden", data["detail"])

    def test_only_managers(self):
        self.assertEqual(self.call("GET", "/api/overview", role="user")[0], 403)
        self.assertEqual(self.call("GET", "/api/clients/1/config", role="user")[0], 403)
        self.assertEqual(self.call("GET", "/health", role="user")[0], 200)

    def test_without_login(self):
        server.ApiHandler.settings.update({"password": ""})
        code, data = self.call("GET", "/api/overview")
        self.assertEqual(code, 200)
        self.assertFalse(data["configured"])

    def test_login_from_file(self):
        server.ApiHandler.settings.update({"username": "", "password": ""})
        Path(self.tmp.name, "login.json").write_text(json.dumps({"username": USER, "password": PASSWORD}))
        try:
            data = self.call("GET", "/api/overview")[1]
            self.assertTrue(data["configured"])
            self.assertEqual(data["error"], "")
            settings = self.call("GET", "/api/settings")[1]
            self.assertTrue(settings["password_set"])
            self.assertNotIn(PASSWORD, json.dumps(settings))
        finally:
            Path(self.tmp.name, "login.json").unlink()

    def test_wrong_password_is_explained(self):
        server.ApiHandler.settings.update({"password": "falsch"})
        data = self.call("GET", "/api/overview")[1]
        self.assertIn("Anmeldung", data["error"])

    def test_unreachable(self):
        server.ApiHandler.settings.update({"url": "http://127.0.0.1:9"})
        self.assertIn("nicht erreichbar", self.call("GET", "/api/overview")[1]["error"])

    def test_bad_settings(self):
        self.assertEqual(self.call("PUT", "/api/settings", {"url": "kein url"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/settings", {"username": "a:b"})[0], 400)

    def test_web_page(self):
        code, page = self.call("GET", "/", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"WireGuard", page)


class HelperTest(unittest.TestCase):
    def test_epoch(self):
        self.assertEqual(wgeasy.epoch(None), 0)
        self.assertEqual(wgeasy.epoch("1970-01-02"), 86400)
        self.assertEqual(wgeasy.epoch("1970-01-01T00:01:00.000Z"), 60)
        self.assertEqual(wgeasy.epoch("quatsch"), 0)


if __name__ == "__main__":
    unittest.main()
