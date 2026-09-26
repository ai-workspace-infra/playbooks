import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parents[1] / "roles/vhosts/xconnect_one"


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def executable(path: Path, body: str):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ReleaseForeignOverlayTests(unittest.TestCase):
    def render(self, root: Path, port: int) -> Path:
        template = jinja2.Template((ROLE / "templates/release_foreign.py.j2").read_text(encoding="utf-8"))
        script = root / "release.py"
        script.write_text(template.render(
            xconnect_one_state_dir="/var/lib/xconnect-one/shared",
            xconnect_one_expected_network_id="net_shared_vault",
            xconnect_one_expected_wireguard_interface="xconone0",
            xconnect_one_expected_xray_loopback_port=port,
        ), encoding="utf-8")
        return script

    def host(self, root: Path, interface_up_after_down: bool = False):
        bin_dir, log = root / "bin", root / "calls.log"
        bin_dir.mkdir()
        executable(bin_dir / "systemctl", f'echo "systemctl $*" >> {log}\n')
        executable(bin_dir / "ip", f'[ -f {root}/xconone0.up ] && exit 0 || exit 1\n')
        down = "" if interface_up_after_down else f"rm -f {root}/xconone0.up\n"
        executable(bin_dir / "xconnect", f'echo "xconnect $*" >> {log}\n{down}')
        (root / "xconone0.up").write_text("")
        for sub, network in (("", "net_uat"), ("/shared", "net_shared_vault")):
            state = root / f"var/lib/xconnect-one{sub}"
            state.mkdir(parents=True, exist_ok=True)
            (state / "state.json").write_text(json.dumps({"network_id": network, "device_id": "vault-legacy"}))
        units = root / "etc/systemd/system"
        units.mkdir(parents=True)
        (units / "xconnect-one-sync.service").write_text(
            f"[Service]\nExecStart={bin_dir}/xconnect sync --state-dir /var/lib/xconnect-one\n")
        (units / "xconnect-one-sync@shared.service").write_text(
            f"[Service]\nExecStart={bin_dir}/xconnect sync --state-dir /var/lib/xconnect-one/shared\n")
        return bin_dir, log

    def run_script(self, root: Path, bin_dir: Path, port: int):
        env = dict(os.environ, XCONNECT_ONE_RELEASE_ROOT=str(root), PATH=f"{bin_dir}:{os.environ['PATH']}")
        return subprocess.run([sys.executable, str(self.render(root, port))], env=env, capture_output=True, text=True)

    def test_leaves_the_other_network_and_keeps_its_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root, port = Path(directory), free_port()
            bin_dir, log = self.host(root)
            result = self.run_script(root, bin_dir, port)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("xconnect released: net_uat", result.stdout)
            calls = log.read_text()
            self.assertIn("systemctl disable --now xconnect-one-sync.timer", calls)
            self.assertIn("xconnect down --state-dir /var/lib/xconnect-one\n", calls)
            self.assertNotIn("shared", calls)
            self.assertTrue((root / "var/lib/xconnect-one/state.json").exists())

    def test_ignores_backup_and_pre_reenroll_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root, port = Path(directory), free_port()
            bin_dir, log = self.host(root)
            backup = root / "var/lib/xconnect-one/uat.pre-reenroll.20260922"
            backup.mkdir(parents=True)
            (backup / "state.json").write_text(json.dumps({"network_id": "net_uat", "device_id": "vault-legacy"}))
            result = self.run_script(root, bin_dir, port)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text()
            self.assertNotIn("pre-reenroll", calls)

    def test_fails_when_the_interface_is_still_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root, port = Path(directory), free_port()
            bin_dir, _ = self.host(root, interface_up_after_down=True)
            result = self.run_script(root, bin_dir, port)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("xconone0 is still up after leaving net_uat", result.stdout + result.stderr)

    def test_release_is_opt_in_and_runs_before_the_preflight(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
        names = [task.get("name") for task in tasks]
        release = tasks[names.index("Take this host off other XConnect networks")]
        self.assertEqual(release["when"], "xconnect_one_release_foreign_overlays | bool")
        self.assertLess(names.index("Take this host off other XConnect networks"),
                        names.index("Refuse overlapping XConnect One host networking"))
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
        self.assertIs(defaults["xconnect_one_release_foreign_overlays"], False)


if __name__ == "__main__":
    unittest.main()


class JoinDiagnosticTests(unittest.TestCase):
    def test_join_failure_is_reported_without_leaking_the_invitation(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
        block = next(task for task in tasks if task.get("name") == "Join XConnect One with protected invite file")
        names = [task["name"] for task in block["block"]]
        join = block["block"][names.index("Join the signed XConnect Zero network")]
        report = block["block"][names.index("Report why the XConnect One join failed")]
        self.assertIs(join["no_log"], True)
        self.assertIs(join["failed_when"], False)
        self.assertEqual(names.index("Report why the XConnect One join failed"),
                         names.index("Join the signed XConnect Zero network") + 1)
        message = report["ansible.builtin.fail"]["msg"]
        self.assertIn("'[A-Za-z0-9+/_=-]{32,}', '<redacted>'", message)
        self.assertIn("'xconnect://[^ ]+', 'xconnect://<redacted>'", message)
        self.assertEqual(report["when"], "(xconnect_one_join.rc | default(1)) != 0")
