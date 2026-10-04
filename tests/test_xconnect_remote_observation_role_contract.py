"""Contract and offline fixture tests for the read-only XConnect observer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/docker/observability_server_operations"
TASKS = ROLE / "tasks/xconnect_remote_observation.yml"
RUN_ID = "xcl-123-1"
NETWORK = "net_uat-xcl-123-1"
GATEWAY = "gw-xcl-123-1"
CLIENT = "one-xcl-123-1"
KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 32-byte fixture key


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n")
    path.chmod(0o755)


class XConnectRemoteObservationTests(unittest.TestCase):
    def test_operation_is_explicit_and_read_only(self):
        tasks = yaml.safe_load(TASKS.read_text())
        main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        rendered = TASKS.read_text()
        self.assertIn("xconnect_remote_observation", str(main))
        self.assertNotRegex(rendered, r"\b(?:xconnect|xconnect-gateway)\s+(?:sync|up|apply)\b")
        self.assertNotRegex(rendered, r"\b(?:systemctl|service)\b.*\brestart\b")
        self.assertNotIn("ansible.builtin.systemd", rendered)
        self.assertNotIn("ansible.builtin.service", rendered)
        command_tasks = [
            task for task in tasks
            if "ansible.builtin.command" in task
            or "ansible.builtin.shell" in task
        ]
        self.assertGreaterEqual(len(command_tasks), 4)
        for task in command_tasks:
            self.assertIs(task["changed_when"], False)
            self.assertIs(task["failed_when"], False)
            self.assertIs(task["no_log"], True)

    def fixture(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
        gateway_state = directory / "gateway"
        client_state = directory / "client"
        (gateway_state / "runtime").mkdir(parents=True)
        client_state.mkdir()
        (gateway_state / "state.json").write_text(json.dumps({
            "gateway_id": GATEWAY,
            "network_id": NETWORK,
            "applied_generation": 2,
            "applied_config_id": "cfg-2",
        }))
        (gateway_state / "runtime/xconzero0.conf").write_text(
            f"[Interface]\n\n# DeviceID = {CLIENT}\nPublicKey = {KEY}\n"
        )
        executable(directory / "xconnect-gateway", '[[ "$1" == status ]]')
        executable(directory / "xconnect", """
if [[ "$1" == status ]]; then
  printf '%s\\n' '{"joined":true,"device_id":"one-xcl-123-1","network_id":"net_uat-xcl-123-1","revision":"cfg-2","generations":{"state":2},"runtime":{"applied":true,"core_id":"xray"},"credential":{"present":true,"expired":false}}'
else
  exit 1
fi
""")
        executable(directory / "wg", f'echo "{KEY} $(date +%s)"')
        inventory = directory / "inventory.ini"
        inventory.write_text(
            "[xconnect_gateway]\n"
            "gateway ansible_connection=local\n"
            "[xconnect_client]\n"
            "client ansible_connection=local\n"
        )
        variables = {
            "observability_operations_environment": "uat",
            "observability_operation": "xconnect_remote_observation",
            "xconnect_remote_observation_gateway_host": "gateway",
            "xconnect_remote_observation_client_host": "client",
            "xconnect_remote_observation_run_id": RUN_ID,
            "xconnect_remote_observation_gateway_id": GATEWAY,
            "xconnect_remote_observation_client_id": CLIENT,
            "xconnect_remote_observation_network_id": NETWORK,
            "xconnect_remote_observation_gateway_public_key": KEY,
            "xconnect_remote_observation_gateway_state_dir": str(gateway_state),
            "xconnect_remote_observation_client_state_dir": str(client_state),
            "xconnect_remote_observation_become": False,
            "xconnect_remote_observation_path": f"{directory}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        return directory, inventory, variables

    def run_fixture(self, inventory: Path, variables: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "ansible-playbook", "-i", str(inventory),
                "observability_operations.yml", "-e", json.dumps(variables),
            ],
            cwd=ROOT,
            env=dict(os.environ, ANSIBLE_NOCOLOR="1"),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_healthy_fixture_emits_sanitized_contract(self):
        _, inventory, variables = self.fixture()
        result = self.run_fixture(inventory, variables)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("NODE_OBSERVATION run=xcl-123-1", output)
        self.assertIn("refresh=OK", output)
        self.assertIn("sync=OK", output)
        self.assertIn("gateway_peer=OBSERVED", output)
        self.assertIn("client_peer=OBSERVED", output)
        self.assertIn("NODE_OBSERVATION_RESULT=SUMMARY_ONLY", output)
        self.assertIn("changed=0", output)
        self.assertNotIn(KEY, output)
        self.assertNotIn("cfg-2", output)

    def test_bad_client_status_fails_closed_without_mutating_the_fixture(self):
        directory, inventory, variables = self.fixture()
        executable(directory / "xconnect", """
if [[ "$1" == status ]]; then
  printf '%s\\n' '{"joined":true,"device_id":"one-xcl-123-1","network_id":"net_uat-xcl-123-1","revision":"cfg-2","generations":{"state":2},"runtime":{"applied":true,"core_id":"not-xray"},"credential":{"present":true,"expired":false}}'
else
  exit 1
fi
""")
        result = self.run_fixture(inventory, variables)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("sync=UNVERIFIED", output)
        self.assertIn("client_peer=UNVERIFIED", output)
        self.assertNotIn(KEY, output)

    def test_non_uat_is_rejected_before_remote_observation(self):
        _, inventory, variables = self.fixture()
        variables["observability_operations_environment"] = "prod"
        result = self.run_fixture(inventory, variables)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("NODE_OBSERVATION run=", output)


if __name__ == "__main__":
    unittest.main()
