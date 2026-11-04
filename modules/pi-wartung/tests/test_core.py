import os
import shlex
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from piwartung import core  # noqa: E402
from piwartung.core import Inventory, Pi  # noqa: E402


class Conf(unittest.TestCase):
    def test_conf_survives_shell_sourcing(self):
        pi = Pi(name="Küche", host="1.2.3.4", pre_cmd="sudo systemctl stop 'a b'; echo $HOME",
                excludes="/data/*\n/x y", jump="me@jump")
        out = subprocess.run(["bash", "-c", 'source /dev/stdin; printf "%s|%s|%s" "$NAME" "$PRE_CMD" "$EXCLUDES"'],
                             input=pi.conf_text(), capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, "Küche|sudo systemctl stop 'a b'; echo $HOME|/data/*\n/x y")
        self.assertIn("NOTIFY=errors", pi.conf_text())


class Names(unittest.TestCase):
    def test_segments(self):
        for good in ("Wohnzimmer", "Firma Müller", "Pi-1_b"):
            self.assertTrue(core.valid_segment(good), good)
        for bad in ("", ".", "..", ".versteckt", "a/b", "a$b", "a:b", "a`b"):
            self.assertFalse(core.valid_segment(bad), bad)

    def test_categories(self):
        self.assertTrue(core.valid_category(""))
        self.assertTrue(core.valid_category("Firma/Standort A"))
        self.assertFalse(core.valid_category("Firma/../etc"))

    def test_implied_parents(self):
        inv = Inventory(categories=["Familie"], pis=[Pi(name="x", category="Firma/Nord")])
        self.assertEqual(inv.all_categories(), ["Familie", "Firma", "Firma/Nord"])

    def test_name_taken_only_within_category(self):
        a = Pi(name="x", category="A")
        inv = Inventory(pis=[a])
        self.assertTrue(inv.name_taken("x", "A"))
        self.assertFalse(inv.name_taken("x", "B"))
        self.assertFalse(inv.name_taken("x", "A", ignore_id=a.id))


class Parse(unittest.TestCase):
    def test_shell_kv_with_printf_q(self):
        text = "LAST_ERROR=Zu\\ wenig\\ Platz\nLAST_IMAGE=''\nRUNNING=0\n"
        got = core.parse_shell_kv(text)
        self.assertEqual(got["LAST_ERROR"], "Zu wenig Platz")
        self.assertEqual(got["LAST_IMAGE"], "")
        self.assertEqual(got["RUNNING"], "0")

    def test_human_size(self):
        self.assertEqual(core.human_size(1536 * 1024 * 1024), "1,5 GiB")


class Ssh(unittest.TestCase):
    def test_jump_command(self):
        pi = Pi(host="h", port=2, user="u", jump="j@x", jump_port=3)
        self.assertEqual(pi.ssh_command(), ["ssh", "-p", "2", "-J", "j@x:3", "u@h"])
        self.assertEqual(shlex.join(Pi(host="h").ssh_command()), "ssh -p 22 pi@h")


class Dashboard(unittest.TestCase):
    TEXT = ("DISK\t100\t1000\n"
            "PI\t/r/A/x\t0\tfailed\t50\t10\t2\t300\t40\toff\t10\t\n"
            "RUN\t/r/A/x\t2026-01-01_120000\tfailed\tKaputt\n"
            "RUN\t/r/A/x\t2026-01-02_120000\tok\t\n")

    def test_parse(self):
        s = core.parse_summary(self.TEXT)
        self.assertEqual((s["avail"], s["total"]), (100, 1000))
        self.assertEqual(s["pis"]["/r/A/x"]["bytes"], 300)
        self.assertEqual(s["runs"][0]["error"], "Kaputt")
        self.assertEqual(s["runs"][1]["error"], "")

    def test_parse_update_fields(self):
        s = core.parse_summary("PI\t/r/A/x\t0\tok\t50\t10\t2\t300\t40\toff\t10\t\t58\t60\tupgrading\tfailed\n")
        row = s["pis"]["/r/A/x"]
        self.assertEqual((row["upd_count"], row["upd_time"], row["upd_state"], row["upd_last"]), (58, 60, "upgrading", "failed"))
        self.assertNotIn("upd_count", core.parse_summary(self.TEXT)["pis"]["/r/A/x"])

    def test_old_pi_is_not_in_update_all_and_conf_names_setting(self):
        self.assertEqual(Pi(name="x").updates, "single")
        self.assertIn("UPDATES=careful", Pi(name="x", updates="careful").conf_text())

    def test_attention(self):
        pi = Pi(name="x")
        s = core.parse_summary(self.TEXT)
        now = 10 + 20 * 86400
        items = core.attention_items([pi], {pi.id: "/r/A/x"}, s, {pi.id: {"disk": "9G 10G 90%", "temp": "80 °C", "reboot": "ja"}}, now)
        texts = [i[2] for i in items]
        self.assertEqual(items[0][2], "Letzte Sicherung ist fehlgeschlagen")
        for part in ("20 Tagen", "90 %", "80 °C", "Neustart", "Kein Zeitplan"):
            self.assertTrue(any(part in t for t in texts), part)

    def test_unreachable_and_full_share(self):
        pi = Pi(name="x")
        s = core.parse_summary("DISK\t5\t100\n")
        items = core.attention_items([pi], {pi.id: "/d"}, s, {pi.id: {}}, 0)
        names = [(i[1], i[2]) for i in items]
        self.assertIn(("x", "Nicht erreichbar"), names)
        self.assertIn(("Unraid", "Weniger als 10 % Speicher frei"), names)
        self.assertIn(("x", "Noch kein Image vorhanden"), names)


if __name__ == "__main__":
    unittest.main()
