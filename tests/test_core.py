import base64
import hashlib
import http.client
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

    def test_monitor_links_follow_the_outside_address(self):
        with tempfile.TemporaryDirectory() as d:
            inst = home_net.Install(d)
            state = home_net.new_state(d, None, ["netzwerkmonitor"], 8093, "geheim123", owner="chef")
            env = Path(d, "netzwerkmonitor.env")
            inst.write_private(env, "NTFY_TOKEN=x\nPUBLIC_URL=http://alt:8085/\n")
            inst.apply(state)
            self.assertIn("PUBLIC_URL=http://alt:8085/", env.read_text())
            Path(d, "gateway", "security.json").write_text(json.dumps({"external_url": "https://home.example"}))
            inst.apply(state)
            self.assertEqual(env.read_text(), "NTFY_TOKEN=x\nPUBLIC_URL=https://home.example/m/netzwerkmonitor/\n")

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
