import base64
import hashlib
import http.client
import io
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock
import unittest.mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "core" / "gateway"))

import home_net  # noqa: E402
import server as gateway  # noqa: E402
import webauthn  # noqa: E402
from security import Security  # noqa: E402


class Modules(unittest.TestCase):
    def test_every_module_has_what_the_core_needs(self):
        modules = home_net.available_modules()
        self.assertEqual(set(modules), {"netzwerkmonitor", "pi-wartung", "pc-sicherung", "ntfy", "unraid", "pocket-id", "pocket-id-server",
                                        "wireguard", "wireguard-server", "pihole", "proxy-manager"})
        for module in modules.values():
            for key in ("id", "name", "description", "icon", "services"):
                self.assertIn(key, module)

    def test_unraid_module_only_on_unraid(self):
        modules = home_net.available_modules()
        with mock.patch.object(home_net.Path, "exists", return_value=False):
            self.assertFalse(home_net.usable(modules["unraid"]))
            with self.assertRaises(home_net.HomeNetError):
                home_net.check_usable(modules, ["pi-wartung", "unraid"])
        home_net.check_usable(modules, ["pi-wartung"])
        doc = home_net.compose_document(modules, ["unraid"])
        self.assertEqual(doc["services"]["unraid-verwaltung"]["network_mode"], "none")
        self.assertIn("${DATA_DIR}/unraid/run:/run/unraid", doc["services"]["home-net-gateway"]["volumes"])

    def test_compose_only_holds_chosen_modules(self):
        doc = home_net.compose_document(home_net.available_modules(), ["pi-wartung"])
        self.assertEqual(set(doc["services"]), {home_net.GATEWAY, "pi-wartung-engine", "pi-wartung-service"})
        self.assertTrue(doc["services"]["pi-wartung-engine"]["privileged"])
        self.assertNotIn("privileged", doc["services"]["pi-wartung-service"])
        self.assertEqual(doc["services"]["pi-wartung-service"]["restart"], "unless-stopped")
        self.assertTrue(doc["services"]["pi-wartung-engine"]["build"]["context"].startswith("/"))

    def test_gateway_reaches_monitor_only_through_its_socket(self):
        cfg = home_net.gateway_config(home_net.available_modules(), ["netzwerkmonitor", "ntfy"], {"netzwerkmonitor": "x"})
        monitor, ntfy = cfg["modules"]
        self.assertEqual(monitor["upstream"], "unix:/run/netzwerkmonitor/api.sock")
        self.assertEqual(monitor["password"], "x")
        self.assertEqual(ntfy["external_port"], 8091)
        self.assertNotIn("upstream", ntfy)


    def test_gateway_shares_the_socket_folder_and_monitor_opens_no_port(self):
        modules = home_net.available_modules()
        doc = home_net.compose_document(modules, ["netzwerkmonitor"])
        self.assertIn("${DATA_DIR}/netzwerkmonitor/run:/run/netzwerkmonitor", doc["services"]["home-net-gateway"]["volumes"])
        self.assertIn("${DATA_DIR}/netzwerkmonitor/run:/run/netzwerkmonitor", doc["services"]["networkmonitor"]["volumes"])
        self.assertIn("LISTEN_SOCKET=/run/netzwerkmonitor/api.sock", doc["services"]["networkmonitor"]["environment"])
        self.assertNotIn("ports", doc["services"]["networkmonitor"])


class ProgramUpdate(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "home-net").write_text("alt")
        (self.repo / "weg.txt").write_text("alt")

    def archive(self, files):
        path = self.root / "main.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, text in files.items():
                info = tarfile.TarInfo(f"Home-Net-main/{name}")
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text.encode()))
        return path.as_uri()

    def test_new_version_replaces_the_folder(self):
        home_net.replace_program(self.repo, self.archive({"home-net": "neu", "core/a.py": "x"}))
        self.assertEqual((self.repo / "home-net").read_text(), "neu")
        self.assertEqual((self.repo / "core" / "a.py").read_text(), "x")
        self.assertFalse((self.repo / "weg.txt").exists())
        self.assertEqual(sorted(p.name for p in self.root.glob("repo*")), ["repo"])

    def test_failed_or_wrong_download_keeps_the_old_version(self):
        for source in ((self.root / "fehlt.tar.gz").as_uri(), self.archive({"anderes": "x"})):
            with self.assertRaises(home_net.HomeNetError):
                home_net.replace_program(self.repo, source)
            self.assertEqual((self.repo / "home-net").read_text(), "alt")

    def test_broken_archive_keeps_the_old_version(self):
        broken = self.root / "kaputt.tar.gz"
        broken.write_bytes(b"kein archiv")
        with self.assertRaises(home_net.HomeNetError):
            home_net.replace_program(self.repo, broken.as_uri())
        self.assertEqual((self.repo / "home-net").read_text(), "alt")
        self.assertEqual(sorted(p.name for p in self.root.glob("repo*")), ["repo"])

    def test_without_ssl_curl_downloads(self):
        with unittest.mock.patch.object(home_net, "has_https", return_value=False):
            home_net.replace_program(self.repo, self.archive({"home-net": "neu"}))
            self.assertEqual((self.repo / "home-net").read_text(), "neu")
            with self.assertRaises(home_net.HomeNetError):
                home_net.replace_program(self.repo, (self.root / "fehlt.tar.gz").as_uri())
            self.assertEqual((self.repo / "home-net").read_text(), "neu")

    def test_finds_the_data_folder_beside_the_program(self):
        data = self.root / "data"
        data.mkdir()
        (data / "home-net.json").write_text("{}")
        for name, value in (("REPO", self.repo), ("POINTER", self.root / "fehlt")):
            self.addCleanup(setattr, home_net, name, getattr(home_net, name))
            setattr(home_net, name, value)
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("HOME_NET_DATA", None)
            self.assertEqual(home_net.Install.locate().data, data)


