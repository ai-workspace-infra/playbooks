"""Offline contract tests only; these are not runtime acceptance evidence."""
import copy
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADAPTER = load("dev_adapter", "scripts/dev_k3s_bootstrap.py")
VERIFY = load("dev_verify", "roles/vhosts/k3s_platform_bootstrap/files/verify_bootstrap.py")
SHA = "a" * 40


class Contract(unittest.TestCase):
    def test_runtime_verifier_observes_explicit_root_name(self):
        argv = ["verify", "--node", ADAPTER.TARGET, "--namespace", "flux-system", "--source", "config",
                "--root-name", "generation-root", "--commit", SHA, "--version", "", "--architecture", ""]
        with mock.patch.object(sys, 'argv', argv), \
             mock.patch.object(VERIFY, 'get', side_effect=self.resources()) as observe, \
             mock.patch.object(VERIFY.socket, 'gethostname', return_value=ADAPTER.TARGET), \
             mock.patch('builtins.print'):
            VERIFY.main()
        self.assertEqual(observe.call_args_list[-1].args,
                         ('-n', 'flux-system', 'get', 'kustomization', 'generation-root'))

    def cmdb(self):
        return {ADAPTER.TARGET: {"name": ADAPTER.TARGET, "label": ADAPTER.TARGET, "instance_id": 123,
            "ip": "198.51.100.10", "groups": ["dev", "k3s"], "host_vars": {
                "cloud_provider": "akamai-cloud", "cloud_region": "jp-tyo-3",
                "k3s_platform_node_name": ADAPTER.TARGET, "k3s_platform_write_kubeconfig_mode": "0600",
                "k3s_platform_k3s_version": "v1.37.1+k3s1"}}}

    def test_single_target_and_no_arbitrary_ansible_overrides(self):
        source = self.cmdb()
        source[ADAPTER.TARGET]["host_vars"]["ansible_connection"] = "local"
        projected = ADAPTER.validate_cmdb(source, "123")
        self.assertNotIn("ansible_connection", projected[ADAPTER.TARGET]["host_vars"])
        for changed in ({}, {**source, "production": {}}, {"production": source[ADAPTER.TARGET]}):
            with self.assertRaises(ValueError):
                ADAPTER.validate_cmdb(changed, "123")
        with self.assertRaises(ValueError):
            ADAPTER.validate_cmdb(source, "456")

    def test_wrong_environment_provider_mode_rejected(self):
        for path, value in [("groups", ["prod"]), ("label", "other")]:
            source = self.cmdb()
            source[ADAPTER.TARGET][path] = value
            with self.assertRaises(ValueError):
                ADAPTER.validate_cmdb(source, "123")
        source = self.cmdb()
        source[ADAPTER.TARGET]["host_vars"]["k3s_platform_write_kubeconfig_mode"] = "0644"
        with self.assertRaises(ValueError):
            ADAPTER.validate_cmdb(source, "123")

    def resources(self):
        meta = {"generation": 2, "uid": "unit-test-uid"}
        status = {"observedGeneration": 2, "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 2}]}
        source = {"metadata": copy.deepcopy(meta), "status": copy.deepcopy(status)}
        source["spec"] = {"ref": {"commit": SHA}}
        source["status"]["artifact"] = {"revision": "main@sha1:" + SHA}
        root = {"metadata": copy.deepcopy(meta), "status": copy.deepcopy(status)}
        root["status"]["lastAppliedRevision"] = "main@sha1:" + SHA
        node = {"metadata": {"name": ADAPTER.TARGET, "uid": "node-unit-test", "labels": {"node-role.kubernetes.io/control-plane": "true"}}, "status": copy.deepcopy(status)}
        return {"items": [node]}, {"metadata": {"uid": "cluster-unit-test"}}, source, root

    def test_observed_revision_and_generation_are_hard_gates(self):
        args = self.resources()
        self.assertEqual(VERIFY.evaluate(*args, ADAPTER.TARGET, SHA)["source_revision"], "main@sha1:" + SHA)
        changes = [(2, "observedGeneration", 1), (3, "lastAppliedRevision", "main@sha1:" + "b"*40)]
        for idx, key, value in changes:
            args = self.resources()
            args[idx]["status"][key] = value
            with self.assertRaises(ValueError):
                VERIFY.evaluate(*args, ADAPTER.TARGET, SHA)
        with self.assertRaises(ValueError):
            VERIFY.evaluate(*self.resources(), ADAPTER.TARGET, "b"*40)
        args = self.resources()
        args[0]["items"].append(copy.deepcopy(args[0]["items"][0]))
        with self.assertRaises(ValueError):
            VERIFY.evaluate(*args, ADAPTER.TARGET, SHA)

    def test_false_ready_missing_uid_and_wrong_node_fail(self):
        for index in (2, 3):
            args = self.resources()
            args[index]["status"]["conditions"][0]["status"] = "False"
            with self.assertRaises(ValueError):
                VERIFY.evaluate(*args, ADAPTER.TARGET, SHA)
        args = self.resources()
        args[1]["metadata"].pop("uid")
        with self.assertRaises(ValueError):
            VERIFY.evaluate(*args, ADAPTER.TARGET, SHA)
        with self.assertRaises(ValueError):
            VERIFY.evaluate(*self.resources(), "other-node", SHA)

    def test_reusable_wrapper_is_dev_only_and_immutable(self):
        path = ROOT / ".github/workflows/dev-k3s-bootstrap.yml"
        if not path.exists():
            self.skipTest("wrapper is added after its immutable producer commit")
        doc = yaml.safe_load(path.read_text())
        trigger = doc.get("on", doc.get(True))
        self.assertEqual(set(trigger), {"workflow_call"})
        self.assertEqual(set(trigger["workflow_call"]["inputs"]), {"cmdb_artifact", "cmdb_sha256", "instance_id", "gitops_sha"})
        job = doc["jobs"]["bootstrap"]
        self.assertEqual(job["environment"], "dev")
        checkout = job["steps"][0]["with"]
        self.assertEqual(checkout["repository"], "ai-workspace-infra/playbooks")
        self.assertRegex(checkout["ref"], r"^[0-9a-f]{40}$")
        self.assertNotIn("secrets", trigger["workflow_call"])

    def test_root_and_unbounded_vault_sessions_are_rejected(self):
        valid = {"client_token": "synthetic-unit-value", "token_type": "service", "lease_duration": 300, "policies": ["default", "dev-bootstrap"]}
        self.assertEqual(ADAPTER.validate_session(valid), valid)
        for field in ("policies", "token_policies", "identity_policies"):
            session = {**valid, field: ["root"]}
            with self.assertRaises(ValueError):
                ADAPTER.validate_session(session)
        for ttl in (0, -1, 1201):
            with self.assertRaises(ValueError):
                ADAPTER.validate_session({**valid, "lease_duration": ttl})

    def test_single_node_receipt_is_opt_in_not_a_generic_role_change(self):
        defaults = yaml.safe_load((ROOT / "roles/vhosts/k3s_platform_bootstrap/defaults/main.yml").read_text())
        self.assertFalse(defaults["k3s_platform_strict_dev_receipt"])
        tasks = yaml.safe_load((ROOT / "roles/vhosts/k3s_platform_bootstrap/tasks/main.yml").read_text())
        observer = next(t for t in tasks if t["name"] == "Verify actual node cluster and observed Flux revision")
        self.assertEqual(observer["when"], "k3s_platform_strict_dev_receipt | bool")
        self.assertIn('"k3s_platform_strict_dev_receipt": True', (ROOT / "scripts/dev_k3s_bootstrap.py").read_text())

    def test_expected_runtime_k3s_version_and_architecture_are_required(self):
        args = self.resources()
        args[0]["items"][0]["status"]["nodeInfo"] = {"kubeletVersion": "v1.37.1+k3s1", "architecture": "amd64"}
        self.assertEqual(VERIFY.evaluate(*args, ADAPTER.TARGET, SHA, "v1.37.1+k3s1", "amd64")["node_info"]["architecture"], "amd64")
        for version, arch in (("v1.30.6+k3s1", "amd64"), ("v1.37.1+k3s1", "arm64")):
            with self.assertRaises(ValueError):
                VERIFY.evaluate(*args, ADAPTER.TARGET, SHA, version, arch)

    def test_source_commit_and_synchronous_role_are_present(self):
        tasks = (ROOT / "roles/vhosts/k3s_platform_bootstrap/tasks/main.yml").read_text()
        self.assertIsInstance(yaml.safe_load(tasks), list)
        self.assertIn("commit: {{ k3s_platform_git_commit }}", tasks)
        self.assertNotIn("poll: 0", tasks)
        self.assertIn("verify_bootstrap.py", tasks)
        script = (ROOT / "scripts/dev_k3s_bootstrap.py").read_text()
        self.assertNotIn("StrictHostKeyChecking=no", script)
        self.assertNotIn("ssh-keyscan", script)
        self.assertIn('["ssh-add", "-"]', script)


if __name__ == "__main__":
    unittest.main()
