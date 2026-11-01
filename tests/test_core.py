import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "core" / "gateway"))

import home_net  # noqa: E402
import server as gateway  # noqa: E402


class Modules(unittest.TestCase):
    def test_every_module_has_what_the_core_needs(self):
        modules = home_net.available_modules()
        self.assertEqual(set(modules), {"netzwerkmonitor", "pi-wartung", "ntfy"})
        for module in modules.values():
            for key in ("id", "name", "description", "icon", "services"):
                self.assertIn(key, module)

    def test_compose_only_holds_chosen_modules(self):
        doc = home_net.compose_document(home_net.available_modules(), ["pi-wartung"])
        self.assertEqual(set(doc["services"]), {home_net.GATEWAY, "pi-wartung-engine", "pi-wartung-service"})
        self.assertTrue(doc["services"]["pi-wartung-engine"]["privileged"])
        self.assertNotIn("privileged", doc["services"]["pi-wartung-service"])
        self.assertEqual(doc["services"]["pi-wartung-service"]["restart"], "unless-stopped")
        self.assertTrue(doc["services"]["pi-wartung-engine"]["build"]["context"].startswith("/"))

    def test_gateway_config_reaches_host_network_modules(self):
        cfg = home_net.gateway_config(home_net.available_modules(), ["netzwerkmonitor", "ntfy"], {"netzwerkmonitor": "x"})
        monitor, ntfy = cfg["modules"]
        self.assertEqual(monitor["upstream"], "http://host.docker.internal:8085")
        self.assertEqual(monitor["password"], "x")
        self.assertEqual(ntfy["external_port"], 8091)
        self.assertNotIn("upstream", ntfy)


class Installation(unittest.TestCase):
    def test_install_add_remove_files(self):
        with tempfile.TemporaryDirectory() as d:
            pointer = Path(d) / "pointer"
            original = home_net.POINTER
            home_net.POINTER = pointer
            self.addCleanup(setattr, home_net, "POINTER", original)
            data = Path(d) / "data"
            home_net.main(["install", "--data", str(data), "--modules", "pi-wartung", "--password", "geheim123",
                           "--no-start"])
            state = json.loads((data / "home-net.json").read_text())
            self.assertEqual(state["modules"], ["pi-wartung"])
            self.assertEqual(oct((data / "home-net.json").stat().st_mode)[-3:], "600")
            self.assertNotIn("geheim123", (data / "home-net.json").read_text())
            self.assertTrue((data / "pi-wartung" / "config" / "auth.json").exists())
            env = (data / ".env").read_text()
            self.assertIn(f"IMAGES_DIR={data}/pi-wartung/images", env)
            compose = json.loads((data / "docker-compose.yml").read_text())
            self.assertIn("pi-wartung-engine", compose["services"])
            inst = home_net.Install(data)
            state["modules"].append("netzwerkmonitor")
            state["upstream"]["netzwerkmonitor"] = "y"
            inst.apply(state)
            self.assertTrue((data / "netzwerkmonitor.env").exists())
            self.assertEqual(oct((data / "gateway" / "gateway.json").stat().st_mode)[-3:], "600")

    def test_unknown_module_refused(self):
        with tempfile.TemporaryDirectory() as d:
            inst = home_net.Install(d)
            state = home_net.new_state(d, None, ["gibtsnicht"], 1, "geheim123")
            with self.assertRaises(home_net.HomeNetError):
                inst.apply(state)


class Upstream(BaseHTTPRequestHandler):
    last_auth = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        Upstream.last_auth = self.headers.get("Authorization")
        if self.path == "/page":
            body = b'<a href="/x"><img src="/assets/a.png"><link href="rel/y">'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        elif self.path == "/moved":
            body = b""
            self.send_response(302)
            self.send_header("Location", "/settings")
            self.send_header("Content-Type", "text/plain")
        else:
            body = json.dumps({"path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Gateway(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        gateway.CONFIG_DIR = self.dir.name
        gateway.Handler.limiter = gateway.Limiter()
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.addCleanup(self.up.shutdown)
        port = self.up.server_address[1]
        modules = {"modules": [{"id": "demo", "name": "Demo", "description": "d", "icon": "i", "web": True,
                                "upstream": f"http://127.0.0.1:{port}", "health": "/health",
                                "public": ["/app"], "password": "modulpw"},
                               {"id": "ntfy", "name": "ntfy", "description": "n", "icon": "i", "external_port": 8091}]}
        Path(self.dir.name, "gateway.json").write_text(json.dumps(modules))
        Path(self.dir.name, "auth.json").write_text(json.dumps(home_net.password_record("geheim", rounds=1000)))
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.addCleanup(self.gw.shutdown)
        self.base = f"http://127.0.0.1:{self.gw.server_address[1]}"

    def call(self, path, password="geheim"):
        req = urllib.request.Request(self.base + path)
        if password is not None:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"app:{password}".encode()).decode())
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_login_required_and_lockout(self):
        self.assertEqual(self.call("/m/demo/x", None)[0], 401)
        self.assertEqual(self.call("/", None)[0], 401)
        self.assertEqual(self.call("/health", None)[0], 200)
        for _ in range(10):
            self.assertEqual(self.call("/", "falsch")[0], 401)
        self.assertEqual(self.call("/", "geheim")[0], 429)

    def test_proxy_swaps_password(self):
        code, body, _ = self.call("/m/demo/api/x?a=1")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["path"], "/api/x?a=1")
        self.assertEqual(Upstream.last_auth, "Basic " + base64.b64encode(b"app:modulpw").decode())

    def test_public_paths_skip_login_and_do_not_send_password(self):
        code, _, _ = self.call("/m/demo/app/latest.json", None)
        self.assertEqual(code, 200)
        self.assertIsNone(Upstream.last_auth)
        self.assertEqual(self.call("/m/demo/application", None)[0], 401)

    def test_html_and_redirect_get_prefix(self):
        _, body, _ = self.call("/m/demo/page")
        self.assertIn(b'href="/m/demo/x"', body)
        self.assertIn(b'src="/m/demo/assets/a.png"', body)
        self.assertIn(b'href="rel/y"', body)
        code, _, headers = self.call("/m/demo/moved")
        self.assertEqual(code, 302)
        self.assertEqual(headers["Location"], "/m/demo/settings")

    def test_module_list(self):
        code, body, _ = self.call("/api/modules")
        self.assertEqual(code, 200)
        data = json.loads(body)["modules"]
        self.assertEqual(data[0]["path"], "/m/demo")
        self.assertNotIn("password", data[0])
        self.assertNotIn("upstream", data[0])
        self.assertEqual(data[1]["external_port"], 8091)

    def test_unknown_module(self):
        self.assertEqual(self.call("/m/nix/")[0], 404)
        self.assertEqual(self.call("/m/ntfy/")[0], 404)


if __name__ == "__main__":
    unittest.main()
