import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import npm  # noqa: E402
import server  # noqa: E402

EMAIL, PASSWORD = "home-net@example.org", "geheim"
DAY = 86400


def stamp(days):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + days * DAY))


def example_state():
    """A small home with every kind of address."""
    return {
        "calls": [], "tokens": 0, "read_only": False,
        "proxies": [
            {"id": 1, "domain_names": ["cloud.example.org"], "forward_scheme": "http", "forward_host": "192.168.1.10",
             "forward_port": 8080, "access_list_id": 0, "certificate_id": 1, "ssl_forced": True, "enabled": 1,
             "meta": {"nginx_online": True}},
            {"id": 2, "domain_names": ["router.example.org"], "forward_scheme": "https", "forward_host": "192.168.1.1",
             "forward_port": 443, "access_list_id": 1, "certificate_id": 2, "enabled": 1, "meta": {}},
            {"id": 3, "domain_names": ["fotos.example.org"], "forward_scheme": "http", "forward_host": "192.168.1.10",
             "forward_port": 2283, "access_list_id": 2, "certificate_id": 1, "enabled": 1, "meta": {}},
            {"id": 4, "domain_names": ["alt.example.org"], "forward_scheme": "http", "forward_host": "192.168.1.99",
             "forward_port": 80, "access_list_id": 0, "certificate_id": 3, "enabled": 1, "meta": {}},
            {"id": 5, "domain_names": ["aus.example.org"], "forward_scheme": "http", "forward_host": "192.168.1.10",
             "forward_port": 81, "access_list_id": 0, "certificate_id": 0, "enabled": 0, "meta": {}},
        ],
        "redirects": [{"id": 1, "domain_names": ["www.example.org"], "forward_scheme": "auto",
                       "forward_domain_name": "example.org", "certificate_id": 1, "enabled": 1}],
        "dead": [],
        "streams": [{"id": 1, "incoming_port": 51820, "forwarding_host": "192.168.1.10", "forwarding_port": 51820,
                     "tcp_forwarding": 0, "udp_forwarding": 1, "enabled": 1}],
        "certs": [
            {"id": 1, "provider": "letsencrypt", "nice_name": "example.org", "domain_names": ["*.example.org"],
             "expires_on": stamp(60)},
            {"id": 2, "provider": "letsencrypt", "nice_name": "router.example.org",
             "domain_names": ["router.example.org"], "expires_on": stamp(9)},
            {"id": 3, "provider": "other", "nice_name": "Altes Zertifikat", "domain_names": ["alt.example.org"],
             "expires_on": stamp(-3)},
            {"id": 4, "provider": "letsencrypt", "nice_name": "unbenutzt", "domain_names": ["x.example.org"],
             "expires_on": stamp(-30)},
        ],
        "lists": [
            {"id": 1, "name": "nur-intern", "satisfy_any": 1, "items": [],
             "clients": [{"address": "192.168.1.0/24", "directive": "allow"},
                         {"address": "10.8.0.0/24", "directive": "allow"}]},
            {"id": 2, "name": "familie", "satisfy_any": 1, "items": [{"username": "oma"}],
             "clients": [{"address": "192.168.1.0/24", "directive": "allow"}]},
        ],
    }


