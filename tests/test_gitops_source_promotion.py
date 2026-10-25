import copy
import datetime
import importlib.util
import json
import subprocess
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
                self.root["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
                self.root["status"]["observedGeneration"] = self.root["metadata"]["generation"]
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

    def rollback_case(self, mode="apply", converge=True):
        c = config(mode)
        c.update(rollback=True, rollback_ancestor_verified=True,
                 rollback_reason="Recover failed process-exporter health canary")
        client = Client(c, converge=converge)
        client.root["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
        client.root["status"]["lastAppliedRevision"] = c["desired_tag"] + "@sha1:" + c["desired_sha"]
        return c, client

    def test_explicit_rollback_recovers_known_unhealthy_root_and_retains_evidence(self):
        c, client = self.rollback_case()
        report = owner.execute(c, client, self.machine)
        self.assertEqual(report["result"], "verified")
        self.assertEqual(report["direction"], "rollback")
        self.assertFalse(report["recovery"]["baseline_root_ready"])
        self.assertTrue(report["recovery"]["baseline_source_ready"])
        self.assertTrue(report["recovery"]["ancestor_verified"])
        self.assertEqual(len(client.calls), 1)
        operations = json.loads(client.calls[0][client.calls[0].index("-p") + 1])
        self.assertEqual([op["op"] for op in operations], ["test", "test", "replace"])
        self.assertEqual(operations[0]["path"], "/metadata/resourceVersion")

    def test_rollback_plan_does_not_mutate_and_forward_still_rejects_unhealthy_root(self):
        c, client = self.rollback_case("plan")
        self.assertEqual(owner.execute(c, client, self.machine)["result"], "planned")
        self.assertEqual(client.calls, [])
        c["rollback"] = False
        with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
        self.assertEqual(client.calls, [])

    def test_rollback_rejects_unknown_root_or_unready_source(self):
        for mutate in (
                lambda c, x: x.root["status"].update(lastAppliedRevision=""),
                lambda c, x: x.root["status"].update(lastAppliedRevision="other@sha1:" + "e" * 40),
                lambda c, x: x.source["status"].update(conditions=[{"type": "Ready", "status": "False"}]),
                lambda c, x: x.source["status"]["artifact"].update(revision="other@sha1:" + "e" * 40),
                lambda c, x: x.source["status"].update(observedGeneration=0)):
            c, client = self.rollback_case()
            mutate(c, client)
            with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
            self.assertEqual(client.calls, [])

    def test_rollback_requires_ancestor_attestation_and_reason(self):
        for values in ({"rollback_ancestor_verified": False}, {"rollback_reason": ""}, {"rollback": "true"}):
            c, client = self.rollback_case()
            c.update(values)
            with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
            self.assertEqual(client.calls, [])

    def test_rollback_timeout_preserves_recovery_and_idempotent_success_does_not_patch(self):
        c, client = self.rollback_case(converge=False)
        times = iter((0, 20))
        report = owner.execute(c, client, self.machine, monotonic=lambda: next(times))
        self.assertEqual(report["result"], "mutated_and_failed")
        self.assertFalse(report["recovery"]["baseline_root_ready"])
        c, client = self.rollback_case()
        owner.execute(c, client, self.machine)
        client.calls.clear()
        self.assertEqual(owner.execute(c, client, self.machine)["result"], "verified")
        self.assertEqual(client.calls, [])

    def forward_recovery_case(self, mode="apply", converge=True):
        c = config(mode)
        c.update(recover_forward=True, recover_forward_ancestor_verified=True,
                 recovery_reason="Advance the reviewed plugin archive-bound repair")
        client = Client(c, converge=converge)
        client.root["status"]["conditions"] = [{"type": "Ready", "status": "False"}]
        return c, client

    def test_explicit_forward_recovery_only_allows_known_unhealthy_root(self):
        for applied in ("expected", "desired"):
            c, client = self.forward_recovery_case()
            client.root["status"]["lastAppliedRevision"] = c[applied + "_tag"] + "@sha1:" + c[applied + "_sha"]
            report = owner.execute(c, client, self.machine)
            self.assertEqual(report["result"], "verified")
            self.assertEqual(report["direction"], "recover-forward")
            self.assertEqual(report["recovery"]["ancestry_direction"], "expected_to_desired")
            self.assertFalse(report["recovery"]["baseline_root_ready"])
            self.assertTrue(report["recovery"]["baseline_source_ready"])
            self.assertEqual(len(client.calls), 1)
            operations = json.loads(client.calls[0][client.calls[0].index("-p") + 1])
            self.assertEqual([op["op"] for op in operations], ["test", "test", "replace"])
            self.assertEqual(operations[-1]["path"], "/spec/ref")

    def test_recover_plan_is_readonly_and_default_forward_is_not_weakened(self):
        c, client = self.forward_recovery_case("plan")
        self.assertEqual(owner.execute(c, client, self.machine)["result"], "planned")
        self.assertEqual(client.calls, [])
        c["recover_forward"] = False
        with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
        self.assertEqual(client.calls, [])

    def test_recover_forward_requires_attestation_reason_and_no_rollback(self):
        for values in ({"recover_forward_ancestor_verified": False}, {"recovery_reason": ""},
                       {"recovery_reason": "line\nbreak"}, {"recovery_reason": " " * 301 + "repair"},
                       {"recovery_reason": "   "}, {"recover_forward": "true"},
                       {"rollback": True, "rollback_ancestor_verified": True, "rollback_reason": "previous"}):
            c, client = self.forward_recovery_case()
            c.update(values)
            with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
            self.assertEqual(client.calls, [])

    def test_recover_forward_rejects_unknown_root_source_and_scope(self):
        for mutate in (
                lambda c, x: x.root["status"].update(lastAppliedRevision=""),
                lambda c, x: x.root["status"].update(lastAppliedRevision="unknown@sha1:" + "e" * 40),
                lambda c, x: x.source["spec"].update(ref={"branch": "main"}),
                lambda c, x: x.source["status"].update(conditions=[{"type": "Ready", "status": "False"}]),
                lambda c, x: x.source["status"]["artifact"].update(revision="unknown@sha1:" + "e" * 40),
                lambda c, x: x.source["status"].update(observedGeneration=0),
                lambda c, x: x.root["spec"].update(path="./unrelated")):
            c, client = self.forward_recovery_case()
            mutate(c, client)
            with self.assertRaises(ValueError): owner.execute(c, client, self.machine)
            self.assertEqual(client.calls, [])

    def test_forward_recovery_timeout_unknown_mutation_and_retry_are_not_success(self):
        c, client = self.forward_recovery_case(converge=False)
        times = iter((0, 20))
        report = owner.execute(c, client, self.machine, monotonic=lambda: next(times))
        self.assertEqual(report["result"], "mutated_and_failed")
        self.assertEqual(report["direction"], "recover-forward")
        c, client = self.forward_recovery_case()
        client.patch_error = True
        report = owner.execute(c, client, self.machine)
        self.assertEqual(report["result"], "mutation_failed_or_unknown")
        self.assertEqual(report["changed"], "UNKNOWN")
        c, client = self.forward_recovery_case()
        owner.execute(c, client, self.machine)
        client.calls.clear()
        self.assertEqual(owner.execute(c, client, self.machine)["result"], "verified")
        self.assertEqual(client.calls, [])

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

    def test_rollback_requires_matching_cli_and_boolean_plan_marker(self):
        for plan, flag in (({}, True), ({"rollback": True, "rollback_reason": "failure"}, False),
                           ({"rollback": "true", "rollback_reason": "failure"}, True),
                           ({"rollback": True}, True)):
            with self.assertRaises(ValueError): runner.rollback_requested(plan, flag)
        self.assertTrue(runner.rollback_requested({"rollback": True, "rollback_reason": "failed canary"}, True))
        self.assertFalse(runner.rollback_requested({}, False))

    def test_rollback_ancestry_and_scope_are_both_required(self):
        c = config()
        plan = {"cluster": c, "release": c, "rollback": True, "rollback_reason": "failed canary"}
        calls = []
        def fake_git(directory, *args):
            calls.append(args)
            if args[:3] == ("remote", "get-url", "origin"): return c["repo_url"]
            if args[0] == "rev-parse": return c["expected_sha"] if c["expected_tag"] in args[1] else c["desired_sha"]
            if args[0] == "ls-remote":
                tag = args[2].split("/")[-1]
                return (c["expected_sha"] if tag == c["expected_tag"] else c["desired_sha"]) + "\trefs/tags/" + tag
            if args[0] == "merge-base": return ""
            if args[0] == "diff": return "services/platform-observability/prod/values.yaml"
            if args[0] == "show" and args[1].endswith(runner.ROOT_SOURCE_PATH):
                return json.dumps({"kind": "GitRepository", "metadata": {"name": owner.SOURCE, "namespace": "flux-system"},
                                   "spec": {"url": c["repo_url"], "ref": {"tag": c["desired_tag"]}}})
            if args[0] == "show":
                return json.dumps({"kind": "Kustomization", "metadata": {"name": owner.ROOT_KUSTOMIZATION},
                                   "spec": {"path": owner.ROOT_PATH, "sourceRef": {"name": owner.SOURCE}}})
            raise AssertionError(args)
        with patch.object(runner, "git", side_effect=fake_git):
            self.assertEqual(runner.verify_release(plan, "/tmp/example", True), ["services/platform-observability/prod/values.yaml"])
        self.assertIn(("merge-base", "--is-ancestor", c["desired_sha"], c["expected_sha"]), calls)
        def bad_ancestor(directory, *args):
            if args[0] == "merge-base": raise subprocess.CalledProcessError(1, "git")
            return fake_git(directory, *args)
        with patch.object(runner, "git", side_effect=bad_ancestor):
            with self.assertRaisesRegex(ValueError, "not a verified ancestor"): runner.verify_release(plan, "/tmp/example", True)
        with patch.object(runner, "git", side_effect=lambda directory, *args: "services/unrelated/prod/values.yaml" if args[0] == "diff" else fake_git(directory, *args)):
            with self.assertRaisesRegex(ValueError, "non-observability"): runner.verify_release(plan, "/tmp/example", True)

    def test_forward_recovery_cli_and_plan_markers_must_match(self):
        for plan, flag in (({}, True), ({"recover_forward": True, "recovery_reason": "repair"}, False),
                           ({"recover_forward": "true", "recovery_reason": "repair"}, True),
                           ({"recover_forward": True}, True),
                           ({"recover_forward": True, "recovery_reason": "repair", "rollback": True}, True)):
            with self.assertRaises(ValueError): runner.recover_forward_requested(plan, flag)
        self.assertTrue(runner.recover_forward_requested({"recover_forward": True, "recovery_reason": "reviewed repair"}, True))
        self.assertFalse(runner.recover_forward_requested({}, False))
        command = [__import__("sys").executable, str(ROOT / "scripts/run_gitops_source_promotion.py"),
                   "--plan", "/unused", "--cmdb", "/unused", "--gitops-root", "/unused", "--evidence-dir", "/unused",
                   "--rollback", "--recover-forward"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with argument", result.stderr)

    def test_forward_recovery_ancestry_direction_and_scope_are_verified(self):
        c = config()
        plan = {"cluster": c, "release": c, "recover_forward": True, "recovery_reason": "plugin repair"}
        calls = []
        def fake_git(directory, *args):
            calls.append(args)
            if args[:3] == ("remote", "get-url", "origin"): return c["repo_url"]
            if args[0] == "rev-parse": return c["expected_sha"] if c["expected_tag"] in args[1] else c["desired_sha"]
            if args[0] == "ls-remote":
                tag = args[2].split("/")[-1]
                return (c["expected_sha"] if tag == c["expected_tag"] else c["desired_sha"]) + "\trefs/tags/" + tag
            if args[0] == "merge-base": return ""
            if args[0] == "diff": return "services/platform-observability/prod/release.yaml"
            if args[0] == "show" and args[1].endswith(runner.ROOT_SOURCE_PATH):
                return json.dumps({"kind": "GitRepository", "metadata": {"name": owner.SOURCE, "namespace": "flux-system"},
                                   "spec": {"url": c["repo_url"], "ref": {"tag": c["desired_tag"]}}})
            if args[0] == "show":
                return json.dumps({"kind": "Kustomization", "metadata": {"name": owner.ROOT_KUSTOMIZATION},
                                   "spec": {"path": owner.ROOT_PATH, "sourceRef": {"name": owner.SOURCE}}})
            raise AssertionError(args)
        with patch.object(runner, "git", side_effect=fake_git):
            self.assertEqual(runner.verify_release(plan, "/tmp/example", recover_forward=True),
                             ["services/platform-observability/prod/release.yaml"])
        self.assertIn(("merge-base", "--is-ancestor", c["expected_sha"], c["desired_sha"]), calls)
        def bad_ancestor(directory, *args):
            if args[0] == "merge-base": raise subprocess.CalledProcessError(1, "git")
            return fake_git(directory, *args)
        with patch.object(runner, "git", side_effect=bad_ancestor):
            with self.assertRaisesRegex(ValueError, "not a verified ancestor"):
                runner.verify_release(plan, "/tmp/example", recover_forward=True)
        with patch.object(runner, "git", side_effect=lambda directory, *args: "services/unrelated/prod/config.yaml" if args[0] == "diff" else fake_git(directory, *args)):
            with self.assertRaisesRegex(ValueError, "non-observability"):
                runner.verify_release(plan, "/tmp/example", recover_forward=True)
        with patch.object(runner, "git", side_effect=fake_git):
            bad = copy.deepcopy(plan); bad["release"]["desired_tag"] = "main"
            with self.assertRaises(ValueError): runner.verify_release(bad, "/tmp/example", recover_forward=True)
            same = copy.deepcopy(plan); same["release"]["desired_sha"] = same["cluster"]["expected_sha"]
            with self.assertRaises(ValueError): runner.verify_release(same, "/tmp/example", recover_forward=True)

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
