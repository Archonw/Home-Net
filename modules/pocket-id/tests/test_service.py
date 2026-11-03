import json
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

import pocketid  # noqa: E402
import server  # noqa: E402

KEY = "test-key"


class FakePocketId(BaseHTTPRequestHandler):
    """Enough of the Pocket ID API (v2) for the module."""
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
        split = urllib.parse.urlsplit(self.path)
        path, query = split.path, dict(urllib.parse.parse_qsl(split.query))
        if path == "/.well-known/openid-configuration":
            return self.reply(200, {"issuer": "https://id.example.org"})
        if self.headers.get("X-API-Key") != KEY:
            return self.reply(401, {"error": "not signed in"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        s["calls"].append((self.command, path, body))
        page = int(query.get("pagination[page]", 1))

        def listing(items):
            limit = int(query.get("pagination[limit]", 20))
            chunk = items[(page - 1) * limit:page * limit]
            return {"data": chunk, "pagination": {"totalPages": max(1, -(-len(items) // limit)), "currentPage": page}}

        def user_out(u):
            return dict(u, userGroups=[{"id": g, "name": s["groups"][g]["name"]} for g in u["groups"]])

        if self.command == "GET" and path == "/api/users":
            return self.reply(200, listing([user_out(u) for u in s["users"].values()]))
        if self.command == "POST" and path == "/api/users":
            if body["username"] in [u["username"] for u in s["users"].values()]:
                return self.reply(400, {"error": "Username is already in use"})
            uid = f"00000000-0000-0000-0000-00000000000{len(s['users']) + 1}"
            s["users"][uid] = {"id": uid, "username": body["username"], "firstName": body["firstName"],
                               "lastName": body["lastName"], "displayName": body["displayName"],
                               "email": body["email"], "isAdmin": body["isAdmin"], "disabled": body["disabled"],
                               "customClaims": [], "groups": list(body.get("userGroupIds") or [])}
            return self.reply(201, user_out(s["users"][uid]))
        parts = path.split("/")
        if path.startswith("/api/users/"):
            uid = parts[3]
            if uid not in s["users"]:
                return self.reply(404, {"error": "User not found"})
            u = s["users"][uid]
            if len(parts) == 4 and self.command == "GET":
                return self.reply(200, user_out(u))
            if len(parts) == 4 and self.command == "PUT":
                u.update({k: body[k] for k in ("firstName", "lastName", "displayName", "email", "disabled", "isAdmin")})
                return self.reply(200, user_out(u))
            if len(parts) == 4 and self.command == "DELETE":
                del s["users"][uid]
                return self.reply(204)
            if parts[4] == "user-groups":
                u["groups"] = body["userGroupIds"]
                return self.reply(200, user_out(u))
            if parts[4] == "webauthn-credentials":
                return self.reply(200, [{"id": "k"}] * u.get("keys", 0))
            if parts[4] == "one-time-access-token":
                s["ttl"] = body.get("ttl")
                return self.reply(201, {"token": "ABC123"})
            if parts[4] == "one-time-access-email":
                return self.reply(204)
        if path.startswith("/api/custom-claims/user/"):
            s["users"][parts[4]]["customClaims"] = body
            return self.reply(200, body)
        if path == "/api/user-groups":
            return self.reply(200, listing([{"id": g, "name": v["name"], "friendlyName": v["name"].title()}
                                            for g, v in s["groups"].items()]))
        if path.startswith("/api/user-groups/"):
            g = s["groups"][parts[3]]
            return self.reply(200, {"id": parts[3], "name": g["name"], "friendlyName": g["name"].title(),
                                    "allowedOidcClients": [{"id": c, "name": s["clients"][c]["name"]}
                                                           for c in g["clients"]]})
        if path == "/api/oidc/clients":
            return self.reply(200, listing([{"id": c, "name": v["name"], "isGroupRestricted": v["restricted"]}
                                            for c, v in s["clients"].items()]))
        return self.reply(404, {"error": "no route"})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


def fresh_state():
    return {
        "calls": [],
        "groups": {"g-immich": {"name": "immich_users", "clients": ["c-immich"]},
                   "g-nc": {"name": "nextcloud_users", "clients": ["c-nc"]},
                   "g-empty": {"name": "familie", "clients": []}},
        "clients": {"c-immich": {"name": "Immich", "restricted": True}, "c-nc": {"name": "Nextcloud", "restricted": True},
                    "c-open": {"name": "Wiki", "restricted": False}},
        "users": {
            "u-jan": {"id": "u-jan", "username": "jan", "firstName": "Jan", "lastName": "B", "displayName": "Jan B",
                      "email": "jan@example.org", "isAdmin": True, "disabled": False, "keys": 2,
                      "customClaims": [{"key": "nextcloud_username", "value": "jan"}], "groups": ["g-immich", "g-nc"]},
            "u-sophie": {"id": "u-sophie", "username": "sophie", "firstName": "Sophie", "lastName": "", "displayName":
                         "Sophie", "email": None, "isAdmin": False, "disabled": False, "keys": 0,
                         "customClaims": [{"key": "nextcloud_username", "value": "sophie"},
                                          {"key": "team", "value": "a"}], "groups": ["g-nc"]},
        },
    }


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakePocketId)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        server.ApiHandler.settings = server.Settings(cls.tmp.name)
        server.ApiHandler.pid = pocketid.PocketId(server.ApiHandler.settings.get)
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.api.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        cls.api.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        FakePocketId.state = fresh_state()
        server.ApiHandler.settings.update({"url": f"http://127.0.0.1:{self.fake.server_address[1]}", "api_key": KEY})

    def call(self, method, path, body=None, role="owner"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Home-Net-Role": role, "X-Home-Net-User": "Jan",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_overview_lists_users_and_apps(self):
        code, data = self.call("GET", "/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(data["issuer"], "https://id.example.org")
        self.assertEqual([a["label"] for a in data["apps"]["switches"]], ["Immich", "Nextcloud"])
        self.assertEqual(data["apps"]["open"], ["Wiki"])
        jan = next(u for u in data["users"] if u["username"] == "jan")
        self.assertEqual(jan["passkeys"], 2)
        self.assertTrue(jan["admin"])
        self.assertEqual(sorted(jan["groups"]), ["g-immich", "g-nc"])
        self.assertNotIn("customClaims", jan)

    def test_only_managers(self):
        code, data = self.call("GET", "/api/overview", role="user")
        self.assertEqual(code, 403)
        self.assertEqual(self.call("GET", "/health", role="user")[0], 200)

    def test_without_key(self):
        server.ApiHandler.settings.update({"api_key": ""})
        code, data = self.call("GET", "/api/overview")
        self.assertEqual(code, 200)
        self.assertFalse(data["configured"])

    def test_key_from_file(self):
        server.ApiHandler.settings.update({"api_key": ""})
        Path(self.tmp.name, "api-key").write_text(KEY + "\n")
        try:
            code, data = self.call("GET", "/api/overview")
            self.assertTrue(data["configured"])
            self.assertEqual(data["error"], "")
            self.assertTrue(self.call("GET", "/api/settings")[1]["api_key_set"])
        finally:
            Path(self.tmp.name, "api-key").unlink()

    def test_wrong_key_is_explained(self):
        server.ApiHandler.settings.update({"api_key": "falsch"})
        code, data = self.call("GET", "/api/overview")
        self.assertIn("API-Schlüssel", data["error"])

    def test_create_user_with_apps_claim_and_invite(self):
        code, data = self.call("POST", "/api/users", {"username": "Christin", "first_name": "Christin",
                                                      "email": "c@example.org", "groups": ["g-immich"], "hours": 48})
        self.assertEqual(code, 201)
        users = FakePocketId.state["users"]
        new = users[data["id"]]
        self.assertEqual(new["username"], "christin")
        self.assertEqual(new["groups"], ["g-immich"])
        self.assertFalse(new["isAdmin"])
        # nextcloud_username always holds the username, "team" does not
        self.assertEqual(new["customClaims"], [{"key": "nextcloud_username", "value": "christin"}])
        self.assertEqual(data["invite"]["link"], "https://id.example.org/lc/ABC123")
        self.assertEqual(FakePocketId.state["ttl"], "48h")

    def test_create_user_errors(self):
        self.assertEqual(self.call("POST", "/api/users", {"username": "jan", "first_name": "X"})[0], 400)
        self.assertEqual(self.call("POST", "/api/users", {"username": "neu"})[0], 400)
        self.assertEqual(self.call("POST", "/api/users", {"first_name": "Neu"})[0], 400)

    def test_switch_app(self):
        self.assertEqual(self.call("PUT", "/api/users/u-sophie/apps", {"group": "g-immich", "on": True})[0], 200)
        self.assertEqual(sorted(FakePocketId.state["users"]["u-sophie"]["groups"]), ["g-immich", "g-nc"])
        self.call("PUT", "/api/users/u-sophie/apps", {"group": "g-nc", "on": False})
        self.assertEqual(FakePocketId.state["users"]["u-sophie"]["groups"], ["g-immich"])
        self.assertEqual(self.call("PUT", "/api/users/u-sophie/groups", {"groups": ["nope"]})[0], 400)

    def test_disable_and_delete(self):
        self.assertEqual(self.call("PUT", "/api/users/u-sophie", {"disabled": True})[0], 200)
        u = FakePocketId.state["users"]["u-sophie"]
        self.assertTrue(u["disabled"])
        self.assertEqual(u["firstName"], "Sophie")
        # Pocket-ID admins stay untouched
        self.assertEqual(self.call("PUT", "/api/users/u-jan", {"disabled": True})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/users/u-jan")[0], 400)
        self.assertEqual(self.call("DELETE", "/api/users/u-sophie")[0], 200)
        self.assertNotIn("u-sophie", FakePocketId.state["users"])
        self.assertEqual(self.call("DELETE", "/api/users/u-sophie")[0], 400)

    def test_rename(self):
        self.call("PUT", "/api/users/u-sophie", {"first_name": "Sophie", "last_name": "M", "email": "s@example.org"})
        u = FakePocketId.state["users"]["u-sophie"]
        self.assertEqual((u["displayName"], u["email"]), ("Sophie M", "s@example.org"))

    def test_invite_by_email(self):
        code, data = self.call("POST", "/api/users/u-sophie/invite", {"email": True})
        self.assertEqual(code, 200)
        self.assertTrue(data["sent"])
        self.assertIn(("POST", "/api/users/u-sophie/one-time-access-email", {"ttl": "72h"}),
                      FakePocketId.state["calls"])

    def test_paging(self):
        for i in range(130):
            FakePocketId.state["users"][f"x{i}"] = {"id": f"x{i}", "username": f"x{i}", "firstName": "X",
                                                    "lastName": "", "displayName": "", "email": None,
                                                    "isAdmin": False, "disabled": False, "groups": []}
        self.assertEqual(len(server.ApiHandler.pid.users()), 132)

    def test_settings_validation(self):
        self.assertEqual(self.call("PUT", "/api/settings", {"url": "kein url"})[0], 400)
        self.assertEqual(self.call("PUT", "/api/settings", {"api_key": "a b"})[0], 400)

    def test_unreachable(self):
        server.ApiHandler.settings.update({"url": "http://127.0.0.1:9"})
        code, data = self.call("GET", "/api/overview")
        self.assertIn("nicht erreichbar", data["error"])
        self.assertEqual(self.call("PUT", "/api/users/u-sophie", {"disabled": True})[0], 502)


class ClaimTest(unittest.TestCase):
    def test_username_claims(self):
        users = [{"username": "a", "customClaims": [{"key": "nc", "value": "a"}, {"key": "x", "value": "a"}]},
                 {"username": "b", "customClaims": [{"key": "nc", "value": "b"}, {"key": "x", "value": "z"}]},
                 {"username": "c"}]
        self.assertEqual(pocketid.username_claims(users, "d"), [{"key": "nc", "value": "d"}])
        self.assertEqual(pocketid.username_claims([], "d"), [])


if __name__ == "__main__":
    unittest.main()