class FakeNpm(BaseHTTPRequestHandler):
    """Enough of the Nginx Proxy Manager API (v2) for the module."""
    state = None

    def log_message(self, *args):
        pass

    def reply(self, code, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        s = self.state
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        if path == "/api/":
            return self.reply(200, {"status": "OK", "setup": True, "version": "2.16.0"})
        if self.command == "POST" and path == "/api/tokens":
            if body.get("identity") != EMAIL or body.get("secret") != PASSWORD:
                return self.reply(401, {"error": {"code": 401, "message": "Invalid email or password"}})
            s["tokens"] += 1
            return self.reply(200, {"token": f"t{s['tokens']}", "expires": stamp(1)})
        if self.headers.get("Authorization") != f"Bearer t{s['tokens']}":
            return self.reply(401, {"error": {"code": 401, "message": "Token has expired"}})
        s["calls"].append((self.command, self.path))
        lists = {"/api/nginx/proxy-hosts": "proxies", "/api/nginx/redirection-hosts": "redirects",
                 "/api/nginx/dead-hosts": "dead", "/api/nginx/streams": "streams",
                 "/api/nginx/certificates": "certs", "/api/nginx/access-lists": "lists"}
        if self.command == "GET" and path in lists:
            return self.reply(200, s[lists[path]])
        if self.command == "POST":
            if s["read_only"]:
                return self.reply(403, {"error": {"code": 403, "message": "Permission Denied"}})
            parts = path.split("/")
            if path.startswith("/api/nginx/certificates/") and path.endswith("/renew"):
                cert = next(c for c in s["certs"] if c["id"] == int(parts[4]))
                cert["expires_on"] = stamp(90)
                return self.reply(200, cert)
            if path.startswith("/api/nginx/proxy-hosts/"):
                host = next(h for h in s["proxies"] if h["id"] == int(parts[4]))
                if host["enabled"] == (parts[5] == "enable"):
                    return self.reply(400, {"error": {"code": 400, "message": "Host is already enabled"}})
                host["enabled"] = 1 if parts[5] == "enable" else 0
                return self.reply(200, True)
        return self.reply(404, {"error": {"code": 404, "message": "Not Found"}})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


DNS = {"cloud.example.org": ["203.0.113.5"], "router.example.org": ["203.0.113.5"], "fotos.example.org": ["203.0.113.5"],
       "alt.example.org": [], "www.example.org": ["10.0.0.2"], "v6.example.org": ["2001:db8::1"]}
TARGETS = {("192.168.1.99", 80): "down"}


def fake_checks():
    return npm.Checks(resolve=lambda d: DNS.get(d, []), connect=lambda h, p: TARGETS.get((h, p), "up"))


class ExposureTest(unittest.TestCase):
    def test_rules(self):
        e = npm.exposure
        self.assertEqual(e(True, None)[0], "public")
        self.assertEqual(e(False, None)[0], "off")
        lan = {"name": "lan", "satisfy_any": True, "users": 0,
               "clients": [{"address": "192.168.178.0/24", "directive": "allow"}]}
        self.assertEqual(e(True, lan)[0], "internal")
        self.assertEqual(e(True, dict(lan, users=2))[0], "password")
        self.assertEqual(e(True, dict(lan, users=2, satisfy_any=False))[0], "internal")
        some = dict(lan, clients=lan["clients"] + [{"address": "198.51.100.7", "directive": "allow"}])
        self.assertEqual(e(True, some)[0], "restricted")
        everyone = dict(lan, clients=[{"address": "all", "directive": "allow"}])
        self.assertEqual(e(True, everyone)[0], "public")
        # a deny-all before the allow-all wins in nginx
        blocked = dict(lan, clients=[{"address": "192.168.178.0/24", "directive": "allow"},
                                     {"address": "all", "directive": "deny"},
                                     {"address": "all", "directive": "allow"}])
        self.assertEqual(e(True, blocked)[0], "internal")
        vpn = dict(lan, clients=[{"address": "100.64.0.0/10", "directive": "allow"},
                                 {"address": "fd00::/8", "directive": "allow"}])
        self.assertEqual(e(True, vpn)[0], "internal")
        only_password = {"name": "pw", "satisfy_any": False, "users": 1, "clients": []}
        self.assertEqual(e(True, only_password)[0], "internal")  # NPM: deny all and satisfy all lets nobody in
        self.assertEqual(e(True, dict(only_password, satisfy_any=True))[0], "password")

    def test_times(self):
        self.assertEqual(npm.parse_time("2026-12-01 10:00:00"), 1796119200)
        self.assertEqual(npm.parse_time("2026-12-01T10:00:00.000Z"), 1796119200)
        self.assertIsNone(npm.parse_time(""))
        self.assertEqual(npm.cert_state(1000 + 30 * DAY, now=1000), ("ok", 30))
        self.assertEqual(npm.cert_state(1000 + 5 * DAY, now=1000), ("soon", 5))
        self.assertEqual(npm.cert_state(500, now=1000)[0], "expired")

    def test_dns_packet(self):
        """Parses an answer with a CNAME before the A record, as public resolvers send it."""
        import socket
        import struct
        sent = {}

        class FakeSocket:
            def __init__(self, *a):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def sendto(self, packet, address):
                sent["packet"] = packet

            def recv(self, n):
                p = sent["packet"]
                ident = p[:2]
                question = p[12:]
                cname = b"\x03cdn\x07example\x03net\x00"
                answer = (b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, len(cname)) + cname
                          + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("203.0.113.9"))
                return ident + struct.pack(">HHHHH", 0x8180, 1, 2, 0, 0) + question + answer

        real = socket.socket
        socket.socket = FakeSocket
        try:
            self.assertEqual(npm.dns_query("cloud.example.org", "1.1.1.1"), ["203.0.113.9"])
            self.assertEqual(npm.dns_query("cloud.example.org", "1.1.1.1", qtype=28), [])
            self.assertEqual(fake_checks().dns("v6.example.org")["state"], "ipv6")
        finally:
            socket.socket = real


