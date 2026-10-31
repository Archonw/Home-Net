import os
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from piwartung import core  # noqa: E402
from piwartung.core import Inventory, Pi, Settings, Unraid  # noqa: E402


class Conf(unittest.TestCase):
    def test_conf_survives_shell_sourcing(self):
        pi = Pi(name="Küche", host="1.2.3.4", pre_cmd="sudo systemctl stop 'a b'; echo $HOME",
                excludes="/data/*\n/x y", jump="me@jump")
        out = subprocess.run(["bash", "-c", 'source /dev/stdin; printf "%s|%s|%s" "$NAME" "$PRE_CMD" "$EXCLUDES"'],
                             input=pi.conf_text(), capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, "Küche|sudo systemctl stop 'a b'; echo $HOME|/data/*\n/x y")


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


class Paths(unittest.TestCase):
    def test_pidir_and_local(self):
        s = Settings(root="/mnt/user/b/PW")
        u = Unraid(s)
        self.assertEqual(u.pidir(Pi(name="n", category="A/B")), "/mnt/user/b/PW/A/B/n")
        self.assertEqual(u.pidir(Pi(name="n")), "/mnt/user/b/PW/n")
        self.assertEqual(s.to_local("/mnt/user/b/x"), "/mnt/unraid/b/x")
        self.assertEqual(s.to_local("/mnt/userx/b"), "/mnt/userx/b")


class SshConfig(unittest.TestCase):
    def test_parse(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg") as f:
            f.write("Host a b\n  Hostname 1.1.1.1\n  Port 2222\n  User u\n  IdentityFile ~/.ssh/k\n"
                    "Host *\n  User ignored\nHost c\n  Hostname c.example\n")
            f.flush()
            hosts = core.ssh_config_hosts(f.name)
        self.assertEqual(hosts["b"]["port"], 2222)
        self.assertEqual(hosts["a"]["identity"], "~/.ssh/k")
        self.assertEqual(hosts["c"]["user"], "pi")
        self.assertNotIn("*", hosts)


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


if __name__ == "__main__":
    unittest.main()
