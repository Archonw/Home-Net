import base64
import json
import os
import socketserver
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

import host  # noqa: E402
import server  # noqa: E402

VAR = '''version="7.3.4"
NAME="tower"
COMMENT=""
SYS_MODEL="Custom"
timeZone="Europe/Berlin"
WORKGROUP="WORKGROUP"
localMaster="yes"
hideDotFiles="no"
enableFruit="yes"
serverMultiChannel="no"
USE_NETBIOS="no"
USE_WSD="no"
WSD2_OPT=""
USE_NTP="yes"
NTP_SERVER1="ptbtime1.ptb.de"
NTP_SERVER2=""
NTP_SERVER3=""
NTP_SERVER4=""
startArray="yes"
spindownDelay="15"
spinupGroups="no"
shutdownTimeout="90"
defaultFsType="xfs"
poll_attributes="30"
queueDepth="auto"
nr_requests="Auto"
md_scheduler="auto"
md_num_stripes="1280"
md_queue_limit="80"
md_sync_limit="5"
md_write_method="auto"
shareSMBEnabled="yes"
shareNFSEnabled="yes"
fuse_remember="330"
reservedNames="parity,parity2,disk,disks,cache,flash,user,user0"
sbSynced="1793509201"
sbSynced2="1793606346"
sbSyncErrs="0"
sbSyncExit="0"
mdNumDisabled="1"
mdNumInvalid="1"
mdNumMissing="0"
mdResync="0"
mdResyncPos="0"
mdResyncDt="0"
mdResyncAction="check P"
mdResyncSize="9766436812"
mdState="STARTED"
shareMoverActive="no"
csrf_token="TOKEN123"
'''

SHARES = '''["Bilder"]
name="Bilder"
nameOrig="Bilder"
comment="Fotos"
allocator="highwater"
splitLevel=""
floor="10000000"
include="disk1,disk2"
exclude=""
useCache="yes"
cachePool="cache"
cachePool2=""
cow="auto"
color="green-on"
free="4434499780"
used="16070484648"
["Scripte"]
name="Scripte"
nameOrig="Scripte"
comment=""
allocator="highwater"
splitLevel=""
floor="10000000"
include=""
exclude=""
useCache="no"
cachePool=""
cachePool2=""
cow="auto"
["appdata"]
name="appdata"
nameOrig="appdata"
comment=""
allocator="highwater"
splitLevel=""
floor="0"
include=""
exclude=""
useCache="only"
cachePool="cache"
cachePool2=""
cow="auto"
'''

SEC = '''["disk1"]
export="-"
security="public"
readList=""
writeList=""
["Bilder"]
export="e"
caseSensitive="auto"
security="private"
readList="sophie"
writeList="jan"
volsizelimit=""
["Scripte"]
export="e"
caseSensitive="auto"
security="secure"
readList=""
writeList="jan"
volsizelimit=""
["appdata"]
export="-"
caseSensitive="auto"
security="public"
readList=""
writeList=""
volsizelimit=""
'''

USERS = '''["root"]
idx="0"
name="root"
desc="Console and webGui login account"
passwd="yes"
["jan"]
idx="1"
name="jan"
desc=""
passwd="yes"
["sophie"]
idx="2"
name="sophie"
desc=""
passwd="yes"
'''

DISKS = '''["parity"]
idx="0"
name="parity"
device="sdj"
id="HUH721010ALE601_X"
status="DISK_OK"
temp="35"
numErrors="0"
type="Parity"
rotational="1"
color="green-on"
["disk1"]
idx="1"
name="disk1"
device="sdi"
id="HUH721010ALE601_Y"
status="DISK_OK"
temp="46"
numErrors="0"
type="Data"
rotational="1"
color="green-on"
fsType="xfs"
fsStatus="Mounted"
fsSize="9764349900"
fsFree="1506786512"
["disk3"]
name="disk3"
device=""
status="DISK_NP"
temp="*"
type="Data"
["parity2"]
name="parity2"
device=""
status="DISK_NP_DSBL"
type="Parity"
["cache"]
name="cache"
device="nvme0n1"
id="Samsung_SSD"
status="DISK_OK"
temp="39"
type="Cache"
rotational="0"
fsType="xfs"
fsStatus="Mounted"
fsSize="976284628"
fsFree="418149260"
'''