class ServiceTest(unittest.TestCase):
    def setUp(self):
        FakeNpm.state = example_state()
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeNpm)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.events = os.path.join(self.tmp.name, "events")
        os.makedirs(self.events)
        server.EVENTS_DIR = self.events
        settings = server.Settings(self.tmp.name)
        settings.update({"url": f"http://127.0.0.1:{self.fake.server_port}"})
        server.ApiHandler.settings = settings
        server.ApiHandler.npm = npm.Npm(settings.get, fake_checks())
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()

    def tearDown(self):
        self.api.shutdown()
        self.fake.shutdown()
        self.tmp.cleanup()

    def call(self, method, path, body=None, role="owner"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.api.server_port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Home-Net-Role": role, "X-Home-Net-User": "jan",
                                              **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def sign_in(self):
        self.assertEqual(self.call("PUT", "/api/settings", {"email": EMAIL, "password": PASSWORD})[0], 200)

    def test_needs_settings_and_manager(self):
        code, o = self.call("GET", "/api/overview")
        self.assertEqual((code, o["configured"]), (200, False))
        self.assertEqual(self.call("GET", "/api/overview", role="user")[0], 403)
        self.assertEqual(self.call("GET", "/health", role="user")[0], 200)
        self.call("PUT", "/api/settings", {"email": EMAIL, "password": "falsch"})
        o = self.call("GET", "/api/overview")[1]
        self.assertTrue(o["configured"])
        self.assertIn("lehnt", o["error"])
        self.assertEqual(self.call("PUT", "/api/settings", {"url": "kein url"})[0], 400)
        s = self.call("GET", "/api/settings")[1]
        self.assertEqual(s["email"], EMAIL)
        self.assertTrue(s["password_set"])
        self.assertNotIn("password", s)
        self.assertEqual(oct(os.stat(os.path.join(self.tmp.name, "settings.json")).st_mode)[-3:], "600")

    def test_overview(self):
        self.sign_in()
        code, o = self.call("GET", "/api/overview")
        self.assertEqual(code, 200, o)
        self.assertEqual(o["version"], "2.16.0")
        by = {h["domains"][0]: h for h in o["hosts"]}
        self.assertEqual(by["cloud.example.org"]["exposure"], "public")
        self.assertEqual(by["router.example.org"]["exposure"], "internal")
        self.assertEqual(by["router.example.org"]["access_list"], "nur-intern")
        self.assertEqual(by["router.example.org"]["dns"], [])
        self.assertEqual(by["fotos.example.org"]["exposure"], "password")
        self.assertEqual(by["aus.example.org"]["exposure"], "off")
        self.assertEqual(by["www.example.org"]["kind"], "redirect")
        self.assertEqual(by["alt.example.org"]["target_state"], "down")
        self.assertEqual(by["alt.example.org"]["dns"][0]["state"], "missing")
        self.assertEqual(by["www.example.org"]["dns"][0]["state"], "private")
        self.assertEqual(by["cloud.example.org"]["dns"][0], {"state": "ok", "ips": ["203.0.113.5"], "domain": "cloud.example.org"})
        self.assertEqual(by["router.example.org"]["ssl"]["state"], "soon")
        self.assertEqual([h["exposure"] for h in o["hosts"]][0], "public")
        certs = {c["id"]: c for c in o["certs"]}
        self.assertEqual(certs[1]["hosts"], ["cloud.example.org", "fotos.example.org", "www.example.org"])
        self.assertTrue(certs[4]["unused"])
        self.assertEqual(certs[3]["state"], "expired")
        s = o["summary"]
        self.assertEqual((s["public"], s["internal"], s["password"], s["off"]), (3, 1, 1, 1))
        self.assertEqual((s["certs_soon"], s["certs_expired"], s["targets_down"]), (1, 1, 1))  # unused cert 4 left out
        self.assertEqual(o["streams"][0]["port"], 51820)
        # the token is reused, and a new one fetched when NPM forgets it
        self.call("GET", "/api/overview")
        self.assertEqual(FakeNpm.state["tokens"], 1)
        FakeNpm.state["tokens"] += 5
        self.assertEqual(self.call("GET", "/api/overview")[1]["error"], "")

    def test_changes(self):
        self.sign_in()
        self.assertEqual(self.call("POST", "/api/certs/2/renew")[0], 200)
        self.assertIn(("POST", "/api/nginx/certificates/2/renew"), FakeNpm.state["calls"])
        code, out = self.call("POST", "/api/certs/3/renew")
        self.assertEqual(code, 400)
        self.assertIn("Let's Encrypt", out["detail"])
        self.assertEqual(self.call("POST", "/api/certs/99/renew")[0], 404)
        self.assertEqual(self.call("PUT", "/api/hosts/proxy/1", {"enabled": False})[0], 200)
        self.assertEqual(FakeNpm.state["proxies"][0]["enabled"], 0)
        self.assertEqual(self.call("PUT", "/api/hosts/proxy/1", {"enabled": False})[0], 200)  # already off: fine
        self.assertEqual(self.call("PUT", "/api/hosts/proxy/1", {})[0], 400)
        self.assertEqual(self.call("PUT", "/api/hosts/proxy/1", {"enabled": True}, role="user")[0], 403)
        FakeNpm.state["read_only"] = True
        code, out = self.call("PUT", "/api/hosts/proxy/1", {"enabled": True})
        self.assertEqual(code, 400)
        self.assertIn("nur ansehen", out["detail"])

    def test_watcher_reports_once(self):
        self.sign_in()
        watcher = server.Watcher(self.tmp.name, server.ApiHandler.npm)

        def kinds():
            out = []
            for name in sorted(os.listdir(self.events)):
                with open(os.path.join(self.events, name)) as f:
                    out.append(json.load(f)["type"])
                os.unlink(os.path.join(self.events, name))
            return sorted(out)

        watcher.check()
        self.assertEqual(kinds(), ["cert_expired", "cert_expiring"])  # the unused expired cert is not reported
        watcher.npm.checks.target_cache.clear()
        watcher.check()
        self.assertEqual(kinds(), ["target_down"])  # second miss in a row
        watcher.check()
        self.assertEqual(kinds(), [])
        # renewed: a later warning comes again
        FakeNpm.state["certs"][1]["expires_on"] = stamp(80)
        watcher.check()
        FakeNpm.state["certs"][1]["expires_on"] = stamp(5)
        watcher = server.Watcher(self.tmp.name, server.ApiHandler.npm)  # state survives a restart
        watcher.check()
        self.assertEqual(kinds(), ["cert_expiring"])


if __name__ == "__main__":
    unittest.main()
