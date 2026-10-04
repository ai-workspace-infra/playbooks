"""Caddy installs stay verifiable while the Cloudsmith apt key is expired."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CADDY_DEFAULTS = ROOT / "roles" / "vhosts" / "caddy" / "defaults" / "main.yml"
CADDY_TASKS = ROOT / "roles" / "vhosts" / "caddy" / "tasks" / "main.yml"
APT_KEY_TASKS = ROOT / "roles" / "vhosts" / "common" / "tasks" / "caddy_apt_source.yml"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class CaddyExpiredAptKeyTest(unittest.TestCase):
    def test_caddy_release_deb_is_version_and_sha512_pinned(self):
        defaults = load(CADDY_DEFAULTS)
        self.assertRegex(defaults["caddy_package_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(set(defaults["caddy_package_arch_map"].values()),
                         set(defaults["caddy_package_sha512"]))
        for checksum in defaults["caddy_package_sha512"].values():
            self.assertRegex(checksum, r"^[0-9a-f]{128}$")

    def test_install_uses_pinned_github_release_and_never_adds_caddy_apt_repo(self):
        tasks_text = CADDY_TASKS.read_text(encoding="utf-8")
        tasks = load(CADDY_TASKS)
        block = next(task["block"] for task in tasks
                     if task.get("name") == "Configure Caddy reverse proxy")
        by_name = {task["name"]: task for task in block}

        source_cleanup = by_name["Remove the stale Caddy stable apt source before refreshing apt"]
        prereq = by_name["Ensure Caddy package prerequisites"]
        download = by_name["Download checksum-pinned official Caddy release package"]
        install = by_name["Install the verified Caddy release package"]

        self.assertLess(block.index(source_cleanup), block.index(prereq))
        self.assertIn("/etc/apt/sources.list.d/caddy-stable.list", source_cleanup["loop"])
        self.assertEqual(prereq["ansible.builtin.apt"]["update_cache"], True)
        self.assertIn("github.com/caddyserver/caddy/releases/download/v", download["ansible.builtin.get_url"]["url"])
        self.assertIn("checksum", download["ansible.builtin.get_url"])
        self.assertEqual(install["ansible.builtin.apt"]["state"], "present")
        self.assertIn("deb", install["ansible.builtin.apt"])
        self.assertNotIn("apt_repository", tasks_text)
        self.assertNotIn("trusted=yes", tasks_text)

    def test_expired_subkey_is_detected_and_only_caddy_source_is_removed(self):
        tasks = {task["name"]: task for task in load(APT_KEY_TASKS)}
        decision = tasks["Caddy apt source | decide whether the signing key has expired"]
        expression = decision["ansible.builtin.set_fact"]["caddy_apt_key_expired"]
        self.assertIn("^(pub|sub):", expression)
        self.assertIn("ansible_facts['date_time']['epoch']", expression)

        removal = tasks["Caddy apt source | remove the source while its signing key is expired"]
        self.assertEqual(removal["when"], "caddy_apt_key_expired | bool")
        self.assertEqual(removal["loop"], [
            "/etc/apt/sources.list.d/caddy-stable.list",
            "/etc/apt/sources.list.d/caddy-stable.sources",
            "/etc/apt/keyrings/caddy-stable.gpg",
            "/etc/apt/keyrings/caddy-stable.asc",
        ])
        self.assertNotIn("trusted=yes", APT_KEY_TASKS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
