import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import pihole  # noqa: E402
import server  # noqa: E402

PASSWORD = "geheim"


def fake_state(total=1000, blocked=200, prefix="", clients=None):
    now = int(time.time()) // 600 * 600
    return {
        "password": PASSWORD, "blocking": "enabled", "remote": "v6.1.4", "timer": None, "logins": 0, "sids": set(), "calls": [],
        "summary": {"queries": {"total": total, "blocked": blocked, "percent_blocked": 100 * blocked / total,
                                "cached": total // 4, "forwarded": total // 2, "unique_domains": 50},
                    "clients": {"active": 3, "total": 5},
                    "gravity": {"domains_being_blocked": 123456, "last_update": now - 86400}},
        "blocked": [{"domain": f"{prefix}ads.example.com", "count": blocked // 2},
                    {"domain": "tracker.example.net", "count": blocked // 4}],
        "permitted": [{"domain": "example.org", "count": total // 3}],
        "clients": clients or [{"ip": "192.168.1.31", "name": "pixel", "count": total // 2},
                               {"ip": "192.168.1.40", "name": "tv", "count": total // 4}],
        "history": [{"timestamp": now - 600 * i, "total": total // 144, "blocked": blocked // 144,
                     "cached": 0, "forwarded": 0} for i in range(144)],
    }


class FakePihole(BaseHTTPRequestHandler):
    """Enough of the Pi-hole 6 API for the module."""
    state = None

    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        s = self.state
        split = urllib.parse.urlsplit(self.path)
        path, query = split.path, dict(urllib.parse.parse_qsl(split.query))
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        if path == "/api/auth" and self.command == "POST":
            if s["password"] and body.get("password") != s["password"]:
                return self.reply(401, {"session": {"valid": False, "sid": None}})
            s["logins"] += 1
            sid = f"sid{s['logins']}"
            s["sids"].add(sid)
            return self.reply(200, {"session": {"valid": True, "sid": sid if s["password"] else None, "validity": 1800}})
        if s["password"] and self.headers.get("X-FTL-SID") not in s["sids"]:
            return self.reply(401, {"error": {"key": "unauthorized"}})
        s["calls"].append((self.command, path, body))
        if path == "/api/dns/blocking":
            if self.command == "POST":
                s["blocking"] = "enabled" if body["blocking"] else "disabled"
                s["timer"] = body.get("timer")
            return self.reply(200, {"blocking": s["blocking"], "timer": s["timer"]})
        if path == "/api/info/version":
            return self.reply(200, {"version": {"core": {"local": {"version": "v6.1.4"}, "remote": {"version": s["remote"]}},
                                                "web": {"local": {"version": None}, "remote": {"version": None}},
                                                "ftl": {"local": {"version": "v6.2.3"}, "remote": {"version": "v6.2.3"}}}})
        if path == "/api/stats/summary":
            return self.reply(200, s["summary"])
        if path == "/api/stats/top_domains":
            items = s["blocked"] if query.get("blocked") == "true" else s["permitted"]
            return self.reply(200, {"domains": items})
        if path == "/api/stats/top_clients":
            return self.reply(200, {"clients": s["clients"]})
        if path == "/api/history":
            return self.reply(200, {"history": s["history"]})
        return self.reply(404, {"error": {"key": "not_found"}})

    do_GET = do_POST = handle_any


def start_fake(state):
    handler = type("Fake", (FakePihole,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.ApiHandler.settings = server.Settings(cls.tmp.name)
        server.ApiHandler.piholes = pihole.Piholes(server.ApiHandler.settings.get)
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        self.a, self.b = fake_state(), fake_state(500, 100, prefix="b-")
        self.fa, self.fb = start_fake(self.a), start_fake(self.b)
        self.addCleanup(self.fa.shutdown)
        self.addCleanup(self.fb.shutdown)
        server.ApiHandler.settings.update({"instances": [
            {"name": "Erster", "url": f"127.0.0.1:{self.fa.server_address[1]}", "password": PASSWORD},
            {"name": "Zweiter", "url": f"http://127.0.0.1:{self.fb.server_address[1]}/admin/", "password": PASSWORD},
        ]})

    def call(self, method, path, body=None, role="owner"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.api.server_address[1]}{path}", data=data, method=method,
                                     headers={"X-Home-Net-Role": role, "X-Home-Net-User": "test",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_overview(self):
        code, out = self.call("GET", "/api/overview", role="user")
        self.assertEqual(code, 200)
        self.assertTrue(out["configured"])
        self.assertFalse(out["manager"])
        self.assertEqual(out["blocking"], "enabled")
        self.assertEqual([i["name"] for i in out["instances"]], ["Erster", "Zweiter"])
        self.assertEqual(out["instances"][0]["version"], "v6.1.4")
        self.assertTrue(all(i["online"] for i in out["instances"]))

    def test_session_is_reused_and_renewed(self):
        self.call("GET", "/api/overview")
        self.call("GET", "/api/stats")
        self.assertEqual(self.a["logins"], 1)
        self.a["sids"].clear()  # session ran out on the Pi-hole
        code, _ = self.call("GET", "/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(self.a["logins"], 2)

    def test_pause_reaches_all_and_resume(self):
        code, out = self.call("POST", "/api/pause", {"seconds": 300}, role="user")
        self.assertEqual(code, 200)
        self.assertTrue(all(r["ok"] for r in out["instances"]))
        self.assertAlmostEqual(out["until"], time.time() + 300, delta=5)
        for s in (self.a, self.b):
            self.assertEqual((s["blocking"], s["timer"]), ("disabled", 300))
        _, overview = self.call("GET", "/api/overview")
        self.assertEqual(overview["blocking"], "disabled")
        code, _ = self.call("POST", "/api/resume", role="user")
        self.assertEqual(code, 200)
        self.assertEqual((self.a["blocking"], self.b["blocking"]), ("enabled", "enabled"))

    def test_pause_without_end_only_for_managers(self):
        code, _ = self.call("POST", "/api/pause", {}, role="user")
        self.assertEqual(code, 403)
        code, _ = self.call("POST", "/api/pause", {})
        self.assertEqual(code, 200)
        self.assertEqual((self.a["blocking"], self.a["timer"]), ("disabled", None))

    def test_pause_checks_duration(self):
        for bad in (0, -5, 999999, "300", True):
            code, _ = self.call("POST", "/api/pause", {"seconds": bad})
            self.assertEqual(code, 400, bad)

    def test_mixed_state(self):
        self.b["blocking"] = "disabled"
        _, out = self.call("GET", "/api/overview")
        self.assertEqual(out["blocking"], "mixed")

    def test_stats_are_added_up(self):
        code, out = self.call("GET", "/api/stats", role="user")
        self.assertEqual(code, 200)
        self.assertEqual((out["total"], out["blocked"]), (1500, 300))
        self.assertEqual(out["percent"], 20.0)
        self.assertEqual(out["answered"], 2)
        clients = {c["ip"]: c["count"] for c in out["top_clients"]}
        self.assertEqual(clients["192.168.1.31"], 750)
        domains = {d["domain"]: d["count"] for d in out["top_blocked"]}
        self.assertEqual(domains["tracker.example.net"], 75)
        self.assertEqual(domains["ads.example.com"], 100)
        self.assertEqual(len(out["history"]), 144)
        self.assertEqual(out["history"][-1]["total"], 1000 // 144 + 500 // 144)
        self.assertEqual(out["gravity"], 123456)

    def test_one_offline(self):
        self.fb.shutdown()
        self.fb.server_close()
        _, out = self.call("GET", "/api/overview")
        self.assertEqual([i["online"] for i in out["instances"]], [True, False])
        self.assertIn("Zweiter ist nicht erreichbar", out["instances"][1]["error"])
        self.assertEqual(out["blocking"], "enabled")
        _, stats = self.call("GET", "/api/stats")
        self.assertEqual((stats["answered"], stats["total"]), (1, 1000))
        code, pause = self.call("POST", "/api/pause", {"seconds": 30})
        self.assertEqual(code, 200)
        self.assertEqual([r["ok"] for r in pause["instances"]], [True, False])

    def test_wrong_password(self):
        self.a["password"] = "anders"
        _, out = self.call("GET", "/api/overview")
        self.assertIn("lehnt das Passwort ab", out["instances"][0]["error"])

    def test_without_password(self):
        self.a["password"] = ""
        server.ApiHandler.settings.update({"instances": [
            {"name": "Offen", "url": f"http://127.0.0.1:{self.fa.server_address[1]}", "password": ""}]})
        code, out = self.call("POST", "/api/pause", {"seconds": 10})
        self.assertEqual(code, 200)
        self.assertEqual(self.a["blocking"], "disabled")

    def test_settings_only_managers_and_keep_password(self):
        self.assertEqual(self.call("GET", "/api/settings", role="user")[0], 403)
        code, out = self.call("GET", "/api/settings", role="admin")
        self.assertEqual(code, 200)
        self.assertEqual(out["instances"][1]["url"], f"http://127.0.0.1:{self.fb.server_address[1]}")
        self.assertTrue(out["instances"][0]["password_set"])
        self.assertNotIn("password", json.dumps(out).replace("password_set", ""))
        # saved again without password: the old one stays
        items = [{"name": i["name"], "url": i["url"]} for i in out["instances"]]
        code, _ = self.call("PUT", "/api/settings", {"instances": items})
        self.assertEqual(code, 200)
        self.assertEqual(server.ApiHandler.settings.get()["instances"][0]["password"], PASSWORD)
        path = Path(self.tmp.name) / "settings.json"
        self.assertEqual(oct(path.stat().st_mode)[-3:], "600")

    def test_settings_validation(self):
        for bad in ({"instances": "x"}, {"instances": [{"url": "nicht da"}]},
                    {"instances": [{"url": "http://a"}, {"url": "a"}]},
                    {"instances": [{"url": f"http://h{i}"} for i in range(9)]}):
            self.assertEqual(self.call("PUT", "/api/settings", bad)[0], 400, bad)

    def test_not_configured(self):
        server.ApiHandler.settings.update({"instances": []})
        _, out = self.call("GET", "/api/overview")
        self.assertFalse(out["configured"])
        self.assertEqual(self.call("POST", "/api/pause", {"seconds": 30})[0], 400)

    def test_page_and_health(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.api.server_address[1]}/", timeout=5)
        self.assertIn(b"Pi-hole", req.read())
        self.assertEqual(self.call("GET", "/health", role="user"), (200, {"ok": True}))


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.events = Path(self.tmp.name) / "events"
        self.events.mkdir()
        server.EVENTS_DIR = str(self.events)
        self.addCleanup(setattr, server, "EVENTS_DIR", "")
        self.a = fake_state()
        self.fa = start_fake(self.a)
        self.addCleanup(self.fa.shutdown)
        self.settings = server.Settings(self.tmp.name)
        self.settings.update({"instances": [
            {"name": "Erster", "url": f"http://127.0.0.1:{self.fa.server_address[1]}", "password": PASSWORD},
            {"name": "Weg", "url": "http://127.0.0.1:9", "password": ""}]})
        self.watcher = server.Watcher(pihole.Piholes(self.settings.get), self.tmp.name)

    def sent(self):
        out = [json.loads(p.read_text()) for p in sorted(self.events.glob("*.json"))]
        for p in self.events.glob("*.json"):
            p.unlink()
        return [(e["type"], e["title"]) for e in out]

    def test_offline_only_after_some_checks_and_back_once(self):
        self.watcher.tick(now=1)
        self.watcher.tick(now=2)
        self.assertEqual(self.sent(), [])
        self.watcher.tick(now=3)
        self.assertEqual(self.sent(), [("pihole_offline", "Weg ist nicht erreichbar")])
        self.watcher.tick(now=4)
        self.assertEqual(self.sent(), [])
        # after a restart it is not reported again
        again = server.Watcher(self.watcher.piholes, self.tmp.name)
        for t in range(5):
            again.tick(now=10 + t)
        self.assertEqual(self.sent(), [])
        # it comes back
        items = self.settings.get()["instances"]
        self.settings.update({"instances": [items[0], {"name": "Weg", "url": items[0]["url"].replace("127.0.0.1", "localhost"),
                                                        "password": PASSWORD}]})
        again.tick(now=20)
        self.assertEqual(self.sent(), [])  # different address, old one forgotten
        self.assertEqual(again.down, set())

    def test_back_online(self):
        self.a_url = self.settings.get()["instances"][0]["url"]
        self.fa.shutdown()
        self.fa.server_close()
        for t in range(3):
            self.watcher.tick(now=t + 1)
        self.assertIn(("pihole_offline", "Erster ist nicht erreichbar"), self.sent())
        fb = ThreadingHTTPServer(("127.0.0.1", self.fa.server_address[1]), type("F", (FakePihole,), {"state": self.a}))
        threading.Thread(target=fb.serve_forever, daemon=True).start()
        self.addCleanup(fb.shutdown)
        self.watcher.tick(now=10)
        self.assertIn(("pihole_online", "Erster ist wieder erreichbar"), self.sent())

    def test_update_reported_once(self):
        self.watcher.tick(now=100000)
        self.assertNotIn("pihole_update", [k for k, _ in self.sent()])
        self.a["remote"] = "v6.2"
        self.watcher.tick(now=100001)  # not yet time for the next look
        self.assertEqual([k for k, _ in self.sent() if k == "pihole_update"], [])
        self.watcher.tick(now=100000 + server.UPDATE_EVERY)
        self.assertEqual([t for k, t in self.sent() if k == "pihole_update"], ["Update für Erster"])
        self.watcher.tick(now=100000 + 2 * server.UPDATE_EVERY)
        self.assertEqual([t for k, t in self.sent() if k == "pihole_update"], [])
        _, out = None, self.watcher.piholes.overview()
        self.assertEqual(out["instances"][0]["updates"], [{"part": "Core", "installed": "v6.1.4", "available": "v6.2"}])


class VersionTest(unittest.TestCase):
    def test_updates(self):
        self.assertEqual(pihole.updates({}), [])
        self.assertEqual(pihole.updates({"ftl": {"local": {"version": "v6.8"}, "remote": {"version": "v6.10"}},
                                         "core": {"local": {"version": None}, "remote": {"version": "v6.3"}},
                                         "web": {"local": {"version": "v6.6.1"}, "remote": {"version": "v6.6"}},
                                         "docker": {"local": "2025.11.0", "remote": "2025.12.1"}}),
                         [{"part": "FTL", "installed": "v6.8", "available": "v6.10"},
                          {"part": "Docker", "installed": "2025.11.0", "available": "2025.12.1"}])


if __name__ == "__main__":
    unittest.main()