class Installation(unittest.TestCase):
    def test_install_add_remove_files(self):
        with tempfile.TemporaryDirectory() as d:
            pointer = Path(d) / "pointer"
            original = home_net.POINTER
            home_net.POINTER = pointer
            self.addCleanup(setattr, home_net, "POINTER", original)
            data = Path(d) / "data"
            home_net.main(["install", "--data", str(data), "--modules", "pi-wartung", "--owner", "chef", "--password", "geheim123",
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

    def test_pc_backups_get_their_own_folder_and_socket(self):
        with tempfile.TemporaryDirectory() as d:
            inst = home_net.Install(d)
            state = home_net.new_state(d, str(Path(d, "bilder", "pi")), ["pc-sicherung"], 8093, "geheim123")
            inst.apply(state)
            self.assertIn(f"PC_DIR={d}/bilder/PC-Sicherung", Path(d, ".env").read_text())
            self.assertTrue(Path(d, "bilder", "PC-Sicherung").is_dir())
            state["pc_dir"] = str(Path(d, "pcs"))
            inst.apply(state)
            self.assertIn(f"PC_DIR={d}/pcs", Path(d, ".env").read_text())
            doc = json.loads(Path(d, "docker-compose.yml").read_text())
            self.assertIn("${DATA_DIR}/pc-sicherung/run:/run/pc-sicherung", doc["services"]["home-net-gateway"]["volumes"])
            self.assertEqual(doc["services"]["pc-sicherung"]["network_mode"], "host")
            cfg = json.loads(Path(d, "gateway", "gateway.json").read_text())
            self.assertEqual(cfg["modules"][0]["upstream"], "unix:/run/pc-sicherung/api.sock")

    def test_pocket_id_with_its_own_server(self):
        with tempfile.TemporaryDirectory() as d:
            pointer = Path(d) / "pointer"
            original = home_net.POINTER
            home_net.POINTER = pointer
            self.addCleanup(setattr, home_net, "POINTER", original)
            data = Path(d) / "data"
            home_net.main(["install", "--data", str(data), "--modules", "pocket-id", "--owner", "chef", "--password",
                           "geheim123", "--no-start"])
            self.assertFalse((data / "pocket-id" / "config" / "api-key").exists())
            with mock.patch.object(home_net.Install, "start"), \
                    self.assertRaises(SystemExit):
                home_net.main(["add", "pocket-id-server", "--url", "http://id.example.org"])
            with mock.patch.object(home_net.Install, "start"):
                home_net.main(["add", "pocket-id-server", "--url", "https://id.example.org/"])
            state = json.loads((data / "home-net.json").read_text())
            self.assertEqual(state["pocket_id_url"], "https://id.example.org")
            self.assertEqual((data / "pocket-id-server.env").read_text(), "APP_URL=https://id.example.org\n")
            key = (data / "pocket-id-server" / "secrets" / "api-key").read_text()
            self.assertGreater(len(key.strip()), 16)
            self.assertEqual(oct((data / "pocket-id-server" / "secrets" / "encryption-key").stat().st_mode)[-3:], "600")
            # the module gets the same key, and keys stay on the next apply
            self.assertEqual((data / "pocket-id" / "config" / "api-key").read_text(), key)
            home_net.Install(data).apply(state)
            self.assertEqual((data / "pocket-id-server" / "secrets" / "api-key").read_text(), key)
            doc = json.loads((data / "docker-compose.yml").read_text())
            self.assertIn("pocket-id-server", doc["services"])
            self.assertIn("host.docker.internal:host-gateway", doc["services"]["pocket-id-verwaltung"]["extra_hosts"])

    def test_sqlite_data_goes_to_the_pool_on_unraid(self):
        with tempfile.TemporaryDirectory() as d:
            mnt = Path(d)
            (mnt / "user" / "appdata" / "home-net").mkdir(parents=True)
            (mnt / "disk1").mkdir()
            self.assertEqual(home_net.pool_path("/mnt/user/appdata/home-net", mnt), Path("/mnt/user/appdata/home-net"))
            (mnt / "dockeronly" / "appdata" / "home-net").mkdir(parents=True)
            (mnt / "cache" / "appdata" / "home-net").mkdir(parents=True)
            self.assertEqual(home_net.pool_path("/mnt/user/appdata/home-net", mnt), mnt / "cache" / "appdata" / "home-net")
            self.assertEqual(home_net.pool_path("/srv/home-net", mnt), Path("/srv/home-net"))

    def test_pocket_id_add_asks_for_existing_server(self):
        with tempfile.TemporaryDirectory() as d:
            pointer = Path(d) / "pointer"
            original = home_net.POINTER
            home_net.POINTER = pointer
            self.addCleanup(setattr, home_net, "POINTER", original)
            data = Path(d) / "data"
            home_net.main(["install", "--data", str(data), "--modules", "pi-wartung", "--owner", "chef", "--password",
                           "geheim123", "--no-start"])
            fresh = (data / "home-net.json").read_text()
            with mock.patch.object(home_net.Install, "start"), mock.patch("builtins.input", return_value="j"):
                home_net.main(["add", "pocket-id"])
            self.assertNotIn("pocket-id-server", json.loads((data / "home-net.json").read_text())["modules"])
            (data / "home-net.json").write_text(fresh)
            answers = iter(["n", "https://id.example.org"])
            with mock.patch.object(home_net.Install, "start"), mock.patch("builtins.input", lambda _q: next(answers)):
                home_net.main(["add", "pocket-id"])
            state = json.loads((data / "home-net.json").read_text())
            self.assertEqual(state["modules"], ["pi-wartung", "pocket-id", "pocket-id-server"])

    def test_wireguard_with_its_own_server(self):
        with tempfile.TemporaryDirectory() as d:
            pointer = Path(d) / "pointer"
            original = home_net.POINTER
            home_net.POINTER = pointer
            self.addCleanup(setattr, home_net, "POINTER", original)
            data = Path(d) / "data"
            home_net.main(["install", "--data", str(data), "--modules", "pi-wartung", "--owner", "chef", "--password",
                           "geheim123", "--no-start"])
            fresh = (data / "home-net.json").read_text()
            with mock.patch.object(home_net.Install, "start"):
                home_net.main(["add", "wireguard", "--existing"])
            state = json.loads((data / "home-net.json").read_text())
            self.assertNotIn("wireguard-server", state["modules"])
            self.assertFalse((data / "wireguard" / "config" / "login.json").exists())
            (data / "home-net.json").write_text(fresh)
            answers = iter(["n", "https://vpn.example.org/"])
            with mock.patch.object(home_net.Install, "start"), mock.patch("builtins.input", lambda _q: next(answers)):
                home_net.main(["add", "wireguard"])
            state = json.loads((data / "home-net.json").read_text())
            self.assertEqual(state["modules"], ["pi-wartung", "wireguard", "wireguard-server"])
            self.assertEqual(state["wireguard_host"], "vpn.example.org")
            env = (data / "wireguard-server.env").read_text()
            password = (data / "wireguard-server" / "secrets" / "password").read_text().strip()
            self.assertGreater(len(password), 16)
            self.assertIn(f"INIT_PASSWORD={password}\n", env)
            self.assertIn("INIT_HOST=vpn.example.org\n", env)
            self.assertEqual(oct((data / "wireguard-server.env").stat().st_mode)[-3:], "600")
            login = json.loads((data / "wireguard" / "config" / "login.json").read_text())
            self.assertEqual(login, {"url": "http://wireguard-server:51821", "username": "home-net", "password": password})
            # the password stays on the next apply
            home_net.Install(data).apply(state)
            self.assertEqual((data / "wireguard-server" / "secrets" / "password").read_text().strip(), password)
            doc = json.loads((data / "docker-compose.yml").read_text())
            self.assertEqual(doc["services"]["wireguard-server"]["cap_add"], ["NET_ADMIN", "SYS_MODULE"])
            self.assertIn("${DATA_DIR}/wireguard/run:/run/wireguard", doc["services"]["home-net-gateway"]["volumes"])
            self.assertIn("WG_EASY_DATA=", (data / ".env").read_text())
            cfg = json.loads((data / "gateway" / "gateway.json").read_text())
            entries = {m["id"]: m for m in cfg["modules"]}
            self.assertEqual(entries["wireguard"]["upstream"], "unix:/run/wireguard/api.sock")
            self.assertEqual(entries["wireguard-server"]["external_port"], 51821)

    def test_wireguard_host_is_checked(self):
        self.assertEqual(home_net.ask_wireguard_host("203.0.113.5"), "203.0.113.5")
        with self.assertRaises(home_net.HomeNetError):
            home_net.ask_wireguard_host("vpn.example.org:51820")

    def test_app_settings_move_from_the_monitor_once(self):
        with tempfile.TemporaryDirectory() as d:
            inst = home_net.Install(d)
            state = home_net.new_state(d, None, ["netzwerkmonitor"], 8093, "geheim123", owner="chef")
            old = Path(d, "netzwerkmonitor")
            (old / "app").mkdir(parents=True)
            (old / "app" / "netzwerkmonitor-0.1.250.apk").write_bytes(b"PK apk")
            con = sqlite3.connect(old / "networkmonitor.db")
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            con.executemany("INSERT INTO meta VALUES (?, ?)", [("cfg.app_update_token", json.dumps("ghp_x")),
                                                                ("cfg.poll_interval", "30")])
            con.commit()
            con.close()
            inst.apply(state)
            app = Path(d, "gateway", "app.json")
            self.assertEqual(json.loads(app.read_text()), {"repo": "Archonw/Home-Net", "token": "ghp_x"})
            self.assertEqual(oct(app.stat().st_mode)[-3:], "600")
            self.assertEqual(Path(d, "gateway", "app", "netzwerkmonitor-0.1.250.apk").read_bytes(), b"PK apk")
            # Changes made on the page afterwards stay
            app.write_text(json.dumps({"repo": "", "token": ""}))
            inst.apply(state)
            self.assertEqual(json.loads(app.read_text())["repo"], "")

    def test_unraid_folder_holds_exactly_the_home_net_containers(self):
        with tempfile.TemporaryDirectory() as d:
            folders = Path(d, "docker.json")
            other = {"name": "Arr", "containers": ["sonarr"], "settings": {"preview": 2}}
            folders.write_text(json.dumps({"a": {"name": "Home-Net", "containers": ["home-net-gateway", "ntfy"],
                                                 "settings": {"preview": 2}}, "b": other}))
            original = home_net.UNRAID_FOLDERS
            home_net.UNRAID_FOLDERS = folders
            self.addCleanup(setattr, home_net, "UNRAID_FOLDERS", original)
            inst = home_net.Install(Path(d, "data"))
            inst.apply(home_net.new_state(d, None, ["netzwerkmonitor", "pc-sicherung"], 8093, "geheim123"))
            saved = json.loads(folders.read_text())
            self.assertEqual(saved["a"]["containers"], ["home-net-gateway", "networkmonitor", "pc-sicherung"])
            self.assertEqual(saved["a"]["settings"], {"preview": 2})
            self.assertEqual(saved["b"], other)
            self.assertFalse(home_net.sync_unraid_folder(saved["a"]["containers"], folders))
            self.assertFalse(home_net.sync_unraid_folder(["x"], Path(d, "fehlt.json")))

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
        modules["owner"] = "app"
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
        for _ in range(gateway.MAX_FAILS):
            self.assertEqual(self.call("/", "falsch")[0], 401)
        self.assertEqual(self.call("/", "geheim")[0], 429)

    def test_forged_forwarded_address_does_not_escape_lockout(self):
        for i in range(gateway.MAX_FAILS):
            req = urllib.request.Request(self.base + "/", headers={
                "Authorization": "Basic " + base64.b64encode(b"app:falsch").decode(),
                "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req, timeout=5)
        req = urllib.request.Request(self.base + "/", headers={
            "Authorization": "Basic " + base64.b64encode(b"app:geheim").decode(),
            "X-Forwarded-For": "10.9.9.9, 203.0.113.9"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 429)

    def test_tunnel_headers_count_as_outside(self):
        for header, value in (("CF-Connecting-IP", "203.0.113.9"), ("X-Real-IP", "203.0.113.9"),
                              ("Forwarded", "for=203.0.113.9"), ("Tailscale-Funnel-Request", "?1")):
            req = urllib.request.Request(self.base + "/", headers={
                "Authorization": "Basic " + base64.b64encode(b"app:geheim").decode(), header: value})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(caught.exception.code, 401, header)

    def test_home_networks(self):
        cfg = json.loads(Path(self.dir.name, "gateway.json").read_text())
        self.assertTrue(gateway.is_home_address("172.18.0.5"))
        cfg["lan"] = ["192.168.178.0/24", "kaputt"]
        Path(self.dir.name, "gateway.json").write_text(json.dumps(cfg))
        self.assertTrue(gateway.is_home_address("192.168.178.40"))
        self.assertTrue(gateway.is_home_address("::ffff:192.168.178.40"))
        self.assertTrue(gateway.is_home_address("127.0.0.1"))
        self.assertFalse(gateway.is_home_address("172.18.0.5"))
        self.assertFalse(gateway.is_home_address("10.0.0.2"))
        self.assertFalse(gateway.is_home_address("fd00::1"))
        self.assertFalse(gateway.is_home_address("203.0.113.9"))

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

    def test_module_pages_get_the_way_back(self):
        page = gateway.rewrite_html(b'<html><head></head><body class="x"><a href="/settings">s</a></body></html>', "/m/demo")
        self.assertIn(b'<body class="x"><a href="/" style', page)
        self.assertIn(b'href="/m/demo/settings"', page)

    def test_theme_script_is_public_and_in_every_module_page(self):
        page = gateway.rewrite_html(b"<html><head></head><body></body></html>", "/m/demo")
        self.assertIn(b'<script src="/hn/theme.js"></script>', page)
        code, body, headers = self.call("/hn/theme.js", None)
        self.assertEqual(code, 200)
        self.assertIn(b"window.HN", body)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))

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


ORIGIN = "https://home.example.de"
RP_ID = "home.example.de"


def cbor_bytes(data):
    return bytes([0x58, len(data)]) + data


def b64u(data):
    return webauthn.b64url(data)


class FakePasskey:
    """Behaves like a phone: signs with its own key, for whichever site it is asked."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = b"credential-id-1"
        self.count = 0

    def auth_data(self, rp_id=RP_ID, flags=0x05, attested=False):
        self.count += 1
        out = hashlib.sha256(rp_id.encode()).digest() + bytes([flags | (0x40 if attested else 0)])
        out += struct.pack(">I", self.count)
        if attested:
            n = self.key.public_key().public_numbers()
            cose = (b"\xa5\x01\x02\x03\x26\x20\x01\x21" + cbor_bytes(n.x.to_bytes(32, "big"))
                    + b"\x22" + cbor_bytes(n.y.to_bytes(32, "big")))
            out += bytes(16) + struct.pack(">H", len(self.id)) + self.id + cose
        return out

    def create(self, challenge, origin=ORIGIN, rp_id=RP_ID, flags=0x05):
        client = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": origin}).encode()
        auth = self.auth_data(rp_id, flags, attested=True)
        att = b"\xa3\x63fmt\x64none\x67attStmt\xa0\x68authData\x59" + struct.pack(">H", len(auth)) + auth
        return {"clientData": b64u(client), "attestation": b64u(att)}

    def get(self, challenge, origin=ORIGIN, rp_id=RP_ID, flags=0x05):
        client = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
        auth = self.auth_data(rp_id, flags)
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.id), "clientData": b64u(client), "authData": b64u(auth), "signature": b64u(sig)}


class AccountBase(unittest.TestCase):
    MODULES = []

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        gateway.CONFIG_DIR = self.dir.name
        gateway.Handler.limiter = gateway.Limiter()
        Path(self.dir.name, "gateway.json").write_text(json.dumps({"owner": "chef", "modules": self.module_list()}))
        Path(self.dir.name, "auth.json").write_text(json.dumps(home_net.password_record("geheim123", rounds=1000)))
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.addCleanup(self.gw.shutdown)
        self.base = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.store = Security.shared(self.dir.name)
        self.owner = self.store.user_by_name("chef")

    def module_list(self):
        return []

    def call(self, path, body=None, method=None, external=True, auth=None, cookie=None, html=False):
        headers = {}
        if external:
            headers["X-Forwarded-For"] = "203.0.113.7"
        if auth:
            headers["Authorization"] = auth
        if cookie:
            headers["Cookie"] = cookie
        if html:
            headers["Accept"] = "text/html"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw.startswith(b"{") else raw, resp.headers
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw) if raw.startswith(b"{") else raw, exc.headers

    @staticmethod
    def cookie_of(headers):
        return headers["Set-Cookie"].split(";")[0]

    def lan_login(self, name="chef", password="geheim123"):
        status, data, headers = self.call("/auth/login", {"username": name, "password": password}, external=False)
        self.assertEqual(status, 200, data)
        self.assertNotIn("Secure", headers["Set-Cookie"])
        return self.cookie_of(headers)


class Passkeys(AccountBase):
    def setUp(self):
        super().setUp()
        self.store.set_external_url(ORIGIN)
        self.phone = FakePasskey()

    def creds(self, **extra):
        return dict({"username": "chef", "password": "geheim123"}, **extra)

    def enroll(self):
        code = self.store.new_code("setup", self.owner["id"])
        status, data, _ = self.call("/auth/setup", self.creds(code=code, name="Handy"))
        self.assertEqual(status, 200, data)
        answer = self.phone.create(data["options"]["challenge"])
        return self.call("/auth/setup/finish", dict(answer, ticket=data["ticket"]))

    def login(self):
        status, data, _ = self.call("/auth/login", self.creds())
        self.assertEqual(status, 200, data)
        answer = self.phone.get(data["options"]["challenge"])
        return self.call("/auth/login/finish", dict(answer, ticket=data["ticket"]))

    def test_setup_needs_the_code_from_the_start_page(self):
        self.assertEqual(self.call("/auth/setup", self.creds(code="00000000"))[0], 400)
        self.assertEqual(self.store.passkeys_of(self.owner["id"]), [])

    def test_code_works_only_once_and_only_for_its_account(self):
        code = self.store.new_code("setup", self.owner["id"])
        other = self.store.create_user(self.owner, {"name": "gast", "password": "geheim456"})
        self.assertEqual(self.call("/auth/setup", {"username": "gast", "password": "geheim456", "code": code})[0], 400)
        self.assertEqual(other["role"], "user")
        self.assertEqual(self.call("/auth/setup", self.creds(code=code))[0], 200)
        self.assertEqual(self.call("/auth/setup", self.creds(code=code))[0], 400)

    def test_external_never_logs_in_with_password_alone(self):
        status, _, _ = self.call("/auth/login", self.creds())
        self.assertEqual(status, 403)
        basic = "Basic " + base64.b64encode(b"chef:geheim123").decode()
        self.assertEqual(self.call("/api/modules", auth=basic)[0], 401)
        self.assertEqual(self.call("/api/modules", external=False, auth=basic)[0], 200)
        code, _, redirect = self.call("/", html=True)
        self.assertEqual((code, redirect["Location"]), (302, "/login"))

    def test_login_with_passkey_gives_secure_session(self):
        status, data, headers = self.enroll()
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["recovery"]), 8)
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        status, data, headers = self.login()
        self.assertEqual(status, 200, data)
        cookie = self.cookie_of(headers)
        self.assertEqual(self.call("/api/modules", cookie=cookie)[0], 200)
        self.assertEqual(self.call("/auth/me", cookie=cookie)[1]["name"], "chef")
        self.assertEqual(self.call("/api/modules", cookie="home_net_session=erfunden")[0], 401)
        self.assertEqual(self.call("/auth/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", cookie=cookie)[0], 401)

    def test_wrong_password_never_reaches_passkey(self):
        self.enroll()
        self.assertEqual(self.call("/auth/login", self.creds(password="falsch"))[0], 401)

    def answer_status(self, **changes):
        status, data, _ = self.call("/auth/login", self.creds())
        self.assertEqual(status, 200, data)
        answer = self.phone.get(data["options"]["challenge"], **changes)
        return self.call("/auth/login/finish", dict(answer, ticket=data["ticket"]))[0]

    def test_refuses_foreign_site_foreign_domain_and_missing_confirmation(self):
        self.enroll()
        self.assertEqual(self.answer_status(origin="https://evil.example"), 400)
        self.assertEqual(self.answer_status(rp_id="evil.example"), 400)
        self.assertEqual(self.answer_status(flags=0x01), 400)

    def test_refuses_other_key_and_replay(self):
        self.enroll()
        _, data, _ = self.call("/auth/login", self.creds())
        other = FakePasskey()
        other.count = self.phone.count + 1
        forged = other.get(data["options"]["challenge"])
        self.assertEqual(self.call("/auth/login/finish", dict(forged, ticket=data["ticket"]))[0], 400)
        gateway.Handler.limiter = gateway.Limiter()
        self.assertEqual(self.answer_status(), 200)
        self.phone.count -= 1
        self.assertEqual(self.answer_status(), 400)

    def test_passkey_of_another_account_does_not_log_in(self):
        self.enroll()
        self.store.create_user(self.owner, {"name": "gast", "password": "geheim456"})
        guest = self.store.user_by_name("gast")
        ticket, challenge = self.store.new_challenge("login", user=guest["id"])
        answer = self.phone.get(challenge)
        self.assertEqual(self.call("/auth/login/finish", dict(answer, ticket=ticket))[0], 400)

    def test_device_key_works_for_modules_but_not_for_accounts(self):
        code = self.store.new_code("device", self.owner["id"])
        status, data, _ = self.call("/auth/pair", self.creds(code=code, name="Pixel"))
        self.assertEqual(status, 200, data)
        bearer = "Bearer " + data["token"]
        self.assertEqual(self.call("/api/modules", auth=bearer)[0], 200)
        self.assertEqual(self.call("/auth/me", auth=bearer)[0], 403)
        self.assertEqual(self.call("/auth/code", {"kind": "setup"}, auth=bearer)[0], 403)
        self.assertNotIn(data["token"], Path(self.dir.name, "security.json").read_text())
        cookie = self.lan_login()
        device = self.call("/auth/me", external=False, cookie=cookie)[1]["devices"][0]
        self.assertEqual(device["name"], "Pixel")
        self.assertEqual(self.call(f"/auth/devices/{device['id']}", method="DELETE", external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", auth=bearer)[0], 401)

    def test_pairing_needs_password_and_code(self):
        code = self.store.new_code("device", self.owner["id"])
        self.assertEqual(self.call("/auth/pair", self.creds(password="falsch", code=code))[0], 401)
        self.assertEqual(self.call("/auth/pair", self.creds(code="1"))[0], 400)

    def test_recovery_code_once(self):
        _, data, _ = self.enroll()
        code = data["recovery"][0]
        status, _, headers = self.call("/auth/recovery", self.creds(code=code))
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/api/modules", cookie=self.cookie_of(headers))[0], 200)
        self.assertEqual(self.call("/auth/recovery", self.creds(code=code))[0], 400)
        self.assertEqual(self.call("/auth/recovery", self.creds(password="falsch", code=data["recovery"][1]))[0], 401)

    def test_lockout_covers_login_attempts(self):
        for _ in range(gateway.MAX_FAILS):
            self.call("/auth/login", self.creds(password="falsch"))
        self.assertEqual(self.call("/auth/login", self.creds())[0], 429)

    def test_external_address_rules(self):
        self.assertRaises(ValueError, self.store.set_external_url, "http://home.example.de")
        self.enroll()
        self.assertRaises(ValueError, self.store.set_external_url, "https://other.example.de")

    def test_session_cookie_not_passed_to_modules(self):
        self.assertIn("cookie", gateway.HOP_BY_HOP)


class HeaderUpstream(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *args):
        pass

    def answer(self):
        HeaderUpstream.seen = {k.lower(): v for k, v in self.headers.items()}
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = answer


class Notifications(AccountBase):
    def module_list(self):
        common = {"description": "d", "icon": "i", "upstream": "http://127.0.0.1:9", "password": "x"}
        return [dict(common, id="pi", name="Pi", events=[{"id": "failed", "label": "Fehler"},
                                                         {"id": "ok", "label": "Erfolg", "default": False}]),
                dict(common, id="netz", name="Netz", events=[{"id": "new", "label": "Neu"}])]

    def setUp(self):
        spool = tempfile.TemporaryDirectory()
        self.addCleanup(spool.cleanup)
        self.spool = Path(spool.name)
        self.addCleanup(setattr, gateway, "EVENTS_DIR", gateway.EVENTS_DIR)
        gateway.EVENTS_DIR = spool.name
        super().setUp()
        self.boss = self.lan_login()
        self.make("oma", ["pi"], ["Familie"])
        self.make("netzer", ["netz"], [])
        self.inbox = gateway.inbox()

    def make(self, name, modules, categories):
        status, data, _ = self.call("/auth/users", {"name": name, "password": "geheim456", "role": "user",
                                                    "modules": modules, "categories": categories},
                                    external=False, cookie=self.boss)
        self.assertEqual(status, 201, data)

    def drop(self, module, name="a", **event):
        folder = self.spool / module
        folder.mkdir(exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps(event))
        self.inbox.scan(gateway.catalog())

    def titles(self, name=None):
        cookie = self.lan_login(name, "geheim456") if name else self.boss
        status, data, _ = self.call("/auth/notifications", external=False, cookie=cookie)
        self.assertEqual(status, 200, data)
        return [e["title"] for e in data["recent"]]

    def test_only_allowed_service_and_category(self):
        self.drop("pi", "1", type="failed", title="Familie kaputt", category="Familie/Oben")
        self.drop("pi", "2", type="failed", title="Firma kaputt", category="Firma")
        self.drop("netz", "3", type="new", title="Neues Gerät")
        self.assertEqual(self.titles("oma"), ["Familie kaputt"])
        self.assertEqual(self.titles("netzer"), ["Neues Gerät"])
        self.assertEqual(self.titles(), ["Neues Gerät", "Firma kaputt", "Familie kaputt"])
        self.assertEqual(list(self.spool.glob("*/*.json")), [])

    def test_account_chooses_and_defaults_apply(self):
        self.drop("pi", "1", type="ok", title="Geklappt", category="Familie")
        self.drop("pi", "2", type="failed", title="Kaputt", category="Familie")
        self.assertEqual(self.titles("oma"), ["Kaputt"])
        cookie = self.lan_login("oma", "geheim456")
        status, data, _ = self.call("/auth/notifications", {"enabled": {"pi.ok": True, "pi.failed": False}},
                                    external=False, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([e["title"] for e in data["recent"]], ["Geklappt"])
        self.assertEqual({t["key"] for t in data["types"]}, {"pi.ok", "pi.failed"})
        self.assertEqual(self.titles(), ["Kaputt"])

    def test_module_cannot_send_what_is_not_its_own(self):
        self.drop("netz", "1", type="failed", title="Fremde Art")
        self.drop("home-net", "2", type="login_locked", title="Gefälscht")
        self.drop("unbekannt", "3", type="failed", title="Unbekannt")
        (self.spool / "pi").mkdir()
        (self.spool / "pi" / "4.json").write_text("kein json")
        self.inbox.scan(gateway.catalog())
        self.assertEqual(self.titles(), [])
        self.assertEqual(list(self.spool.glob("*/*.json")), [])

    def test_security_messages_only_for_managers(self):
        for _ in range(5):
            self.call("/auth/login", {"username": "chef", "password": "falsch"}, external=True)
        self.assertEqual(self.titles(), ["Anmeldung gesperrt"])
        self.assertEqual(self.titles("oma"), [])

    def stream(self, token, after=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.gw.server_address[1], timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/auth/stream" + (f"?after={after}" if after is not None else ""),
                     headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "203.0.113.7"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["X-Accel-Buffering"], "no")
        return resp

    @staticmethod
    def next_event(resp):
        while True:
            line = resp.fp.readline().decode()
            if not line:
                return None
            if line.startswith("data: {"):
                return json.loads(line[6:])

    def test_stream_delivers_live_with_rights_until_device_is_removed(self):
        oma = self.store.user_by_name("oma")
        token = self.store.add_device(oma["id"], "Handy")
        resp = self.stream(token)
        self.inbox.add("pi", "failed", "Firma kaputt", category="Firma")
        self.inbox.add("pi", "failed", "Familie kaputt", category="Familie", link="pi/a1")
        event = self.next_event(resp)
        self.assertEqual((event["title"], event["link"]), ("Familie kaputt", "pi/a1"))
        self.assertNotIn("category", event)
        device = self.store.account(oma)["devices"][0]
        self.store.remove_item("devices", device["id"], self.owner)
        self.inbox.wake()
        self.assertIsNone(self.next_event(resp))

    def test_stream_catches_up_after_a_gap(self):
        token = self.store.add_device(self.owner["id"], "Handy")
        first = self.inbox.add("pi", "failed", "Eins")
        self.inbox.add("pi", "failed", "Zwei")
        event = self.next_event(self.stream(token, after=first["id"]))
        self.assertEqual(event["title"], "Zwei")

    def test_list_survives_restart(self):
        self.inbox.add("pi", "failed", "Bleibt")
        again = gateway.events.Inbox(self.dir.name, gateway.EVENTS_DIR)
        self.assertEqual(again.entries[-1]["title"], "Bleibt")
        self.assertEqual(again.add("pi", "failed", "Neu")["id"], 2)


class Accounts(AccountBase):
    def module_list(self):
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), HeaderUpstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.addCleanup(self.up.shutdown)
        url = f"http://127.0.0.1:{self.up.server_address[1]}"
        common = {"description": "d", "icon": "i", "upstream": url, "password": "modulpw"}
        return [dict(common, id="netz", name="Netz"), dict(common, id="pi", name="Pi")]

    def make(self, cookie, **form):
        status, data, _ = self.call("/auth/users", dict({"password": "geheim456"}, **form), external=False, cookie=cookie)
        self.assertEqual(status, 201, data)
        return data

    def test_old_installation_becomes_owner_with_its_passkeys_and_devices(self):
        path = Path(self.dir.name, "security.json")
        path.write_text(json.dumps({"external_url": ORIGIN, "recovery": ["abc"],
                                    "passkeys": [{"id": "k", "x": "", "y": "", "count": 0, "name": "n", "added": 1}],
                                    "devices": [{"id": "d", "name": "Pixel", "hash": "h", "added": 1}]}))
        data = self.store.load()
        owner = data["users"][0]
        self.assertEqual((owner["name"], owner["role"]), ("chef", "owner"))
        self.assertEqual(owner["recovery"], ["abc"])
        self.assertEqual(data["passkeys"][0]["user"], owner["id"])
        self.assertEqual(data["devices"][0]["user"], owner["id"])
        self.assertIsNotNone(self.store.check_login("chef", "geheim123"))

    def test_owner_renames_and_changes_password(self):
        cookie = self.lan_login()
        self.assertEqual(self.call("/auth/me", {"name": "jan", "old_password": "falsch"}, external=False, cookie=cookie)[0], 403)
        body = {"name": "jan", "old_password": "geheim123", "new_password": "neuesgeheim"}
        self.assertEqual(self.call("/auth/me", body, external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/auth/login", {"username": "chef", "password": "neuesgeheim"}, external=False)[0], 401)
        self.lan_login("JAN", "neuesgeheim")

    def test_user_only_sees_and_reaches_allowed_services(self):
        cookie = self.lan_login()
        self.make(cookie, name="gast", modules=["pi"], categories=["Firma"])
        guest = self.lan_login("gast", "geheim456")
        names = [m["id"] for m in self.call("/api/modules", external=False, cookie=guest)[1]["modules"]]
        self.assertEqual(names, ["pi"])
        self.assertEqual(self.call("/m/netz/api", external=False, cookie=guest)[0], 403)
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=guest)[0], 200)
        self.assertEqual(json.loads(base64.b64decode(HeaderUpstream.seen["x-home-net-categories"])), ["Firma"])
        self.assertEqual(base64.b64decode(HeaderUpstream.seen["x-home-net-user"]), b"gast")
        self.assertEqual(HeaderUpstream.seen["x-home-net-role"], "user")

    def test_clients_cannot_forge_rights(self):
        guest_cookie = self.lan_login()
        self.make(guest_cookie, name="gast", modules=["pi"], categories=["Firma"])
        guest = self.lan_login("gast", "geheim456")
        req = urllib.request.Request(self.base + "/m/pi/api", headers={
            "Cookie": guest, "X-Home-Net-Categories": base64.b64encode(b'["*"]').decode(), "X-Home-Net-Role": "owner"})
        urllib.request.urlopen(req, timeout=5).read()
        self.assertEqual(json.loads(base64.b64decode(HeaderUpstream.seen["x-home-net-categories"])), ["Firma"])
        self.assertEqual(HeaderUpstream.seen["x-home-net-role"], "user")

    def test_user_cannot_manage(self):
        cookie = self.lan_login()
        self.make(cookie, name="gast")
        guest = self.lan_login("gast", "geheim456")
        self.assertEqual(self.call("/auth/users", external=False, cookie=guest)[0], 403)
        self.assertEqual(self.call("/auth/audit", external=False, cookie=guest)[0], 403)
        self.assertEqual(self.call("/auth/users", {"name": "x1", "password": "geheim456"}, external=False, cookie=guest)[0], 403)
        self.assertEqual(self.call("/auth/code", {"kind": "setup", "user": self.owner["id"]}, external=False, cookie=guest)[0], 403)

    def test_admin_cannot_touch_owner_or_create_admins(self):
        cookie = self.lan_login()
        self.make(cookie, name="helfer", role="admin")
        admin = self.lan_login("helfer", "geheim456")
        self.assertEqual(self.call("/auth/users", {"name": "b1", "password": "geheim456", "role": "admin"}, external=False, cookie=admin)[0], 403)
        self.make(admin, name="gast")
        oid = self.owner["id"]
        self.assertEqual(self.call(f"/auth/users/{oid}", {"password": "uebernommen"}, method="PUT", external=False, cookie=admin)[0], 403)
        self.assertEqual(self.call(f"/auth/users/{oid}", method="DELETE", external=False, cookie=admin)[0], 403)
        self.assertEqual(self.call("/auth/code", {"kind": "device", "user": oid}, external=False, cookie=admin)[0], 403)

    def test_last_owner_stays(self):
        cookie = self.lan_login()
        oid = self.owner["id"]
        self.assertEqual(self.call(f"/auth/users/{oid}", method="DELETE", external=False, cookie=cookie)[0], 400)
        self.assertEqual(self.call(f"/auth/users/{oid}", {"role": "user"}, method="PUT", external=False, cookie=cookie)[0], 400)
        self.assertEqual(self.call(f"/auth/users/{oid}", {"active": False}, method="PUT", external=False, cookie=cookie)[0], 400)

    def test_blocked_account_loses_session_and_device(self):
        cookie = self.lan_login()
        user = self.make(cookie, name="gast")
        guest = self.lan_login("gast", "geheim456")
        code = self.store.new_code("device", user["id"])
        token = self.call("/auth/pair", {"username": "gast", "password": "geheim456", "code": code}, external=False)[1]["token"]
        self.assertEqual(self.call(f"/auth/users/{user['id']}", {"active": False}, method="PUT", external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", external=False, cookie=guest)[0], 401)
        self.assertEqual(self.call("/api/modules", auth=f"Bearer {token}")[0], 401)
        self.assertEqual(self.call("/auth/login", {"username": "gast", "password": "geheim456"}, external=False)[0], 401)

    def test_deleting_account_removes_its_device(self):
        cookie = self.lan_login()
        user = self.make(cookie, name="gast")
        code = self.store.new_code("device", user["id"])
        token = self.call("/auth/pair", {"username": "gast", "password": "geheim456", "code": code}, external=False)[1]["token"]
        self.assertEqual(self.call(f"/auth/users/{user['id']}", method="DELETE", external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", auth=f"Bearer {token}")[0], 401)
        self.assertEqual(self.store.load()["devices"], [])

    def test_names_are_unique_and_valid(self):
        cookie = self.lan_login()
        self.make(cookie, name="gast")
        self.assertEqual(self.call("/auth/users", {"name": "GAST", "password": "geheim456"}, external=False, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/auth/users", {"name": "../x", "password": "geheim456"}, external=False, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/auth/users", {"name": "kurzpw", "password": "kurz"}, external=False, cookie=cookie)[0], 400)

    def test_group_members_get_group_services_and_categories(self):
        cookie = self.lan_login()
        anna = self.make(cookie, name="anna", modules=["netz"], categories=["Familie"])
        ben = self.make(cookie, name="ben")
        status, group, _ = self.call("/auth/groups", {"name": "Schule", "modules": ["pi"], "categories": ["Schule"],
                                                      "members": [anna["id"], ben["id"]]}, external=False, cookie=cookie)
        self.assertEqual(status, 201, group)
        listing = self.call("/auth/users", external=False, cookie=cookie)[1]
        self.assertEqual(listing["groups"][0]["members"], [anna["id"], ben["id"]])
        self.assertEqual(next(u for u in listing["users"] if u["name"] == "anna")["modules"], ["netz"])
        session = self.lan_login("anna", "geheim456")
        names = [m["id"] for m in self.call("/api/modules", external=False, cookie=session)[1]["modules"]]
        self.assertEqual(names, ["netz", "pi"])
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=session)[0], 200)
        self.assertEqual(json.loads(base64.b64decode(HeaderUpstream.seen["x-home-net-categories"])), ["Familie", "Schule"])
        # Leaving the group takes the rights away at once.
        self.assertEqual(self.call(f"/auth/groups/{group['id']}", {"members": [ben["id"]]}, method="PUT",
                                   external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=session)[0], 403)
        ben_session = self.lan_login("ben", "geheim456")
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=ben_session)[0], 200)
        self.assertEqual(json.loads(base64.b64decode(HeaderUpstream.seen["x-home-net-categories"])), ["Schule"])
        self.assertEqual(self.call(f"/auth/groups/{group['id']}", method="DELETE", external=False, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=ben_session)[0], 403)
        self.assertEqual([u.get("groups") for u in self.store.load()["users"] if u["name"] == "ben"], [[]])

    def test_groups_via_account_and_checks(self):
        cookie = self.lan_login()
        group = self.call("/auth/groups", {"name": "Schule", "modules": ["pi"]}, external=False, cookie=cookie)[1]
        self.assertEqual(self.call("/auth/groups", {"name": "schule"}, external=False, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/auth/users", {"name": "x1", "password": "geheim456", "groups": ["nope"]},
                                   external=False, cookie=cookie)[0], 400)
        user = self.make(cookie, name="gast", groups=[group["id"]])
        self.assertEqual(user["groups"], [group["id"]])
        guest = self.lan_login("gast", "geheim456")
        self.assertEqual(self.call("/m/pi/api", external=False, cookie=guest)[0], 200)
        self.assertEqual(self.call("/auth/groups", {"name": "Eigene"}, external=False, cookie=guest)[0], 403)
        self.assertEqual(self.call(f"/auth/groups/{group['id']}", method="DELETE", external=False, cookie=guest)[0], 403)

    def test_audit_records_logins_changes_and_module_writes_without_secrets(self):
        cookie = self.lan_login()
        self.make(cookie, name="gast", modules=["pi"])
        self.call("/auth/login", {"username": "gast", "password": "falsches-wort"}, external=False)
        self.call("/m/pi/api/x", {}, external=False, cookie=cookie)
        log = self.call("/auth/audit", external=False, cookie=cookie)[1]["entries"]
        actions = [e["action"] for e in log]
        for wanted in ("Angemeldet", "Konto angelegt", "Anmeldung fehlgeschlagen", "Pi: POST"):
            self.assertIn(wanted, actions)
        self.assertNotIn("falsches-wort", json.dumps(log))
        self.assertNotIn("geheim456", json.dumps(log))


if __name__ == "__main__":
    unittest.main()


class Translations(unittest.TestCase):
    def test_word_lists_are_merged_and_reread(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "en").mkdir()
            Path(tmp, "en", "a.json").write_text('{"Anmelden": "Sign in"}')
            Path(tmp, "en", "b.json").write_text('{"Abmelden": "Sign out"}')
            with mock.patch.object(gateway, "I18N_DIR", tmp):
                gateway._dictionaries.clear()
                self.assertEqual(json.loads(gateway.dictionary("en")), {"Anmelden": "Sign in", "Abmelden": "Sign out"})
                self.assertEqual(gateway.dictionary("fr"), b"{}")
                Path(tmp, "en", "b.json").write_text('{"Abmelden": "Log out"}')
                os.utime(Path(tmp, "en", "b.json"), (1, 1))
                self.assertEqual(json.loads(gateway.dictionary("en"))["Abmelden"], "Log out")

    def test_repo_word_lists_are_valid_and_keep_placeholders(self):
        for path in sorted((ROOT / "i18n").glob("*/*.json")):
            words = json.loads(path.read_text(encoding="utf-8"))
            for german, english in words.items():
                self.assertIsInstance(english, str, f"{path.name}: {german}")
                self.assertEqual(sorted(re.findall(r"\{\d\}", german)), sorted(re.findall(r"\{\d\}", english)),
                                 f"{path.name}: {german}")
                # A template of placeholders and one short word ("{0} für {1}") would match unrelated texts
                if re.fullmatch(r"\{\d\}.*\{\d\}", german):
                    self.assertGreaterEqual(len(re.sub(r"\{\d\}|[\W\d_]", "", german)), 5, f"{path.name}: {german}")


    def test_module_labels_have_english(self):
        words = {}
        for path in (ROOT / "i18n" / "en").glob("*.json"):
            words.update(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((ROOT / "modules").glob("*/module.json")):
            info = json.loads(path.read_text(encoding="utf-8"))
            for text in [info.get("description", "")] + [e.get("label", "") for e in info.get("events", [])]:
                if text:
                    self.assertIn(text, words, f"{path.parent.name}: {text}")


class HomeNetworks(unittest.TestCase):
    def test_host_networks_skip_docker_and_vms(self):
        with tempfile.TemporaryDirectory() as tmp:
            route = Path(tmp, "route")
            route.write_text(
                "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                "br0\t00000000\t01B2A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
                "br0\t00B2A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
                "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
                "br-1a2b3c\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
                "virbr0\t007AA8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
                "wg0\t0000080A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")
            self.assertEqual(home_net.host_networks(route), ["192.168.178.0/24", "10.8.0.0/24"])
            self.assertEqual(home_net.host_networks(Path(tmp, "fehlt")), [])

    def test_gateway_config_carries_networks(self):
        cfg = home_net.gateway_config(home_net.available_modules(), [], {}, lan=["192.168.1.0/24"])
        self.assertEqual(cfg["lan"], ["192.168.1.0/24"])

    def test_vpn_container_and_docker_gateway(self):
        def docker(*args):
            if args[0] == "ps":
                return "WireGuard-Easy\tghcr.io/wg-easy/wg-easy:edge\nwireguard-verwaltung\tx\nnginx\tnginx\n"
            if args[0] == "inspect":
                return {"WireGuard-Easy": "n1 ", "home-net-gateway": "n2 "}[args[-1]]
            return {"n1": "172.17.0.0/16|172.17.0.1 ", "n2": "172.19.0.0/16| "}[args[-1]]
        with mock.patch.object(home_net, "docker_out", side_effect=docker):
            self.assertEqual(home_net.vpn_networks(), ["172.17.0.0/16", "172.19.0.1/32"])
        with mock.patch.object(home_net, "docker_out", return_value=""):
            self.assertEqual(home_net.vpn_networks(), [])


class TimeZone(unittest.TestCase):
    def test_sources(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TZ": ""}):
            etc = Path(tmp)
            ident = etc / "ident.cfg"
            self.assertEqual(home_net.host_timezone(etc, ident), "UTC")
            ident.write_text('NAME="Tower"\ntimeZone="America/New_York"\n')
            self.assertEqual(home_net.host_timezone(etc, ident), "America/New_York")
            (etc / "localtime").symlink_to("/usr/share/zoneinfo/Asia/Tokyo")
            self.assertEqual(home_net.host_timezone(etc, ident), "Asia/Tokyo")
            (etc / "timezone").write_text("Europe/Vienna\n")
            self.assertEqual(home_net.host_timezone(etc, ident), "Europe/Vienna")
class Releases(unittest.TestCase):
    def test_release_images_cover_every_build(self):
        sys.path.insert(0, str(Path(home_net.REPO, "tools")))
        import release_images
        listed = {i["name"]: i for i in release_images.images()}
        doc = home_net.compose_document(home_net.available_modules(), list(home_net.available_modules()))
        built = {name for name, svc in doc["services"].items() if "build" in svc}
        self.assertEqual(set(listed), built)
        self.assertEqual(listed["pc-sicherung"]["platforms"], "linux/amd64")
        self.assertEqual(listed["home-net-gateway"]["platforms"], "linux/amd64,linux/arm64")
        for item in listed.values():
            self.assertTrue(Path(home_net.REPO, item["dockerfile"]).exists(), item)

    def test_newest_release_ignores_app_and_drafts(self):
        releases = [{"tag_name": "app-230"}, {"tag_name": "v1.2.0"}, {"tag_name": "v1.10.0"},
                    {"tag_name": "v2.0.0", "draft": True}, {"tag_name": "v1.11.0", "prerelease": True}]
        self.assertEqual(home_net.newest_release(releases), "v1.10.0")
        self.assertIsNone(home_net.newest_release([{"tag_name": "app-1"}]))

    def test_release_uses_finished_images(self):
        modules = home_net.available_modules()
        doc = home_net.compose_document(modules, ["pi-wartung", "ntfy"], version="v1.2.0")
        services = doc["services"]
        self.assertEqual(services["home-net-gateway"]["image"], "ghcr.io/archonw/home-net/home-net-gateway:v1.2.0")
        self.assertEqual(services["pi-wartung-engine"]["image"], "ghcr.io/archonw/home-net/pi-wartung-engine:v1.2.0")
        self.assertNotIn("build", services["pi-wartung-service"])
        self.assertEqual(services["ntfy"]["image"], modules["ntfy"]["services"]["ntfy"]["image"])
        built = home_net.compose_document(modules, ["pi-wartung"])
        self.assertIn("build", built["services"]["pi-wartung-engine"])

    def test_fork_gets_its_own_sources(self):
        modules = home_net.available_modules()
        try:
            home_net.set_repo("Anna/Home-Net-Fork")
            doc = home_net.compose_document(modules, ["pi-wartung"], version="v1.2.0")
            self.assertEqual(doc["services"]["pi-wartung-engine"]["image"], "ghcr.io/anna/home-net-fork/pi-wartung-engine:v1.2.0")
            self.assertEqual(doc["services"]["home-net-gateway"]["environment"]["HOME_NET_REPO"], "Anna/Home-Net-Fork")
            self.assertIn("api.github.com/repos/Anna/Home-Net-Fork/releases", home_net.RELEASES)
            self.assertTrue(home_net.SOURCE.startswith("https://github.com/Anna/Home-Net-Fork/"))
            with self.assertRaises(home_net.HomeNetError):
                home_net.set_repo("kein repo")
        finally:
            home_net.set_repo(home_net.DEFAULT_REPO)

    def test_clone_repo_from_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(home_net.clone_repo(Path(tmp)))
            Path(tmp, ".git").mkdir()
            for url in ("https://github.com/Anna/Home-Net.git\n", "git@github.com:Anna/Home-Net.git\n", "https://github.com/Anna/Home-Net\n"):
                done = subprocess.CompletedProcess([], 0, stdout=url)
                with mock.patch.object(home_net.subprocess, "run", return_value=done):
                    self.assertEqual(home_net.clone_repo(Path(tmp)), "Anna/Home-Net")

    def test_release_of(self):
        self.assertIsNone(home_net.release_of({}))
        self.assertIsNone(home_net.release_of({"channel": "main", "version": "main"}))
        self.assertEqual(home_net.release_of({"channel": "stabil", "version": "v1.0.0"}), "v1.0.0")

    def test_arch_requirement(self):
        module = {"requires": {"arch": ["amd64"]}}
        with mock.patch("platform.machine", return_value="x86_64"):
            self.assertTrue(home_net.usable(module))
        with mock.patch("platform.machine", return_value="aarch64"):
            self.assertFalse(home_net.usable(module))
