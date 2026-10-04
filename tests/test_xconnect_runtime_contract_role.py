"""Offline contract tests for the read-only XConnect runtime role operation."""

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
TASKS = ROLE / "tasks/xconnect_runtime_contract.yml"
HOST = "tw-xconnect.svc.plus"


class XConnectRuntimeContractTests(unittest.TestCase):
    def test_operation_is_explicit_and_read_only(self):
        tasks = yaml.safe_load(TASKS.read_text())
        main = (ROLE / "tasks/main.yml").read_text()
        rendered = TASKS.read_text()
        self.assertIn("xconnect_runtime_contract", main)
        self.assertIn("xconnect_runtime_contract", rendered)
        self.assertNotRegex(rendered, r"\b(?:xconnect|xconnect-gateway)\s+(?:sync|up|apply)\b")
        self.assertNotRegex(rendered, r"\b(?:systemctl|service)\b.*\brestart\b")
        self.assertNotIn("ansible.builtin.systemd", rendered)
        self.assertNotIn("ansible.builtin.service", rendered)
        command_tasks = [task for task in tasks if "ansible.builtin.shell" in task]
        self.assertEqual(len(command_tasks), 1)
        self.assertIs(command_tasks[0]["changed_when"], False)
        self.assertIs(command_tasks[0]["no_log"], True)

    def fixture(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
        gateway_config = directory / "gateway-xray.json"
        one_state = directory / "one"
        one_config = directory / "one-xray.json"
        (one_state / "runtime").mkdir(parents=True)

        gateway_config.write_text(json.dumps({
            "inbounds": [{
                "listen": "0.0.0.0", "port": 443, "protocol": "vless",
                "streamSettings": {
                    "network": "xhttp", "security": "tls",
                    "tlsSettings": {"rejectUnknownSni": True},
                    "xhttpSettings": {"path": "/xconnect", "mode": "auto", "host": HOST},
                },
            }],
            "outbounds": [{
                "tag": "xconnect-wireguard", "protocol": "freedom",
                "settings": {"redirect": "127.0.0.1:51820"},
            }],
        }))
        one_config.write_text(json.dumps({
            "inbounds": [{
                "listen": "127.0.0.1", "port": 51830,
                "protocol": "dokodemo-door", "settings": {"network": "udp"},
            }],
            "outbounds": [{
                "protocol": "vless", "settings": {"vnext": [{"address": HOST, "port": 443}]},
                "streamSettings": {
                    "network": "xhttp", "security": "tls",
                    "tlsSettings": {"serverName": HOST},
                    "xhttpSettings": {"path": "/xconnect", "mode": "auto", "host": HOST},
                },
            }],
        }))
        (one_state / "runtime/active.json").write_text(json.dumps({"xray_config_path": str(one_config)}))
        inventory = directory / "inventory.ini"
        inventory.write_text("[all]\nnode ansible_connection=local\n")
        common = {
            "observability_operations_environment": "uat",
            "observability_operation": "xconnect_runtime_contract",
            "xconnect_runtime_contract_delegate_host": "node",
            "xconnect_runtime_contract_remote_address": HOST,
            "xconnect_runtime_contract_server_name": HOST,
            "xconnect_runtime_contract_xhttp_path": "/xconnect",
            "xconnect_runtime_contract_xhttp_mode": "auto",
            "xconnect_runtime_contract_xhttp_host": HOST,
            "xconnect_runtime_contract_become": False,
            "xconnect_runtime_contract_path_env": "/usr/local/bin:/usr/bin:/bin",
        }
        gateway = dict(common, xconnect_runtime_contract_role="gateway", xconnect_runtime_contract_path=str(gateway_config))
        one = dict(common, xconnect_runtime_contract_role="one", xconnect_runtime_contract_path=str(one_state))
        return inventory, gateway, one

    def run_fixture(self, inventory: Path, variables: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ansible-playbook", "-i", str(inventory), "observability_operations.yml", "-e", json.dumps(variables)],
            cwd=ROOT,
            env=dict(os.environ, ANSIBLE_NOCOLOR="1"),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_gateway_and_one_contracts_are_valid(self):
        inventory, gateway, one = self.fixture()
        for variables in (gateway, one):
            result = self.run_fixture(inventory, variables)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("XCONNECT_RUNTIME_CONTRACT", output)
            self.assertIn("result=valid", output)
            self.assertIn("changed=0", output)

    def test_contract_mismatch_fails_closed(self):
        inventory, gateway, _ = self.fixture()
        gateway["xconnect_runtime_contract_xhttp_host"] = "wrong.svc.plus"
        result = self.run_fixture(inventory, gateway)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("result=valid", result.stdout + result.stderr)

    def test_non_uat_is_rejected_before_remote_read(self):
        inventory, gateway, _ = self.fixture()
        gateway["observability_operations_environment"] = "prod"
        result = self.run_fixture(inventory, gateway)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("XCONNECT_RUNTIME_CONTRACT", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
