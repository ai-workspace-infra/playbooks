from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "scripts/node_deploy/non_iac_agent_proxy_inventory.py"
RESTORE_PLAN = ROOT / "scripts/node_deploy/caddy_restore_plan.py"


def topology(environment: str = "uat", source: str | None = "vault") -> dict:
    suffix = ".onwalk.net" if environment == "uat" else ".svc.plus"
    pools = []
    for name in ("jp", "us", "sg", "tw", "ph"):
        node = {"id": f"{name}-node", "ansible_user": "fallback"}
        if source is not None or name == "ph":
            node["connection_source"] = source
        pools.append(
            {
                "name": name,
                "region": f"{name}-region",
                "entrypoint": {"fqdn": f"{name}-xconnect{suffix}"},
                "nodes": [node],
            }
        )
    return {"metadata": {"environment": environment}, "spec": {"pools": pools}}


class InventoryOwnerTest(unittest.TestCase):
    def test_incomplete_domain_record_cannot_fall_back_to_another_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            response = {"data": {"data": {
                "ph-xconnect.onwalk.net": {"metadata": "selected-but-incomplete"},
                "ph-node": {"ip": "192.0.2.77", "user": "root", "password": "runtime-only"},
            }}}
            process, inventory, output, _ = self.run_adapter(Path(temporary), response)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(inventory.exists())
            self.assertFalse(output.exists())
            self.assertNotIn("runtime-only", process.stdout + process.stderr)

    def run_adapter(self, directory: Path, response: dict, *, environment: str = "uat", node="ph-node"):
        topology_file = directory / "topology.yml"
        response_file = directory / "vault.json"
        inventory_file = directory / "inventory.yml"
        output_file = directory / "output"
        key_file = directory / "deploy-key"
        topology_file.write_text(yaml.safe_dump(topology(environment)), encoding="utf-8")
        response_file.write_text(json.dumps(response), encoding="utf-8")
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_file)],
            check=True,
        )
        process = subprocess.run(
            [
                sys.executable,
                str(INVENTORY),
                "--environment",
                environment,
                "--topology",
                str(topology_file),
                "--vault-response",
                str(response_file),
                "--node-id",
                node,
                "--inventory",
                str(inventory_file),
                "--deploy-key",
                str(key_file),
                "--github-output",
                str(output_file),
            ],
            text=True,
            capture_output=True,
        )
        return process, inventory_file, output_file, key_file

    def test_renders_private_inventory_from_selected_topology_and_authorized_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            response = {
                "data": {
                    "data": {
                        "ph-xconnect.onwalk.net": {
                            "public_ipv4": "192.0.2.44",
                            "ansible_user": "operator",
                            "SSH_PASSWORD": "runtime-only",
                        }
                    }
                }
            }
            process, inventory_file, output_file, key_file = self.run_adapter(directory, response)
            self.assertEqual(process.returncode, 0, process.stderr)
            rendered = yaml.safe_load(inventory_file.read_text(encoding="utf-8"))
            host = rendered["all"]["children"]["agent_proxy"]["hosts"]["ph-node"]
            self.assertEqual(host["ansible_host"], "192.0.2.44")
            self.assertEqual(host["ansible_user"], "operator")
            self.assertEqual(host["ansible_password"], "runtime-only")
            self.assertEqual(host["ansible_ssh_private_key_file"], str(key_file))
            self.assertEqual(stat.S_IMODE(inventory_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
            outputs = output_file.read_text(encoding="utf-8")
            self.assertIn("domain=ph-xconnect.onwalk.net\n", outputs)
            self.assertNotIn("runtime-only", outputs)

    def test_node_key_atomically_replaces_fallback_and_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            node_key = directory / "node-key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(node_key)],
                check=True,
            )
            encoded = base64.b64encode(node_key.read_bytes()).decode()
            response = {"data": {"data": {"ph-node": {
                "ip": "192.0.2.7", "user": "root", "password": "pw",
                "ssh_private_key_b64": encoded,
            }}}}
            process, _, _, fallback = self.run_adapter(directory, response)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(fallback.read_bytes(), node_key.read_bytes())

    def test_invalid_node_key_fails_without_replacing_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            response = {"data": {"data": {"ph-node": {
                "ip": "192.0.2.7", "user": "root", "password": "pw",
                "ssh_private_key_b64": base64.b64encode(b"not-a-key").decode(),
            }}}}
            process, inventory, _, fallback = self.run_adapter(directory, response)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(inventory.exists())
            subprocess.run(["ssh-keygen", "-y", "-f", str(fallback)], check=True, capture_output=True)
            self.assertNotIn("not-a-key", process.stderr)

    def test_rejects_environment_mismatch_before_inventory_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            response = {"data": {"data": {"ph-node": {
                "ip": "192.0.2.7", "user": "root", "password": "pw",
            }}}}
            process, inventory, _, _ = self.run_adapter(directory, response, environment="prod")
            # Helper creates a prod topology, so replace it with UAT and retry the exact command.
            topology_file = directory / "topology.yml"
            topology_file.write_text(yaml.safe_dump(topology("uat")), encoding="utf-8")
            inventory.unlink()
            command = process.args
            process = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(inventory.exists())
            self.assertIn("environment", process.stderr)


class CaddyRestorePlanTest(unittest.TestCase):
    def invoke(self, directory: Path, status: int, record: dict | None):
        response = directory / "vault.json"
        response.write_text(json.dumps({"data": {"data": record or {}}}), encoding="utf-8")
        output = directory / "output"
        variables = directory / "vars.json"
        process = subprocess.run(
            [
                sys.executable,
                str(RESTORE_PLAN),
                "--vault-response",
                str(response),
                "--http-status",
                str(status),
                "--target",
                "ph-node",
                "--directory",
                "/etc/xcontrol/tls/onwalk.net",
                "--vars-file",
                str(variables),
                "--github-output",
                str(output),
            ],
            text=True,
            capture_output=True,
        )
        return process, output, variables

    @staticmethod
    def complete_record(expiry=None):
        value = base64.b64encode(b"private-material").decode()
        record = {
            "tls_fullchain_pem_b64": value,
            "tls_cert_pem_b64": value,
            "tls_key_pem_b64": value,
            "tls_ca_pem_b64": value,
            "tls_trust_bundle_pem_b64": value,
        }
        if expiry is not None:
            record["not_after_epoch"] = expiry
        return record

    def test_404_and_incomplete_records_are_non_mutating_plans(self):
        for status, record, reason in ((404, None, "no-backup"), (200, {}, "incomplete-backup")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                process, output, variables = self.invoke(Path(temporary), status, record)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertIn("restore_required=false", output.read_text())
                self.assertIn(f"reason={reason}", output.read_text())
                self.assertFalse(variables.exists())

    def test_ready_record_writes_private_role_contract_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            process, output, variables = self.invoke(
                Path(temporary), 200, self.complete_record(int(time.time()) + 90 * 86400)
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(stat.S_IMODE(variables.stat().st_mode), 0o600)
            contract = json.loads(variables.read_text())
            self.assertEqual(contract["caddy_certificate_restore_target"], "ph-node")
            self.assertEqual(contract["caddy_certificate_restore_material"]["key"], "private-material")
            self.assertNotIn("private-material", output.read_text())
            self.assertNotIn("private-material", process.stdout)

    def test_expiring_record_skips_and_non_success_status_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            process, output, variables = self.invoke(
                Path(temporary), 200, self.complete_record(int(time.time()) + 60)
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("reason=renewal-margin", output.read_text())
            self.assertFalse(variables.exists())
        with tempfile.TemporaryDirectory() as temporary:
            process, _, variables = self.invoke(Path(temporary), 500, {})
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse(variables.exists())


class CompositeBoundaryTest(unittest.TestCase):
    def test_actions_do_not_authenticate_to_vault_or_embed_host_restore(self):
        inventory_action = (ROOT / ".github/actions/non-iac-agent-proxy-inventory/action.yml").read_text()
        restore_action = (ROOT / ".github/actions/caddy-certificate-restore/action.yml").read_text()
        combined = inventory_action + restore_action
        self.assertNotIn("VAULT_TOKEN", combined)
        self.assertNotIn("vault-action", combined)
        self.assertNotIn("curl ", combined)
        self.assertIn("caddy_certificate_restore.yml", restore_action)
        self.assertIn("--limit", restore_action)
        self.assertIn("steps.plan.outputs.vars_file", restore_action)
        self.assertIn("rm -f", restore_action)


if __name__ == "__main__":
    unittest.main()