DYNAMIX = '''[confirm]
down="1"
[display]
date="%c"
hot="45"
max="55"
[parity]
mode="0"
'''


class FakeEmhttpd(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_emhttpd(path, posts, answer):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            posts.append((self.path, dict(urllib.parse.parse_qsl(self.rfile.read(length).decode(),
                                                                  keep_blank_values=True))))
            body = answer[0].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = FakeEmhttpd(path, Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_api(path, calls):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length))
            calls.append((self.headers.get("x-api-key"), payload))
            q = payload["query"]
            if "containers" in q:
                data = {"docker": {"containers": [
                    {"id": "abc:1", "names": ["/jellyfin"], "image": "jf", "state": "RUNNING", "status": "Up", "autoStart": True},
                    {"id": "abc:2", "names": ["/home-net-gateway"], "image": "hn", "state": "RUNNING", "status": "Up", "autoStart": True}]}}
            elif "domains" in q:
                data = {"vms": {"domains": [{"id": "vm:1", "name": "Windows", "state": "SHUTOFF"}]}}
            else:
                data = {"ok": True}
            body = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = FakeEmhttpd(path, Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ini = root / "emhttp"
        self.ini.mkdir()
        for name, text in (("var.ini", VAR), ("shares.ini", SHARES), ("sec.ini", SEC), ("users.ini", USERS),
                           ("disks.ini", DISKS), ("sec_nfs.ini", "")):
            (self.ini / name).write_text(text)
        self.shares = root / "user"
        (self.shares / "Bilder" / "Urlaub").mkdir(parents=True)
        (self.shares / "Bilder" / "Urlaub" / "a.jpg").write_bytes(b"JPEGDATA")
        (self.shares / "Scripte").mkdir()
        (root / "geheim.txt").write_text("nein")
        os.symlink(root / "geheim.txt", self.shares / "Scripte" / "raus")
        self.cfg = root / "dynamix.cfg"
        self.cfg.write_text(DYNAMIX)
        host.DYNAMIX_CFG = str(self.cfg)
        self.posts, self.answer, self.calls = [], [""], []
        self.emhttpd = make_emhttpd(str(root / "emhttpd.sock"), self.posts, self.answer)
        self.api_srv = make_api(str(root / "api.sock"), self.calls)
        self.settings = server.Settings(str(root))
        self.srv = host.Server(self.settings.get, root=str(self.ini), shares_root=str(self.shares),
                               emhttp=host.Emhttp(str(root / "emhttpd.sock"), root=str(self.ini)),
                               api=host.UnraidApi(lambda: self.settings.get()["api_key"], str(root / "api.sock")))
        self.notes = root / "notifications"
        (self.notes / "unread").mkdir(parents=True)

    def tearDown(self):
        self.emhttpd.shutdown()
        self.emhttpd.server_close()
        self.api_srv.shutdown()
        self.api_srv.server_close()
        self.tmp.cleanup()

    def last(self):
        path, fields = self.posts[-1]
        self.assertEqual(path, "/update")
        self.assertEqual(fields.pop("csrf_token"), "TOKEN123")
        return fields


class HostTests(Base):
    def test_reads_state(self):
        shares = {s["name"]: s for s in self.srv.shares()}
        self.assertEqual(shares["Bilder"]["storage"], {"primary": "cache", "secondary": "array", "mover": "to_secondary"})
        self.assertEqual(shares["Scripte"]["storage"], {"primary": "array", "secondary": "", "mover": ""})
        self.assertEqual(shares["appdata"]["storage"]["secondary"], "")
        self.assertEqual(shares["Bilder"]["smb"]["access"], {"sophie": "read-only", "jan": "read-write"})
        self.assertEqual([u["name"] for u in self.srv.users()], ["root", "jan", "sophie"])
        disks = {d["name"]: d for d in self.srv.disks()}
        self.assertEqual(set(disks), {"parity", "disk1", "cache"})  # empty slots left out
        self.assertEqual(disks["disk1"]["temp"], 46)
        self.assertTrue(disks["cache"]["ssd"])
        self.assertEqual(self.srv.pools(), ["cache"])
        self.assertTrue(self.srv.writable())
        self.assertEqual(self.srv.array()["check"]["last"], 1793606346)

    def test_untested_version_is_read_only(self):
        (self.ini / "var.ini").write_text(VAR.replace('version="7.3.4"', 'version="8.0.0"'))
        self.assertFalse(self.srv.writable())
        with self.assertRaises(PermissionError):
            self.srv.add_user("anna", "", "pw")
        self.assertEqual(self.posts, [])
        self.settings.update({"allow_untested": True})
        self.srv.add_user("anna", "", "pw")
        self.assertEqual(len(self.posts), 1)

    def test_add_user_sends_web_form(self):
        self.srv.add_user("anna", "Tochter", "geheim ä")
        encoded = base64.b64encode("geheim ä".encode()).decode()
        self.assertEqual(self.last(), {"userName": "anna", "userDesc": "Tochter", "userPassword": encoded,
                                       "userPasswordConf": encoded, "cmdUserEdit": "Add"})
        for bad in ("Anna", "1anna", "an na", ""):
            with self.assertRaises(ValueError):
                self.srv.add_user(bad, "", "pw")
        with self.assertRaises(ValueError):
            self.srv.add_user("jan", "", "pw")
        with self.assertRaises(ValueError):
            self.srv.add_user("anna2", "a:b", "pw")

    def test_user_password_and_delete(self):
        self.srv.set_password("jan", "neu")
        self.assertEqual(self.last()["cmdUserEdit"], "Change")
        self.srv.delete_user("sophie")
        self.assertEqual(self.last(), {"userName": "sophie", "userDesc": "", "confirmDelete": "on", "cmdUserEdit": "Delete"})
        with self.assertRaises(ValueError):
            self.srv.delete_user("root")
        with self.assertRaises(LookupError):
            self.srv.delete_user("niemand")

    def test_emhttpd_error_is_reported(self):
        self.answer[0] = "<p>User name already exists</p>"
        with self.assertRaises(host.HostError) as ctx:
            self.srv.add_user("anna", "", "pw")
        self.assertIn("already exists", str(ctx.exception))

    def test_add_share(self):
        name = self.srv.save_share("", {"name": "Filme", "comment": "Kino",
                                        "storage": {"primary": "cache", "secondary": "array", "mover": "to_secondary"}})
        self.assertEqual(name, "Filme")
        self.assertEqual(self.last(), {"shareNameOrig": "", "shareName": "Filme", "shareComment": "Kino",
                                       "shareAllocator": "highwater", "shareSplitLevel": "", "shareInclude": "",
                                       "shareExclude": "", "shareFloor": "", "shareUseCache": "yes",
                                       "shareCachePool": "cache", "shareCachePool2": "", "shareCOW": "auto",
                                       "cmdEditShare": "Add Share"})
        for bad in ("bilder", "1x", "a b", "x.", "cache", "disk", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.srv.save_share("", {"name": bad})
        with self.assertRaises(ValueError):
            self.srv.save_share("", {"name": "X", "storage": {"primary": "gibtsnicht"}})

    def test_edit_share_keeps_other_values(self):
        self.srv.save_share("Bilder", {"comment": "Neu"})
        fields = self.last()
        self.assertEqual(fields["shareInclude"], "disk1,disk2")
        self.assertEqual(fields["shareFloor"], "10000000")
        self.assertEqual(fields["shareUseCache"], "yes")
        self.assertEqual(fields["cmdEditShare"], "Apply")
        self.assertNotIn("shareCOW", fields)
        self.srv.save_share("Bilder", {"storage": {"primary": "cache", "secondary": "array", "mover": "to_primary"}})
        self.assertEqual(self.last()["shareUseCache"], "prefer")
        self.srv.save_share("Bilder", {"storage": {"primary": "array"}})
        self.assertEqual(self.last()["shareUseCache"], "no")
        self.srv.save_share("Bilder", {"storage": {"primary": "cache"}})
        self.assertEqual(self.last()["shareUseCache"], "only")

    def test_delete_share_only_when_empty(self):
        with self.assertRaises(ValueError):
            self.srv.delete_share("Bilder")
        (self.shares / "Scripte" / "raus").unlink()
        self.srv.delete_share("Scripte")
        fields = self.last()
        self.assertEqual((fields["cmdEditShare"], fields["confirmDelete"], fields["shareNameOrig"]), ("Delete", "on", "Scripte"))

    def test_share_security_and_access(self):
        self.srv.share_security("Bilder", {"export": "eh", "security": "secure"})
        self.assertEqual(self.last(), {"shareName": "Bilder", "shareExport": "eh", "shareSecurity": "secure",
                                       "shareCaseSensitive": "auto", "shareVolsizelimit": "",
                                       "changeShareSecurity": "Apply"})
        self.srv.share_access("Bilder", {"sophie": "read-write"})
        self.assertEqual(self.last(), {"shareName": "Bilder", "userAccess.0": "no-access", "userAccess.1": "read-write",
                                       "userAccess.2": "read-write", "changeShareAccess": "Apply"})
        self.srv.share_access("Scripte", {"sophie": "no-access"})
        self.assertEqual(self.last()["userAccess.2"], "read-only")  # secure: everybody may read
        with self.assertRaises(ValueError):
            self.srv.share_access("appdata", {"jan": "read-only"})
        with self.assertRaises(ValueError):
            self.srv.share_security("Bilder", {"security": "offen"})

    def test_general_settings(self):
        self.srv.save_general("identity", {"NAME": "speicher", "COMMENT": "Keller"})
        self.assertEqual(self.last(), {"NAME": "speicher", "COMMENT": "Keller", "SYS_MODEL": "Custom", "changeNames": "Apply"})
        with self.assertRaises(ValueError):
            self.srv.save_general("identity", {"NAME": "mein server"})
        self.srv.save_general("disks", {"spindownDelay": "30"})
        fields = self.last()
        self.assertEqual((fields["spindownDelay"], fields["md_num_stripes"], fields["startArray"], fields["changeDisk"]),
                         ("30", "1280", "yes", "Apply"))
        self.srv.save_general("time", {"NTP_SERVER2": "de.pool.ntp.org"})
        fields = self.last()
        self.assertEqual((fields["NTP_SERVER1"], fields["NTP_SERVER2"], fields["timeZone"]),
                         ("ptbtime1.ptb.de", "de.pool.ntp.org", "Europe/Berlin"))
        with self.assertRaises(ValueError):
            self.srv.save_general("smb", {"shareSMBEnabled": "vielleicht"})
        with self.assertRaises(ValueError):
            self.srv.save_general("identity", {"COMMENT": 'a"b'})
        with self.assertRaises(LookupError):
            self.srv.save_general("gibtsnicht", {})

    def test_temperatures_in_dynamix_cfg(self):
        self.assertEqual(self.srv.temps()["hot"], 45)
        self.assertEqual(self.srv.temps()["hotssd"], 60)
        self.srv.set_temps({"hot": "48"})
        text = self.cfg.read_text()
        self.assertIn('hot="48"', text)
        self.assertIn('date="%c"', text)
        self.assertIn("[parity]\nmode=\"0\"", text)
        self.assertEqual(self.srv.temps()["hot"], 48)
        with self.assertRaises(ValueError):
            self.srv.set_temps({"hot": "60"})  # above critical
        with self.assertRaises(ValueError):
            self.srv.set_temps({"hot": "200"})

    def test_array_commands(self):
        self.srv.array_command("check")
        self.assertEqual(self.last(), {"startState": "STARTED", "optionCorrect": "correct", "cmdCheck": "Check"})
        with self.assertRaises(ValueError):
            self.srv.array_command("pause")
        with self.assertRaises(LookupError):
            self.srv.array_command("stop")  # stopping the array would stop Home-Net itself

    def test_docker_and_vms(self):
        with self.assertRaises(host.HostError):
            self.srv.containers()
        self.settings.update({"api_key": "key-1"})
        containers = self.srv.containers()
        self.assertEqual([c["name"] for c in containers], ["home-net-gateway", "jellyfin"])
        self.assertTrue(containers[0]["protected"])
        self.assertEqual(self.calls[-1][0], "key-1")
        with self.assertRaises(ValueError):
            self.srv.container_command("abc:2", "stop")
        self.srv.container_command("abc:1", "restart")
        self.assertIn("stop(id: $id)", self.calls[-2][1]["query"])
        self.assertIn("start(id: $id)", self.calls[-1][1]["query"])
        self.assertEqual(self.calls[-1][1]["variables"], {"id": "abc:1"})
        self.assertEqual(self.srv.vms()[0]["state"], "shutoff")
        self.srv.vm_command("vm:1", "start")
        self.assertIn("vm { start(id: $id)", self.calls[-1][1]["query"])

    def test_files_stay_inside_shares(self):
        listing = self.srv.browse("Bilder/Urlaub")
        self.assertEqual(listing["entries"][0]["name"], "a.jpg")
        self.assertEqual(self.srv.browse("")["path"], "")
        for bad in ("../", "Bilder/../../", "Scripte/raus"):
            with self.assertRaises((PermissionError, LookupError), msg=bad):
                self.srv.browse(bad)
        with self.assertRaises(PermissionError):
            self.srv.resolve("Scripte/raus")

    def test_ini_parser(self):
        self.assertEqual(host.parse_ini('["a b"]\nx="1"\n[c]\ny=2\n'), {"a b": {"x": "1"}, "c": {"y": "2"}})
        self.assertEqual(host.parse_ini('k="v"\n'), {"k": "v"})


class ForwarderTests(Base):
    def test_only_new_notifications_are_forwarded(self):
        events = Path(self.tmp.name) / "events"
        events.mkdir()
        server.EVENTS_DIR = str(events)
        (self.notes / "unread" / "old.notify").write_text('event="Alt"\nsubject="alt"\nimportance="alert"\n')
        fw = server.Forwarder(self.settings, folder=str(self.notes), state_dir=self.tmp.name)
        fw.tick()
        self.assertEqual(list(events.iterdir()), [])
        (self.notes / "unread" / "new.notify").write_text(
            'timestamp=1\nevent="Unraid Disk 1 temperature"\nsubject="Warning [TOWER] - Disk 1 is hot (46 C)"\n'
            'description="HUH721010"\nimportance="warning"\n')
        fw.tick()
        files = list(events.glob("*.json"))
        self.assertEqual(len(files), 1)
        event = json.loads(files[0].read_text())
        self.assertEqual(event["type"], "unraid_warning")
        self.assertIn("Disk 1 is hot", event["title"])
        fw.tick()
        self.assertEqual(len(list(events.glob("*.json"))), 1)
        again = server.Forwarder(self.settings, folder=str(self.notes), state_dir=self.tmp.name)
        again.tick()  # remembers what it already forwarded
        self.assertEqual(len(list(events.glob("*.json"))), 1)
        self.assertEqual(again.unread()[0]["importance"], "warning")
        server.EVENTS_DIR = ""


class HttpTests(Base):
    def setUp(self):
        super().setUp()
        server.ApiHandler.server_api = self.srv
        server.ApiHandler.settings = self.settings
        server.ApiHandler.forwarder = server.Forwarder(self.settings, folder=str(self.notes), state_dir=self.tmp.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        super().tearDown()

    def call(self, path, method="GET", body=None, role="owner"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Home-Net-Role": role})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_plain_users_only_see_the_overview(self):
        code, data = self.call("/api/overview", role="user")
        self.assertEqual(code, 200)
        self.assertFalse(data["manager"])
        self.assertEqual(data["name"], "tower")
        self.assertEqual(data["temps"]["hot"], 45)
        for path in ("/api/shares", "/api/users", "/api/files", "/api/settings", "/api/general"):
            self.assertEqual(self.call(path, role="user")[0], 403, path)
        self.assertEqual(self.call("/api/users", "POST", {"name": "x", "password": "y"}, role="user")[0], 403)
        self.assertEqual(self.posts, [])

    def test_manager_flow(self):
        code, data = self.call("/api/shares", role="admin")
        self.assertEqual(code, 200)
        self.assertNotIn("raw", data["shares"][0])
        self.assertEqual(data["users"], ["jan", "sophie"])
        code, data = self.call("/api/users", role="admin")
        self.assertEqual(data["users"][1]["rights"], {"Bilder": "read-write", "Scripte": "read-write"})
        self.assertEqual(data["users"][2]["rights"], {"Bilder": "read-only", "Scripte": "read-only"})
        self.assertEqual(self.call("/api/users", "POST", {"name": "anna", "password": "pw"}, role="admin")[0], 201)
        self.assertEqual(self.call("/api/users/Anna%20B", "DELETE", {}, role="admin")[0], 404)
        self.assertEqual(self.call("/api/shares/Bilder/access", "PUT", {"access": {"jan": "read-only"}}, role="admin")[0], 200)
        self.assertEqual(self.last()["userAccess.1"], "read-only")
        code, data = self.call("/api/files?path=Bilder/Urlaub", role="admin")
        self.assertEqual(data["entries"][0]["name"], "a.jpg")
        code, raw = self.call("/api/download?path=Bilder/Urlaub/a.jpg", role="admin")
        self.assertEqual(raw, b"JPEGDATA")
        self.assertEqual(self.call("/api/download?path=../geheim.txt", role="admin")[0], 403)
        self.assertEqual(self.call("/api/array/check", "POST", {}, role="admin")[0], 202)
        self.assertEqual(self.call("/api/general/temps", "PUT", {"hot": "48"}, role="admin")[0], 200)

    def test_settings_hide_the_key(self):
        code, data = self.call("/api/settings", "PUT", {"api_key": "abc-123"}, role="admin")
        self.assertEqual(code, 200)
        self.assertTrue(data["api_key_set"])
        self.assertNotIn("abc-123", json.dumps(data))
        self.assertEqual(self.call("/api/settings", "PUT", {"allow_untested": True}, role="admin")[0], 403)
        self.assertEqual(self.call("/api/settings", "PUT", {"api_key": "x y"}, role="owner")[0], 400)
        code, data = self.call("/api/docker", role="owner")
        self.assertEqual([c["name"] for c in data["containers"]], ["home-net-gateway", "jellyfin"])

    def test_create_share_with_rights(self):
        # the fake emhttpd does not change shares.ini, so add the share like Unraid would
        def add_later():
            import time
            time.sleep(0.3)
            (self.ini / "shares.ini").write_text(SHARES + '["Filme"]\nname="Filme"\nuseCache="no"\n')
        threading.Thread(target=add_later).start()
        code, data = self.call("/api/shares", "POST", {"name": "Filme", "storage": {"primary": "array"},
                                                        "smb": {"export": "e", "security": "private"},
                                                        "access": {"jan": "read-write"}})
        self.assertEqual(code, 201)
        kinds = [next(k for k in f if k.startswith(("cmd", "change"))) for _, f in self.posts]
        self.assertEqual(kinds, ["cmdEditShare", "changeShareSecurity", "changeShareAccess"])

    def test_page(self):
        code, raw = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Unraid", raw)


if __name__ == "__main__":
    unittest.main()
