import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from service import server  # noqa: E402

ROOT = "/r"
ID = "abc123"
INVENTORY = json.dumps({"pis": [{"id": ID, "name": "Fenster", "category": "Familie", "host": "h"}]})
SUMMARY = ("DISK\t100\t1000\n"
           "PI\t/r/Familie/Fenster\t0\tok\t50\t40\t2\t300\t77\tdaily\t40\t\n"
           "RUN\t/r/Familie/Fenster\t2026-01-01_120000\tfailed\tKaputt\n")


class FakeBackend:
    def __init__(self):
        self.calls = []

    def host_call(self, verb, arg=None, timeout=60):
        self.calls.append((verb, arg))
        return {
            "inventory": INVENTORY, "summary": SUMMARY,
            "status": "disk=9G 10G 90%\ntemp=50 °C\nsudo=ok\nrsync=ok\n",
            "images": "Fenster_1.img.xz\t300\t40\tos=Debian\x1e\n",
            "state": "RUNNING=1\nLAST_ERROR=Fehler\\ X\n",
            "progress": "@@STEP 2 6 Abgleich\n@@PROGRESS 40 1,000 2MB/s\n12:00 Zeile\n",
            "start": "/log", "cancel": "ok",
        }[verb]


class Service(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.auth_path = os.path.join(self.dir.name, "auth.json")
        self.backend = FakeBackend()
        server.ROOT = ROOT
        server.Handler.store = server.Store(self.backend)
        server.Handler.auth = server.Auth(self.auth_path)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def set_password(self, pw):
        salt = b"12345678"
        digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 1000)
        with open(self.auth_path, "w") as f:
            json.dump({"salt": salt.hex(), "hash": digest.hex(), "rounds": 1000}, f)

    def call(self, path, method="GET", password="geheim"):
        req = urllib.request.Request(self.base + path, method=method, data=b"" if method == "POST" else None)
        if password is not None:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"app:{password}".encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, json.loads(body) if body else {}

    def test_no_password_set_refuses_everything(self):
        self.assertEqual(self.call("/api/overview")[0], 503)
        self.assertEqual(self.call("/health", password=None)[0], 200)
        self.assertEqual(self.backend.calls, [])

    def test_wrong_password_and_lockout(self):
        self.set_password("geheim")
        self.assertEqual(self.call("/api/overview", password=None)[0], 401)
        self.assertEqual(self.call("/api/overview", password="falsch")[0], 401)
        for _ in range(9):
            self.call("/api/overview", password="falsch")
        self.assertEqual(self.call("/api/overview")[0], 429)
        self.assertEqual(self.backend.calls, [])

    def test_overview(self):
        self.set_password("geheim")
        code, data = self.call("/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(data["images"], 2)
        self.assertEqual(data["images_bytes"], 300)
        self.assertEqual(data["pis"][0]["name"], "Fenster")
        self.assertEqual(data["errors"][0]["message"], "Kaputt")

    def test_detail_and_actions(self):
        self.set_password("geheim")
        code, data = self.call(f"/api/pis/{ID}")
        self.assertEqual(code, 200)
        self.assertEqual(data["images_list"][0]["os"], "Debian")
        self.assertEqual(data["progress"]["percent"], 40)
        self.assertEqual(data["error"], "Fehler X")
        self.assertEqual(self.call(f"/api/pis/{ID}/backup", "POST")[0], 202)
        self.assertIn(("start", "/r/Familie/Fenster"), self.backend.calls)
        self.assertEqual(self.call(f"/api/pis/{ID}/cancel", "POST")[0], 202)
        self.assertIn(("cancel", "/r/Familie/Fenster"), self.backend.calls)

    def test_unknown_and_wrong_method(self):
        self.set_password("geheim")
        self.assertEqual(self.call("/api/pis/fff")[0], 404)
        self.assertEqual(self.call(f"/api/pis/{ID}/backup")[0], 405)
        self.assertEqual(self.call("/etc/passwd")[0], 404)


if __name__ == "__main__":
    unittest.main()
