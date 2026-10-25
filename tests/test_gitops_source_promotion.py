import copy
import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


owner = load("source_owner", ROOT / "roles/platform/gitops_source_promotion/files/promote.py")
runner = load("source_runner", ROOT / "scripts/run_gitops_source_promotion.py")


def config(mode="plan"):
    return {"environment": "prod", "mode": mode, "machine_id": "a" * 32,
            "expected_tag": "v2026.10.25.3", "expected_sha": "b" * 40,
            "desired_tag": "v2026.10.25.4", "desired_sha": "c" * 40,
            "source_name": owner.SOURCE, "source_namespace": "flux-system",
            "kustomization_name": owner.ROOT_KUSTOMIZATION, "cluster_root": owner.ROOT_PATH,
            "repo_url": "https://github.com/ai-workspace-infra/gitops.git",
            "kubeconfig": "/etc/rancher/k3s/k3s.yaml", "admin_kubeconfig": "/etc/rancher/k3s/k3s.yaml",
            "context": "default", "wait_seconds": 10}


def objects(c):
    revision = c["expected_tag"] + "@sha1:" + c["expected_sha"]
    source = {"metadata": {"name": owner.SOURCE, "namespace": "flux-system", "generation": 1, "resourceVersion": "123"},
              "spec": {"url": c["repo_url"], "ref": {"tag": c["expected_tag"]}},
              "status": {"observedGeneration": 1, "artifact": {"revision": revision},
                         "conditions": [{"type": "Ready", "status": "True"}]}}
    root = {"metadata": {"name": owner.ROOT_KUSTOMIZATION, "namespace": "flux-system", "generation": 1},
            "spec": {"path": owner.ROOT_PATH, "sourceRef": {"kind": "GitRepository", "name": owner.SOURCE}},
            "status": {"observedGeneration": 1, "lastAppliedRevision": revision,
                       "conditions": [{"type": "Ready", "status": "True"}]}}
    return source, root


class Client:
    def __init__(self, c, converge=True, patch_error=False, read_error=False):
        self.c, self.source, self.root = c, *objects(c)
        self.calls, self.converge, self.patch_error, self.read_error = [], converge, patch_error, read_error
        self.patched = False

    def read(self, kind, name):
        if self.patched and self.read_error:
            raise ValueError("lost read")
        return copy.deepcopy(self.source if kind == "gitrepository" else self.root)

    def call(self, args, boolean=False):
        self.calls.append(args)
        if "patch" in args:
            self.patched = True
            if self.patch_error: raise ValueError("response lost")
            self.source["spec"]["ref"] = {"tag": self.c["desired_tag"]}
            if self.converge:
                rev = self.c["desired_tag"] + "@sha1:" + self.c["desired_sha"]
                self.source["status"]["artifact"]["revision"] = rev
                self.root["status"]["lastAppliedRevision"] = rev
        return {}


class SourceOwnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.machine = Path(self.tmp.name) / "machine-id"
        self.machine.write_text("a" * 32)

    def test_plan_never_mutates_and_preserves_old_ref(self):
        c = config()
        client = Client(c)
        report = owner.execute(c, client, self.machine)
        self.assertEqual(report["result"], "planned")
        self.assertEqual(client.calls, [])
        self.assertEqual(report["expected_ref"]["sha"], "b" * 40)
        self.assertEqual(report["audit_identity_isolation"], "NOT_RUN")

    def test_apply_patches_only_source_ref_with_concurrency_tests(self):
        c = config("apply")
        client = Client(c)
        report = owner.execute(c, client, self.machine)
        self.assertEqual(report["result"], "verified")
        self.assertTrue(report["changed"])
        self.assertEqual(len(client.calls), 1)
        operations = json.loads(client.calls[0][client.calls[0].index("-p") + 1])
        self.assertEqual([x["path"] for x in operations], ["/metadata/resourceVersion", "/spec/ref", "/spec/ref"])
        self.assertEqual(operations[-1]["value"], {"tag": c["desired_tag"]})
        self.assertEqual(report["runtime_metrics_acceptance"], "NOT_RUN")

    def test_mismatched_identity_or_baseline_rejects_before_patch(self):
        for mutate in (lambda c, x: c.update(machine_id="d" * 32),
                       lambda c, x: x.source["spec"].update(ref={"branch": "main"}),
                       lambda c, x: x.root["spec"].update(path="./other"),
                       lambda c, x: x.root["status"].update(lastAppliedRevision="stale")):
            c = config("apply")
            client = Client(c)
            mutate(c, client)
            with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
            self.assertEqual(client.calls, [])

    def test_idempotent_apply_does_not_patch_again(self):
        c = config("apply")
        client = Client(c)
        owner.execute(c, client, self.machine)
        client.calls.clear()
        report = owner.execute(c, client, self.machine)
        self.assertEqual(report["result"], "verified")
        self.assertFalse(report["changed"])
        self.assertEqual(client.calls, [])

    def test_timeout_and_lost_patch_response_do_not_become_preflight_success(self):
        c = config("apply")
        times = iter((0, 20))
        report = owner.execute(c, Client(c, converge=False), self.machine, monotonic=lambda: next(times))
        self.assertEqual(report["result"], "mutated_and_failed")
        report = owner.execute(c, Client(c, patch_error=True), self.machine)
        self.assertEqual(report["result"], "mutation_failed_or_unknown")
        self.assertEqual(report["changed"], "UNKNOWN")
        report = owner.execute(c, Client(c, read_error=True), self.machine)
        self.assertEqual(report["result"], "mutated_and_failed")

    def test_audit_rejects_root_or_readable_admin_config(self):
        c = config("audit-report")
        c.update(kubeconfig="/home/audit/kubeconfig", audit_username="audit")
        for euid, readable in ((0, False), (1001, True)):
            with patch.object(owner.os, "geteuid", return_value=euid), patch.object(owner.os, "access", return_value=readable):
                with self.assertRaises(ValueError): owner.execute(c, Client(c), self.machine)

    def test_prod_rejects_branch_like_ref_and_other_kubeconfig(self):
        for values in ({"desired_tag": "main"}, {"environment": "uat"}, {"kubeconfig": "/tmp/other"}):
            c = config(); c.update(values)
            with self.assertRaises(ValueError): owner.validate(c)


class ControllerTest(unittest.TestCase):
    def test_target_requires_fresh_exact_inventory_and_known_host(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        target = {"host": "target", "instance_id": "123", "machine_id": "a" * 32,
                  "ssh_user": "operator", "known_hosts": "192.0.2.1 ssh-ed25519 AAAA",
                  "verified_at": now.isoformat()}
        plan = {"environment": "prod", "owner_sha": "d" * 40, "target": target}
        inventory = {"_meta": {"hostvars": {"target": {"cmdb_instance_id": "123", "cmdb_environment": "prod",
                     "ansible_user": "operator", "ansible_host": "192.0.2.1", "ansible_port": 22}}}}
        self.assertEqual(runner.verify_target(plan, inventory, now)["cmdb_instance_id"], "123")
        for replacement in ({"instance_id": "different"}, {"known_hosts": "192.0.2.2 ssh-ed25519 AAAA"},
                            {"verified_at": (now - datetime.timedelta(hours=2)).isoformat()}):
            wrong = copy.deepcopy(plan); wrong["target"].update(replacement)
            with self.assertRaises(ValueError): runner.verify_target(wrong, inventory, now)

    def test_release_rejects_unrelated_changes_and_retagged_commit(self):
        c = config()
        plan = {"cluster": c, "release": c}
        def fake_git(directory, *args):
            if args[:3] == ("remote", "get-url", "origin"): return c["repo_url"]
            if args[0] == "rev-parse": return c["expected_sha"] if c["expected_tag"] in args[1] else c["desired_sha"]
            if args[0] == "ls-remote":
                tag = args[2].split("/")[-1]
                sha = c["expected_sha"] if tag == c["expected_tag"] else c["desired_sha"]
                return sha + "\trefs/tags/" + tag
            if args[0] == "diff": return "services/unrelated/prod/values.yaml"
            raise AssertionError(args)
        with patch.object(runner, "git", side_effect=fake_git):
            with self.assertRaisesRegex(ValueError, "non-observability"): runner.verify_release(plan, "/tmp/example")
        with patch.object(runner, "git", side_effect=lambda directory, *args: "different" if args[0] == "rev-parse" else fake_git(directory, *args)):
            with self.assertRaisesRegex(ValueError, "local tag"): runner.verify_release(plan, "/tmp/example")


if __name__ == "__main__": unittest.main()
