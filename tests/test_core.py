import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
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


class Passkeys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        gateway.CONFIG_DIR = self.dir.name
        gateway.Handler.limiter = gateway.Limiter()
        Path(self.dir.name, "gateway.json").write_text(json.dumps({"modules": []}))
        Path(self.dir.name, "auth.json").write_text(json.dumps(home_net.password_record("geheim", rounds=1000)))
        self.gw = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        threading.Thread(target=self.gw.serve_forever, daemon=True).start()
        self.addCleanup(self.gw.shutdown)
        self.base = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.store = Security.shared(self.dir.name)
        self.store.set_external_url(ORIGIN)
        self.phone = FakePasskey()

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

    def basic(self, pw="geheim"):
        return "Basic " + base64.b64encode(f"app:{pw}".encode()).decode()

    def enroll(self):
        code = self.store.new_code("setup")
        status, data, _ = self.call("/auth/setup", {"password": "geheim", "code": code, "name": "Handy"})
        self.assertEqual(status, 200, data)
        answer = self.phone.create(data["options"]["challenge"])
        return self.call("/auth/setup/finish", dict(answer, ticket=data["ticket"]))

    def session_cookie(self, headers):
        return headers["Set-Cookie"].split(";")[0]

    def login(self):
        status, data, _ = self.call("/auth/login", {"password": "geheim"})
        self.assertEqual(status, 200, data)
        answer = self.phone.get(data["options"]["challenge"])
        return self.call("/auth/login/finish", dict(answer, ticket=data["ticket"]))

    def test_setup_needs_the_code_from_the_start_page(self):
        status, _, _ = self.call("/auth/setup", {"password": "geheim", "code": "00000000"})
        self.assertEqual(status, 400)
        self.assertFalse(self.store.has_passkeys())

    def test_code_works_only_once(self):
        code = self.store.new_code("setup")
        self.assertEqual(self.call("/auth/setup", {"password": "geheim", "code": code})[0], 200)
        self.assertEqual(self.call("/auth/setup", {"password": "geheim", "code": code})[0], 400)

    def test_external_needs_passkey_once_set_up(self):
        self.assertEqual(self.call("/api/modules", auth=self.basic())[0], 200)
        status, data, headers = self.enroll()
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["recovery"]), 8)
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.call("/api/modules", auth=self.basic())[0], 401)
        self.assertEqual(self.call("/api/modules", external=False, auth=self.basic())[0], 200)
        code, _, redirect = self.call("/", html=True)
        self.assertEqual((code, redirect["Location"]), (302, "/login"))

    def test_login_with_passkey_gives_session(self):
        self.enroll()
        status, data, headers = self.login()
        self.assertEqual(status, 200, data)
        cookie = self.session_cookie(headers)
        self.assertEqual(self.call("/api/modules", cookie=cookie)[0], 200)
        self.assertEqual(self.call("/auth/security", cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", cookie="home_net_session=erfunden")[0], 401)
        self.assertEqual(self.call("/auth/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/modules", cookie=cookie)[0], 401)

    def test_wrong_password_never_reaches_passkey(self):
        self.enroll()
        self.assertEqual(self.call("/auth/login", {"password": "falsch"})[0], 401)

    def login_answer_refused(self, **changes):
        status, data, _ = self.call("/auth/login", {"password": "geheim"})
        self.assertEqual(status, 200, data)
        answer = self.phone.get(data["options"]["challenge"], **changes)
        return self.call("/auth/login/finish", dict(answer, ticket=data["ticket"]))[0]

    def test_refuses_foreign_site_foreign_domain_and_missing_confirmation(self):
        self.enroll()
        self.assertEqual(self.login_answer_refused(origin="https://evil.example"), 400)
        self.assertEqual(self.login_answer_refused(rp_id="evil.example"), 400)
        self.assertEqual(self.login_answer_refused(flags=0x01), 400)

    def test_refuses_other_key_and_replay(self):
        self.enroll()
        _, data, _ = self.call("/auth/login", {"password": "geheim"})
        answer = self.phone.get(data["options"]["challenge"])
        other = FakePasskey()
        other.count = self.phone.count + 1
        forged = other.get(data["options"]["challenge"])
        self.assertEqual(self.call("/auth/login/finish", dict(forged, ticket=data["ticket"]))[0], 400)
        self.assertEqual(self.call("/auth/login/finish", dict(answer, ticket=data["ticket"]))[0], 400)
        gateway.Handler.limiter = gateway.Limiter()
        _, data, _ = self.call("/auth/login", {"password": "geheim"})
        replay = self.phone.get(data["options"]["challenge"])
        self.phone.count -= 1
        self.assertEqual(self.call("/auth/login/finish", dict(replay, ticket=data["ticket"]))[0], 200)
        _, data, _ = self.call("/auth/login", {"password": "geheim"})
        again = self.phone.get(data["options"]["challenge"])
        self.assertEqual(self.call("/auth/login/finish", dict(again, ticket=data["ticket"]))[0], 400)

    def test_device_key_works_for_modules_but_not_for_security(self):
        self.enroll()
        code = self.store.new_code("device")
        status, data, _ = self.call("/auth/pair", {"password": "geheim", "code": code, "name": "Pixel"})
        self.assertEqual(status, 200, data)
        bearer = "Bearer " + data["token"]
        self.assertEqual(self.call("/api/modules", auth=bearer)[0], 200)
        self.assertEqual(self.call("/auth/security", auth=bearer)[0], 403)
        self.assertEqual(self.call("/auth/code", {"kind": "setup"}, auth=bearer)[0], 403)
        self.assertNotIn(data["token"], Path(self.dir.name, "security.json").read_text())
        device = self.store.summary()["devices"][0]
        self.assertEqual(device["name"], "Pixel")
        self.assertEqual(self.call(f"/auth/devices/{device['id']}", method="DELETE", external=False, auth=self.basic())[0], 200)
        self.assertEqual(self.call("/api/modules", auth=bearer)[0], 401)

    def test_pairing_needs_password_and_code(self):
        code = self.store.new_code("device")
        self.assertEqual(self.call("/auth/pair", {"password": "falsch", "code": code})[0], 401)
        self.assertEqual(self.call("/auth/pair", {"password": "geheim", "code": "1"})[0], 400)

    def test_recovery_code_once(self):
        _, data, _ = self.enroll()
        code = data["recovery"][0]
        status, _, headers = self.call("/auth/recovery", {"password": "geheim", "code": code})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/api/modules", cookie=self.session_cookie(headers))[0], 200)
        self.assertEqual(self.call("/auth/recovery", {"password": "geheim", "code": code})[0], 400)
        self.assertEqual(self.call("/auth/recovery", {"password": "falsch", "code": data["recovery"][1]})[0], 401)

    def test_lockout_covers_login_attempts(self):
        for _ in range(gateway.MAX_FAILS):
            self.call("/auth/login", {"password": "falsch"})
        self.assertEqual(self.call("/auth/login", {"password": "geheim"})[0], 429)

    def test_external_address_rules(self):
        self.assertRaises(ValueError, self.store.set_external_url, "http://home.example.de")
        self.enroll()
        self.assertRaises(ValueError, self.store.set_external_url, "https://other.example.de")

    def test_session_cookie_not_passed_to_modules(self):
        self.assertIn("cookie", gateway.HOP_BY_HOP)


if __name__ == "__main__":
    unittest.main()
