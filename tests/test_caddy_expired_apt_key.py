"""An expired Caddy apt signing key must not break apt on the host.

Daily UAT (Selfhost run 36816438929) failed on jp-xconnect with
"EXPKEYSIG 531A6B20FA058A70 Caddy Web Server ... is not signed": the upstream
key expired, so every `apt update` on a host with the source fails. The
source is dropped while its key is expired and never trusted unsigned.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "roles" / "vhosts" / "common" / "tasks" / "caddy_apt_source.yml"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class CaddyExpiredAptKeyTest(unittest.TestCase):
    def test_check_removes_only_the_caddy_source_when_the_key_expired(self):
        tasks = {task["name"]: task for task in load(CHECK)}
        listing = tasks["Caddy apt source | read the signing key"]
        self.assertEqual(listing["ansible.builtin.command"]["argv"],
                         ["gpg", "--show-keys", "--with-colons", "/etc/apt/keyrings/caddy-stable.gpg"])
        removal = tasks["Caddy apt source | remove the source while its signing key is expired"]
        self.assertEqual(removal["when"], "caddy_apt_key_expired | bool")
        self.assertEqual(removal["loop"], [
            "/etc/apt/sources.list.d/caddy-stable.list",
            "/etc/apt/keyrings/caddy-stable.gpg",
            "/etc/apt/keyrings/caddy-stable.asc",
        ])
        decision = tasks["Caddy apt source | decide whether the signing key has expired"]
        expression = decision["ansible.builtin.set_fact"]["caddy_apt_key_expired"]
        self.assertIn("'equalto', 'e'", expression)
        self.assertIn("date_time']['epoch']", expression)
        self.assertNotIn("trusted=yes", CHECK.read_text(encoding="utf-8"))

    def test_common_runs_the_check_before_any_apt_refresh(self):
        tasks = load(ROOT / "roles" / "vhosts" / "common" / "tasks" / "main.yml")
        names = [task["name"] for task in tasks]
        check = names.index("Base | drop the Caddy apt source while its signing key is expired")
        self.assertEqual(tasks[check]["ansible.builtin.import_tasks"], "caddy_apt_source.yml")
        self.assertLess(check, names.index("Base | configure fail2ban"))

    def test_caddy_role_refuses_a_fresh_install_from_an_expired_source(self):
        tasks = load(ROOT / "roles" / "vhosts" / "caddy" / "tasks" / "main.yml")
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Drop the Caddy apt source while its signing key is expired"),
                        names.index("Configure Caddy reverse proxy"))
        block = tasks[names.index("Configure Caddy reverse proxy")]["block"]
        inner = [task["name"] for task in block]
        order = [inner.index(name) for name in (
            "Dearmor Caddy GPG key", "Check the freshly downloaded Caddy signing key",
            "Refuse to install Caddy from a source with an expired signing key", "Add Caddy repository (Debian)")]
        self.assertEqual(order, sorted(order))
        refuse = block[inner.index("Refuse to install Caddy from a source with an expired signing key")]
        self.assertIn("caddy_apt_key_expired | default(false) | bool", refuse["when"])


if __name__ == "__main__":
    unittest.main()
