import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import nextcloud  # noqa: E402
import server  # noqa: E402

ADMIN, APP_PASSWORD = "admin", "abcde-fghij-klmno-pqrst-uvwxy"
GB = 1024 ** 3


def example_state():
    """A small family cloud: one admin, two people, one signs in through OIDC."""
    def user(uid, name, email, groups, used, quota, backend="Database", enabled=True, login=1793700000000):
        return {"id": uid, "displayname": name, "email": email, "groups": groups, "enabled": enabled,
                "quota": {"used": used, "quota": quota, "free": 0, "total": 0, "relative": 0},
                "lastLogin": login, "backend": backend,
                "backendCapabilities": {"setPassword": backend == "Database"}}
    return {
        "calls": [], "serverinfo": True, "maintenance": False, "down": False,
        "groups": ["admin", "familie", "nextcloud_users"],
        "users": {
            "admin": user("admin", "Admin", "", ["admin"], 2 * GB, "none"),
            "anna": user("anna", "Anna Beispiel", "anna@example.org", ["familie"], 3 * GB, 10 * GB),
            "ben-oidc": user("ben-oidc", "Ben", "ben@example.org", ["familie", "nextcloud_users"], 1 * GB, "none",
                             backend="user_oidc", login=0),
        },
        "free": 500 * GB,
        "server_update": "",
        "app_updates": {"calendar": "6.1.0"},
    }


