import base64
import socket
import struct
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import proxydhcp  # noqa: E402
import server  # noqa: E402
import store as store_mod  # noqa: E402
from store import Store, due_key, next_due, pc_from_form  # noqa: E402

MAC = "52:54:00:12:34:56"


class FakeArchive:
    def __init__(self):
        self.cache = {}
        self.runs_list = []
        self.tidied = []

    def ensure(self, pc):
        pass

    def runs(self, pc, fresh=False):
        return self.runs_list

    def size(self, pc_id):
        return 123

    def tidy(self, pc, keep, also=()):
        self.tidied.append((pc["id"], keep, tuple(also)))

    def path(self, pc_id):
        return "/nonexistent/" + pc_id


class FakeServices:
    def __init__(self):
        self.changed = 0

    def server_ip(self):
        return "192.168.1.10"

    def users_changed(self):
        self.changed += 1

    def netboot_state(self):
        return {"enabled": True, "running": True, "error": "", "server_ip": "192.168.1.10", "configured_ip": ""}

    def restart_netboot(self):
        pass


def at(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


class Forms(unittest.TestCase):
    def test_mac_is_normalised_and_checked(self):
        pc = pc_from_form({"name": "Büro", "macs": "52-54-00-12-34-56, 5254001234AB"})
        self.assertEqual(pc["macs"], [MAC, "52:54:00:12:34:ab"])
        with self.assertRaises(ValueError):
            pc_from_form({"name": "Büro", "macs": "kaputt"})
        with self.assertRaises(ValueError):
            pc_from_form({"name": "a/b", "macs": MAC})
        with self.assertRaises(ValueError):
            pc_from_form({"name": "Büro", "macs": ""})

    def test_new_pc_gets_own_secrets(self):
        a, b = pc_from_form({"name": "A", "macs": MAC}), pc_from_form({"name": "B", "macs": MAC})
        self.assertNotEqual(a["repo_password"], b["repo_password"])
        self.assertNotEqual(a["rest_password"], a["repo_password"])
        self.assertRegex(a["id"], r"^[0-9a-f]{12}$")


class Schedule(unittest.TestCase):
    def test_slots(self):
        daily = {"schedule": "daily", "hour": 3}
        self.assertIsNone(due_key(daily, at("2026-11-02 02:59")))
        self.assertEqual(due_key(daily, at("2026-11-02 03:00")), "2026-11-02")
        self.assertEqual(due_key(daily, at("2026-11-02 23:00")), "2026-11-02")
        weekly = {"schedule": "weekly", "hour": 3, "weekday": 6}
        self.assertIsNone(due_key(weekly, at("2026-11-02 04:00")))  # a Monday
        self.assertIsNotNone(due_key(weekly, at("2026-11-08 04:00")))  # a Sunday
        monthly = {"schedule": "monthly", "hour": 3}
        self.assertIsNone(due_key(monthly, at("2026-11-02 04:00")))
        self.assertEqual(due_key(monthly, at("2026-12-01 04:00")), "2026-12")
        self.assertIsNone(due_key({"schedule": "off", "hour": 0}, at("2026-11-02 04:00")))

    def test_next_due(self):
        self.assertEqual(next_due({"schedule": "daily", "hour": 3}, at("2026-11-02 10:00")), at("2026-11-03 03:00"))
        self.assertEqual(next_due({"schedule": "off", "hour": 3}, at("2026-11-02 10:00")), 0)

    def test_each_slot_once(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            s.create({"name": "A", "macs": MAC, "schedule": "daily", "hour": 3})
            self.assertEqual(len(s.take_due(at("2026-11-02 03:05"))), 1)
            self.assertEqual(s.take_due(at("2026-11-02 05:00")), [])
            self.assertEqual(len(Store(d).take_due(at("2026-11-03 03:00"))), 1)


class Jobs(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.s = Store(folder.name)
        self.pc = self.s.create({"name": "A", "macs": MAC})

    def test_one_task_at_a_time(self):
        self.s.arm(self.pc["id"], "backup", "manual", "reboot")
        with self.assertRaises(ValueError):
            self.s.arm(self.pc["id"], "backup", "manual", "reboot")

    def test_missed_and_silent(self):
        job = self.s.arm(self.pc["id"], "backup", "auto", "poweroff")
        self.assertEqual(self.s.expired(job["created"] + 60), [])
        self.assertEqual(self.s.expired(job["created"] + store_mod.ARM_SECONDS["auto"] + 1), [(self.pc["id"], "missed")])
        self.s.touch(self.pc["id"], state="running")
        self.assertEqual(self.s.expired(time.time() + store_mod.SILENT_SECONDS + 1), [(self.pc["id"], "silent")])

    def test_finish_records_history(self):
        self.s.arm(self.pc["id"], "backup", "manual", "reboot")
        self.s.finish(self.pc["id"], "ok", "fertig", {"d0p1": {"added": 5, "size": 10}, "d0p2": {"added": 1, "size": 2}})
        entry = self.s.history(self.pc["id"])[0]
        self.assertEqual((entry["result"], entry["added"], entry["processed"]), ("ok", 6, 12))
        self.assertIsNone(self.s.job(self.pc["id"]))

    def test_waiting_pc_is_matched_and_replaced(self):
        first = self.s.hello({"mac": MAC.upper(), "product": "X"})
        second = self.s.hello({"mac": MAC, "product": "X"})
        listed = self.s.waiting_list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["pc"], self.pc["id"])
        self.assertIsNone(self.s.waiting(first))
        self.assertIsNotNone(self.s.waiting(second))
        self.assertEqual(self.s.waiting_list(["Firma"]), [])


class Http(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.events = Path(folder.name, "events")
        self.events.mkdir()
        self.old_events = server.EVENTS_DIR
        server.EVENTS_DIR = str(self.events)
        self.addCleanup(setattr, server, "EVENTS_DIR", self.old_events)
        self.woken = []
        self.old_wake = server.wake
        server.wake = self.woken.extend
        self.addCleanup(setattr, server, "wake", self.old_wake)
        st = Store(folder.name)
        self.engine = server.Engine(st, FakeArchive(), FakeServices())
        self.engine.sizes = {}
        server.Base.engine = self.engine
        self.boot = ThreadingHTTPServer(("127.0.0.1", 0), server.BootHandler)
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        for srv in (self.boot, self.api):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        self.pc = st.create({"name": "Büro", "macs": MAC, "category": "Firma"})

    def call(self, srv, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", data=data, method=method,
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, raw if path.startswith("/boot/") else json.loads(raw)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    @staticmethod
    def user(role="user", categories=("*",)):
        return {"X-Home-Net-Role": role, "X-Home-Net-User": base64.b64encode(b"Kind").decode(),
                "X-Home-Net-Categories": base64.b64encode(json.dumps(list(categories)).encode()).decode()}

    def test_pc_without_task_starts_its_own_disk(self):
        _, text = self.call(self.boot, "GET", f"/boot/grub/{MAC}")
        self.assertIn(b"exit", text)
        self.assertNotIn(b"linux", text)
        _, text = self.call(self.boot, "GET", "/boot/ipxe/aa:bb:cc:dd:ee:ff")
        self.assertIn(b"exit", text)
        self.assertEqual(self.engine.store.seen_unknown()[0]["mac"], "aa:bb:cc:dd:ee:ff")

    def test_backup_from_netboot_to_result(self):
        code, _ = self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/backup", {"after": "poweroff"}, self.user())
        self.assertEqual(code, 202)
        self.assertEqual(self.woken, [MAC])
        _, text = self.call(self.boot, "GET", f"/boot/grub/{MAC}")
        token = text.decode().split("hn_token=")[1].split()[0]
        self.assertIn(b"linux (http,192.168.1.10:8395)/boot/vmlinuz", text)
        _, ipxe = self.call(self.boot, "GET", f"/boot/ipxe/{MAC}")
        self.assertIn(token.encode(), ipxe)
        _, job = self.call(self.boot, "GET", "/live/job", headers={"X-Token": token})
        self.assertEqual((job["action"], job["after"]), ("backup", "poweroff"))
        self.assertTrue(job["repository"].startswith(f"rest:http://{self.pc['id']}:"))
        _, none = self.call(self.boot, "GET", "/live/job", headers={"X-Token": "falsch"})
        self.assertEqual(none["action"], "none")
        _, reply = self.call(self.boot, "POST", "/live/progress", {"step": "x", "percent": 40}, {"X-Token": token})
        self.assertFalse(reply["cancel"])
        _, d = self.call(self.api, "GET", f"/api/pcs/{self.pc['id']}", headers=self.user())
        self.assertEqual(d["job"]["progress"]["percent"], 40)
        self.call(self.boot, "POST", "/live/done", {"ok": True, "message": "fertig", "stats": {"d0p1": {"added": 9}}},
                  {"X-Token": token})
        _, d = self.call(self.api, "GET", f"/api/pcs/{self.pc['id']}", headers=self.user())
        self.assertIsNone(d["job"])
        self.assertEqual(d["history"][0]["result"], "ok")
        self.assertNotIn("log", d["history"][0])
        sent = [json.loads(p.read_text()) for p in self.events.glob("*.json")]
        self.assertEqual([e["type"] for e in sent], ["backup_ok"])
        self.assertEqual(sent[0]["category"], "Firma")
        self.assertEqual(self.engine.archive.tidied[0][0], self.pc["id"])
        _, text = self.call(self.boot, "GET", f"/boot/grub/{MAC}")
        self.assertNotIn(token.encode(), text)

    def test_cancel_reaches_the_pc(self):
        self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/backup", {}, self.user())
        _, text = self.call(self.boot, "GET", f"/boot/grub/{MAC}")
        token = text.decode().split("hn_token=")[1].split()[0]
        self.call(self.boot, "GET", "/live/job", headers={"X-Token": token})
        self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/cancel", {}, self.user())
        _, reply = self.call(self.boot, "POST", "/live/progress", {}, {"X-Token": token})
        self.assertTrue(reply["cancel"])
        self.call(self.boot, "POST", "/live/done", {"ok": False, "message": "Abgebrochen"}, {"X-Token": token})
        self.assertEqual(self.engine.store.history(self.pc["id"])[0]["result"], "cancelled")
        self.assertEqual(list(self.events.glob("*.json")), [])

    def test_missed_start_is_reported(self):
        self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/backup", {}, self.user())
        self.engine.tick(time.time() + 2 * 3600)
        self.assertEqual(self.engine.store.history(self.pc["id"])[0]["result"], "missed")
        self.assertEqual(json.loads(next(self.events.glob("*.json")).read_text())["type"], "pc_not_reached")

    def test_rights(self):
        code, _ = self.call(self.api, "GET", f"/api/pcs/{self.pc['id']}", headers=self.user(categories=["Familie"]))
        self.assertEqual(code, 404)
        _, overview = self.call(self.api, "GET", "/api/overview", headers=self.user(categories=["Familie"]))
        self.assertEqual(overview["pcs"], [])
        self.engine.archive.runs_list = [{"id": "2026-11-01_030000", "complete": True, "label": "x", "files": [],
                                          "snapshots": []}]
        code, _ = self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/restore", {"run": "2026-11-01_030000"},
                            self.user())
        self.assertEqual(code, 403)
        for method, path in (("POST", "/api/pcs"), ("PUT", "/api/settings"), ("GET", f"/api/pcs/{self.pc['id']}/config"),
                             ("DELETE", f"/api/pcs/{self.pc['id']}")):
            code, _ = self.call(self.api, method, path, {"name": "x", "macs": "aa:bb:cc:dd:ee:01"}, self.user())
            self.assertEqual(code, 403, path)
        code, cfg = self.call(self.api, "GET", f"/api/pcs/{self.pc['id']}/config", headers=self.user("admin"))
        self.assertEqual(code, 200)
        self.assertFalse(any(k.endswith("password") for k in cfg))
        code, _ = self.call(self.api, "POST", f"/api/pcs/{self.pc['id']}/restore", {"run": "2026-11-01_030000",
                                                                                    "parts": ["d0p2", "x;rm"]},
                            self.user("admin"))
        self.assertEqual(code, 202)
        self.assertEqual(self.engine.store.job(self.pc["id"])["parts"], ["d0p2"])

    def test_stick_pc_waits_and_gets_assigned(self):
        _, hello = self.call(self.boot, "POST", "/live/hello", {"mac": "aa:bb:cc:dd:ee:02", "product": "ThinkCentre"})
        token = hello["token"]
        _, job = self.call(self.boot, "GET", "/live/job", headers={"X-Token": token})
        self.assertEqual(job["action"], "wait")
        _, overview = self.call(self.api, "GET", "/api/overview", headers=self.user("owner"))
        short = overview["waiting"][0]["id"]
        code, _ = self.call(self.api, "POST", f"/api/waiting/{short}/assign", {"action": "backup"}, self.user())
        self.assertEqual(code, 403)
        code, made = self.call(self.api, "POST", f"/api/waiting/{short}/assign",
                               {"action": "backup", "new": {"name": "Neu"}}, self.user("owner"))
        self.assertEqual(code, 200)
        self.assertEqual(self.engine.services.changed, 1)
        _, job = self.call(self.boot, "GET", "/live/job", headers={"X-Token": token})
        self.assertEqual((job["action"], job["pc"]), ("backup", made["id"]))
        self.assertEqual(self.engine.store.find(made["id"])["macs"], ["aa:bb:cc:dd:ee:02"])
        self.assertEqual(self.woken, [])

    def test_dnsmasq_only_serves_files(self):
        text = server.dnsmasq_config("br0", "/t")
        self.assertIn("port=0", text)
        self.assertIn("enable-tftp", text)
        self.assertNotIn("dhcp-range", text)


def discover(arch, user_class=b"", kind=1, server=None):
    """A PXE request as a Dell Latitude 7490 sends it (option 97 and 94 included)."""
    def o(code, value):
        return bytes([code, len(value)]) + value
    opts = o(53, bytes([kind])) + o(97, bytes(17)) + o(94, b"\x01\x03\x10") + o(93, struct.pack("!H", arch))
    opts += o(60, f"PXEClient:Arch:{arch:05d}:UNDI:003016".encode())
    if user_class:
        opts += o(77, user_class)
    if server:
        opts += o(54, socket.inet_aton(server))
    head = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, 874012070, 4, 0x8000, bytes(4), bytes(4), bytes(4),
                       bytes(4), bytes.fromhex("e4b97a6e2393") + bytes(10), bytes(64), bytes(128))
    return head + b"\x63\x82\x53\x63" + opts + b"\xff"


class ProxyDhcp(unittest.TestCase):
    def answer(self, packet):
        parsed = proxydhcp.parse(packet)
        self.assertIsNotNone(parsed)
        head, options = parsed
        name = proxydhcp.boot_file(options)
        return name, (proxydhcp.reply(head, options, 2, "192.168.1.10", name) if name else None)

    def test_uefi_gets_the_file_in_the_first_offer(self):
        for arch in (7, 9):
            name, out = self.answer(discover(arch))
            self.assertEqual(name, "shimx64.efi")
            self.assertEqual(out[108:119], b"shimx64.efi")
            self.assertEqual(socket.inet_ntoa(out[20:24]), "192.168.1.10")
            _, options = proxydhcp.parse(b"\x01" + out[1:])
            self.assertEqual(options[53], b"\x02")
            self.assertEqual(options[60], b"PXEClient")
            self.assertEqual(options[43], bytes([6, 1, 8, 255]))
            self.assertEqual(options[67], b"shimx64.efi")
            self.assertEqual(options[97], bytes(17))
            self.assertEqual(out[4:8], struct.pack("!I", 874012070))

    def test_bios_gets_ipxe_then_its_script(self):
        self.assertEqual(self.answer(discover(0))[0], "ipxe.0")
        self.assertEqual(self.answer(discover(0, b"iPXE"))[0], "start.0")

    def test_other_clients_are_left_alone(self):
        self.assertEqual(self.answer(discover(11))[0], None)
        plain = discover(7).replace(b"PXEClient:Arch:00007:UNDI:003016", b"MSFT 5.0:Arch:00007:UNDI:003016")
        self.assertEqual(self.answer(plain)[0], None)
        self.assertIsNone(proxydhcp.parse(b"\x02" + bytes(300)))

    def test_answers_over_udp_and_ignores_requests_for_the_router(self):
        sent = []

        class Sock:
            def sendto(self, data, addr):
                sent.append((data, addr))

        proxy = proxydhcp.ProxyDHCP("lo", "192.168.1.10", log=lambda text: None)
        proxy.handle(Sock(), 67, discover(7), ("0.0.0.0", 68))
        self.assertEqual(sent[-1][1], ("255.255.255.255", 68))
        proxy.handle(Sock(), 67, discover(7, kind=3, server="192.168.1.1"), ("0.0.0.0", 68))
        self.assertEqual(len(sent), 1)
        proxy.handle(Sock(), 4011, discover(7, kind=3, server="192.168.1.10"), ("192.168.1.93", 4011))
        self.assertEqual(sent[-1][1], ("192.168.1.93", 4011))
        self.assertEqual(proxydhcp.parse(b"\x01" + sent[-1][0][1:])[1][53], b"\x05")

if __name__ == "__main__":
    unittest.main()
