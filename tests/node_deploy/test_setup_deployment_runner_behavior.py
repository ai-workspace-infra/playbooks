import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/actions/setup-deployment-runner/scripts/setup.sh"


class SetupDeploymentRunnerBehaviorTests(unittest.TestCase):
    def run_setup(self, *, operation: str, policy: str = "disable-unattended-upgrades", ssh_mode: str = "ready"):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "ssh.log"
            cmdb = root / "cmdb.json"
            cmdb.write_text('{"node-1":{"ip":"192.0.2.10","ansible_user":"root","ansible_port":22}}')
            (fake_bin / "timeout").write_text(textwrap.dedent("""\
                #!/usr/bin/env bash
                set -euo pipefail
                shift 3
                exec "$@"
            """))
            (fake_bin / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
            (fake_bin / "ssh").write_text(textwrap.dedent("""\
                #!/usr/bin/env bash
                set -euo pipefail
                payload="$(cat || true)"
                printf 'ARGS:%s\\nPAYLOAD:%s\\n' "$*" "${payload}" >> "${FAKE_SSH_LOG}"
                if [[ "${FAKE_SSH_MODE}" == auth ]]; then
                  echo 'Permission denied (publickey).' >&2
                  exit 255
                fi
                echo READY
            """))
            for executable in fake_bin.iterdir():
                executable.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "ACTION_MATRIX_HOST": "node-1",
                "ACTION_CMDB_FILE": str(cmdb),
                "ACTION_SSH_KEY_B64": "",
                "ACTION_WAIT_FOR_SSH": "true" if operation == "ssh" else "false",
                "ACTION_WAIT_FOR_PACKAGE_INIT": "true" if operation == "package" else "false",
                "ACTION_PACKAGE_INIT_POLICY": policy,
                "ACTION_INSTALL_ANSIBLE": "false",
                "ACTION_ASSERT_ANSIBLE_TARGET": "false",
                "ACTION_ANSIBLE_INVENTORY": "unused.ini",
                "HOST_SSH_WAIT_TIMEOUT": "2",
                "HOST_SSH_AUTH_FAILURE_GRACE": "0",
                "FAKE_SSH_MODE": ssh_mode,
                "FAKE_SSH_LOG": str(log),
            }
            result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)
            return result, log.read_text() if log.exists() else ""

    def test_auth_failure_is_bounded_and_reports_the_rejected_key(self):
        result, calls = self.run_setup(operation="ssh", ssh_mode="auth")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kept rejecting the deploy key", result.stderr)
        self.assertIn("ConnectTimeout=5", calls)

    def test_preserve_policy_waits_without_disabling_unattended_upgrades(self):
        result, calls = self.run_setup(operation="package", policy="preserve")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Preserving unattended-upgrades policy", result.stdout)
        self.assertNotIn("systemctl disable unattended-upgrades.service", calls)

    def test_legacy_policy_disables_unattended_upgrades_before_probe(self):
        result, calls = self.run_setup(operation="package")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("systemctl disable unattended-upgrades.service", calls)
        self.assertIn("lock-frontend", calls)

    def test_unknown_package_policy_fails_before_ssh(self):
        result, calls = self.run_setup(operation="package", policy="surprise")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("package_init_policy must be", result.stderr)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