class FakeNextcloud(BaseHTTPRequestHandler):
    """Enough of Nextcloud's status.php, serverinfo and OCS Provisioning API (v2) for the module."""
    state = None

    def log_message(self, *args):
        pass

    def reply(self, code, data=None, message="", raw=None):
        body = raw if raw is not None else json.dumps(
            {"ocs": {"meta": {"status": "ok" if code < 300 else "failure", "statuscode": code, "message": message},
                     "data": data if data is not None else []}}).encode()
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
        body = json.loads(self.rfile.read(length)) if length else {}
        if s["down"]:
            return self.reply(503, raw=b"")
        if path == "/status.php":
            return self.reply(200, raw=json.dumps({
                "installed": True, "maintenance": s["maintenance"], "needsDbUpgrade": False, "version": "35.0.2.2",
                "versionstring": "35.0.2", "edition": "", "productname": "Nextcloud"}).encode())
        if not path.startswith("/ocs/v2.php/") or query.get("format") != "json":
            return self.reply(404, raw=b"<html>not found</html>")
        token = base64.b64encode(f"{ADMIN}:{APP_PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != f"Basic {token}" or self.headers.get("OCS-APIRequest") != "true":
            return self.reply(401, message="Current user is not logged in")
        path = path[len("/ocs/v2.php"):]
        s["calls"].append((self.command, path, body or {k: v for k, v in query.items() if k != "format"}))
        users = s["users"]
        if path == "/apps/serverinfo/api/v1/info":
            if not s["serverinfo"]:
                return self.reply(404, message="")
            return self.reply(200, {
                "nextcloud": {
                    "system": {"version": "35.0.2.2", "freespace": s["free"], "cpuload": [0.5, 0.4, 0.3],
                               "mem_total": 16000000, "mem_free": 8000000,
                               "apps": {"num_installed": 42, "num_updates_available": len(s["app_updates"]),
                                        "app_updates": s["app_updates"] or []},
                               "update": {"lastupdatedat": 1793700000, "available": bool(s["server_update"]),
                                          "available_version": s["server_update"]}},
                    "storage": {"num_users": len(users), "num_files": 12345},
                    "shares": {"num_shares": 7}},
                "server": {"webserver": "nginx", "php": {"version": "8.3.1"},
                           "database": {"type": "mysql", "version": "10.11", "size": 300000000}},
                "activeUsers": {"last5minutes": 1, "last1hour": 2, "last24hours": 3}})
        if self.command == "GET" and path == "/cloud/users/details":
            return self.reply(200, {"users": users})
        if self.command == "GET" and path == "/cloud/groups":
            return self.reply(200, {"groups": s["groups"]})
        if self.command == "POST" and path == "/cloud/users":
            uid = body["userid"]
            if uid in users:
                return self.reply(400, message="User already exists")
            if not body.get("password") and not body.get("email"):
                return self.reply(400, message="To send a password link to the user an email address is required.")
            users[uid] = {"id": uid, "displayname": body.get("displayName", uid), "email": body.get("email", ""),
                          "groups": body.get("groups", []), "enabled": True, "backend": "Database",
                          "quota": {"used": 0, "quota": body.get("quota", "default")}, "lastLogin": 0}
            return self.reply(200, {"id": uid})
        if self.command == "POST" and path == "/cloud/groups":
            s["groups"].append(body["groupid"])
            return self.reply(200)
        parts = path.split("/")
        if len(parts) >= 4 and parts[1:3] == ["cloud", "users"]:
            uid = urllib.parse.unquote(parts[3])
            if uid not in users:
                return self.reply(404, message="User does not exist")
            u = users[uid]
            rest = parts[4:]
            if self.command == "PUT" and not rest:
                if body["key"] == "displayname":
                    u["displayname"] = body["value"]
                elif body["key"] == "quota":
                    u["quota"]["quota"] = body["value"]
                elif body["key"] == "email":
                    u["email"] = body["value"]
                return self.reply(200)
            if self.command == "PUT" and rest in (["enable"], ["disable"]):
                u["enabled"] = rest == ["enable"]
                return self.reply(200)
            if rest == ["groups"]:
                group = body.get("groupid") or query.get("groupid")
                if group not in s["groups"]:
                    return self.reply(404, message="Group does not exist")
                if self.command == "POST":
                    u["groups"].append(group)
                elif self.command == "DELETE":
                    u["groups"].remove(group)
                return self.reply(200)
            if self.command == "POST" and rest == ["welcome"]:
                return self.reply(200)
            if self.command == "DELETE" and not rest:
                del users[uid]
                return self.reply(200)
        return self.reply(404, message="Not found")

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


class HelpersTest(unittest.TestCase):
    def test_quota(self):
        q = nextcloud.quota_text
        self.assertEqual(q(""), "default")
        self.assertEqual(q("unbegrenzt"), "none")
        self.assertEqual(q("5"), "5 GB")
        self.assertEqual(q("1,5 tb"), "1.5 TB")
        self.assertEqual(q("500 MB"), "500 MB")
        with self.assertRaises(ValueError):
            q("viel")
        self.assertEqual(nextcloud.quota_label(10 * GB), "10 GB")
        self.assertEqual(nextcloud.quota_label("none"), "Unbegrenzt")
        self.assertEqual(nextcloud.quota_label(-3), "Unbegrenzt")
        self.assertEqual(nextcloud.quota_label("default"), "Standard")

    def test_user_ids(self):
        self.assertEqual(nextcloud.check_user_id(" anna "), "anna")
        for bad in ("", "a/b", "x" * 65, "../etc"):
            with self.assertRaises(ValueError):
                nextcloud.check_user_id(bad)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        FakeNextcloud.state = example_state()
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeNextcloud)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.events = os.path.join(self.tmp.name, "events")
        os.makedirs(self.events)
        server.EVENTS_DIR = self.events
        settings = server.Settings(self.tmp.name)
        settings.update({"url": f"http://127.0.0.1:{self.fake.server_port}"})
        server.ApiHandler.settings = settings
        server.ApiHandler.nc = nextcloud.Nextcloud(settings.get)
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
        self.assertEqual(self.call("PUT", "/api/settings", {"user": ADMIN, "password": APP_PASSWORD})[0], 200)
        server.ApiHandler.nc.forget()

    def overview(self, role="owner"):
        server.ApiHandler.nc.forget()
        code, o = self.call("GET", "/api/overview", role=role)
        self.assertEqual(code, 200, o)
        return o

    def test_settings(self):
        o = self.overview()
        self.assertFalse(o["configured"])
        self.assertEqual(self.call("GET", "/health", role="user")[0], 200)
        self.assertEqual(self.call("GET", "/api/settings", role="user")[0], 403)
        self.call("PUT", "/api/settings", {"user": ADMIN, "password": "falsch"})
        o = self.overview()
        self.assertTrue(o["configured"])
        self.assertIn("App-Passwort", o["error"])
        self.assertEqual(self.call("PUT", "/api/settings", {"url": "kein url"})[0], 400)
        self.call("PUT", "/api/settings", {"verify_tls": False})
        s = self.call("GET", "/api/settings")[1]
        self.assertEqual((s["user"], s["password_set"], s["verify_tls"]), (ADMIN, True, False))
        self.assertNotIn("password", s)
        self.assertEqual(oct(os.stat(os.path.join(self.tmp.name, "settings.json")).st_mode)[-3:], "600")

    def test_overview(self):
        self.sign_in()
        o = self.overview()
        self.assertEqual(o["error"], "")
        self.assertEqual(o["status"]["version"], "35.0.2")
        self.assertEqual(o["user_count"], 3)
        self.assertEqual(o["account"], ADMIN)
        i = o["info"]
        self.assertEqual((i["free_bytes"], i["files"], i["shares"], i["apps_installed"]), (500 * GB, 12345, 7, 42))
        self.assertEqual(i["app_updates"], [{"id": "calendar", "version": "6.1.0"}])
        self.assertEqual(i["active"], {"5min": 1, "1h": 2, "24h": 3})
        self.assertEqual(i["mem_total"], 16000000 * 1024)
        self.assertFalse(i["storage_low"])
        users = {u["id"]: u for u in o["users"]}
        self.assertEqual(users["anna"]["quota"], 10 * GB)
        self.assertEqual(users["anna"]["quota_text"], "10 GB")
        self.assertEqual(users["admin"]["quota_text"], "Unbegrenzt")
        self.assertTrue(users["admin"]["admin"])
        self.assertFalse(users["ben-oidc"]["can_password"])
        self.assertEqual(users["anna"]["last_login"], 1793700000)
        self.assertEqual(o["groups"], ["admin", "familie", "nextcloud_users"])
        # plain users see the state, not the people
        o = self.overview(role="user")
        self.assertEqual((o["users"], o["groups"], o["user_count"], o["account"]), ([], [], None, ""))
        self.assertFalse(o["can_manage"])
        self.assertEqual(o["info"]["files"], 12345)
        # without the app serverinfo the rest still works
        FakeNextcloud.state["serverinfo"] = False
        o = self.overview()
        self.assertIsNone(o["info"])
        self.assertEqual(o["user_count"], 3)
        FakeNextcloud.state["down"] = True
        self.assertIn("Fehler 503", self.overview()["error"])

    def test_users(self):
        self.sign_in()
        code, out = self.call("POST", "/api/users", {"id": "clara", "name": "Clara", "email": "c@example.org",
                                                     "groups": ["familie"], "quota": "5"})
        self.assertEqual(code, 200, out)
        clara = FakeNextcloud.state["users"]["clara"]
        self.assertEqual((clara["groups"], clara["quota"]["quota"]), (["familie"], "5 GB"))
        self.assertNotIn("password", FakeNextcloud.state["calls"][-1][2])
        code, out = self.call("POST", "/api/users", {"id": "clara", "email": "c@example.org"})
        self.assertEqual(code, 400)
        self.assertIn("already exists", out["detail"])
        self.assertEqual(self.call("POST", "/api/users", {"id": "dora"})[0], 400)  # no password, no e-mail
        self.assertEqual(self.call("POST", "/api/users", {"id": "a/b", "password": "x"})[0], 400)
        self.assertEqual(self.call("POST", "/api/users", {"id": "eve", "password": "x"}, role="user")[0], 403)

        self.assertEqual(self.call("PUT", "/api/users/anna", {"name": "Anna B.", "quota": "unbegrenzt"})[0], 200)
        anna = FakeNextcloud.state["users"]["anna"]
        self.assertEqual((anna["displayname"], anna["quota"]["quota"]), ("Anna B.", "none"))
        self.assertEqual(self.call("PUT", "/api/users/anna", {})[0], 400)
        self.assertEqual(self.call("PUT", "/api/users/anna", {"enabled": False})[0], 200)
        self.assertFalse(anna["enabled"])
        code, out = self.call("PUT", "/api/users/Admin", {"enabled": False})
        self.assertEqual(code, 400)
        self.assertIn("Home-Net", out["detail"])

        self.assertEqual(self.call("PUT", "/api/users/anna/groups", {"group": "nextcloud_users", "member": True})[0], 200)
        self.assertIn("nextcloud_users", anna["groups"])
        self.assertEqual(self.call("PUT", "/api/users/anna/groups", {"group": "familie", "member": False})[0], 200)
        self.assertEqual(anna["groups"], ["nextcloud_users"])
        self.assertIn(("DELETE", "/cloud/users/anna/groups", {"groupid": "familie"}), FakeNextcloud.state["calls"])
        self.assertEqual(self.call("PUT", "/api/users/admin/groups", {"group": "admin", "member": False})[0], 400)
        self.assertEqual(self.call("PUT", "/api/users/anna/groups", {"group": "gibts-nicht", "member": True})[0], 400)
        self.assertEqual(self.call("POST", "/api/users/anna/welcome")[0], 200)
        self.assertEqual(self.call("POST", "/api/groups", {"name": "kinder"})[0], 200)
        self.assertIn("kinder", FakeNextcloud.state["groups"])

        self.assertEqual(self.call("DELETE", "/api/users/anna", {"confirm": "ann"})[0], 400)
        self.assertIn("anna", FakeNextcloud.state["users"])
        self.assertEqual(self.call("DELETE", "/api/users/admin", {"confirm": "admin"})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/users/anna?confirm=anna")[0], 200)  # as the app sends it
        self.assertNotIn("anna", FakeNextcloud.state["users"])
        self.assertEqual(self.call("DELETE", "/api/users/anna", {"confirm": "anna"})[0], 400)  # gone

    def test_watcher_reports_once(self):
        self.sign_in()
        watcher = server.Watcher(self.tmp.name, nextcloud.Nextcloud(server.ApiHandler.settings.get))

        def titles():
            out = []
            for name in sorted(os.listdir(self.events)):
                with open(os.path.join(self.events, name)) as f:
                    e = json.load(f)
                    out.append((e["type"], e["title"]))
                os.unlink(os.path.join(self.events, name))
            return sorted(out)

        watcher.check()
        self.assertEqual(titles(), [("nextcloud_update", "App-Updates in Nextcloud")])
        watcher.check()
        self.assertEqual(titles(), [])
        FakeNextcloud.state["server_update"] = "35.0.3"
        FakeNextcloud.state["free"] = 5 * GB
        watcher = server.Watcher(self.tmp.name, watcher.nc)  # state survives a restart
        watcher.check()
        self.assertEqual(titles(), [("nextcloud_storage", "Nextcloud: Speicher wird knapp"),
                                    ("nextcloud_update", "Update für Nextcloud")])
        FakeNextcloud.state["down"] = True
        watcher.check()
        self.assertEqual(titles(), [])  # one miss is not enough
        watcher.check()
        self.assertEqual(titles(), [("nextcloud_down", "Nextcloud nicht erreichbar")])
        watcher.check()
        self.assertEqual(titles(), [])
        FakeNextcloud.state["down"] = False
        watcher.check()
        self.assertEqual(titles(), [("nextcloud_down", "Nextcloud wieder erreichbar")])


if __name__ == "__main__":
    unittest.main()
