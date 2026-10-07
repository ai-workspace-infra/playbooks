import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "xconnect-lab-runtime.py"
OWNER_SHA = "a" * 40
SPEC = importlib.util.spec_from_file_location("xconnect_lab_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeRunner:
    def __init__(self, ansible_returncode=0, known=True, raise_ansible=False, checkout_sha=OWNER_SHA):
        self.calls = []
        self.ansible_returncode = ansible_returncode
        self.known = known
        self.raise_ansible = raise_ansible
        self.checkout_sha = checkout_sha

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:2] == ["ssh-keygen", "-F"]:
            return SimpleNamespace(
                returncode=0 if self.known else 1,
                stdout="# Host one-uat.svc.plus found\none-uat.svc.plus ssh-ed25519 a2V5YmxvYg==\n",
            )
        if command[0] == "git":
            return SimpleNamespace(returncode=0, stdout=self.checkout_sha + "\n", stderr="")
        if command[0] == "ansible-playbook":
            if self.raise_ansible:
                raise RuntimeError("runner exception")
            return SimpleNamespace(returncode=self.ansible_returncode, stdout="", stderr="")
        raise AssertionError(command)


class RuntimeOwnerTests(unittest.TestCase):
    def environment(self, root):
        for name, body in (("key", "PRIVATE"), ("known_hosts", "host key")):
            path = root / name
            path.write_text(body)
            path.chmod(0o600)
        variables = root / "variables.json"
        variables.write_text(json.dumps({
            "xconnect_one_enabled": True,
            "xconnect_one_device_id": "one-lab-1",
        }))
        variables.chmod(0o600)
        return {
            "RUNNER_TEMP": str(root),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "XCONNECT_RUNTIME_OWNER_SHA": OWNER_SHA,
            "XCONNECT_RUNTIME_ACTION_REF": OWNER_SHA,
            "XCONNECT_RUNTIME_OPERATION": "one",
            "XCONNECT_RUNTIME_TARGET": "one-uat.svc.plus",
            "XCONNECT_RUNTIME_SSH_USER": "deployer",
            "XCONNECT_RUNTIME_PRIVATE_KEY_FILE": str(root / "key"),
            "XCONNECT_RUNTIME_KNOWN_HOSTS_FILE": str(root / "known_hosts"),
            "XCONNECT_RUNTIME_VARIABLES_FILE": str(variables),
            "XCONNECT_RUNTIME_RECEIPT_FILE": str(root / "receipt.json"),
            "GITHUB_OUTPUT": str(root / "output"),
        }

    def test_exact_target_runs_role_with_strict_host_checking_and_private_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            runner = FakeRunner()
            receipt = MODULE.execute(env, runner)
            command, options = runner.calls[-1]
            self.assertEqual(command[0], "ansible-playbook")
            self.assertEqual(command[command.index("--limit") + 1], "xconnect-owner-target")
            self.assertEqual(options["env"]["ANSIBLE_HOST_KEY_CHECKING"], "True")
            self.assertIn("StrictHostKeyChecking=yes", options["env"]["ANSIBLE_SSH_ARGS"])
            self.assertNotIn("accept-new", options["env"]["ANSIBLE_SSH_ARGS"])
            self.assertRegex(receipt["host_key_fingerprint"], r"^SHA256:[A-Za-z0-9+/]+$")
            self.assertEqual(receipt["run_id"], "xcl-123-1")
            self.assertEqual(receipt["owner_sha"], OWNER_SHA)
            self.assertEqual((root / "receipt.json").stat().st_mode & 0o777, 0o600)
            self.assertFalse(any(path.name.endswith("inventory.json") for path in root.iterdir()))

    def test_pattern_target_and_untrusted_target_fail_before_ansible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            env["XCONNECT_RUNTIME_TARGET"] = "*.svc.plus"
            runner = FakeRunner()
            with self.assertRaisesRegex(MODULE.ContractError, "one exact host"):
                MODULE.execute(env, runner)
            env["XCONNECT_RUNTIME_TARGET"] = "one-uat.svc.plus"
            with self.assertRaisesRegex(MODULE.ContractError, "does not contain"):
                MODULE.execute(env, FakeRunner(known=False))
            for unsafe in ("localhost", "127.0.0.1", "169.254.10.2"):
                env["XCONNECT_RUNTIME_TARGET"] = unsafe
                with self.subTest(target=unsafe), self.assertRaises(MODULE.ContractError):
                    MODULE.execute(env, FakeRunner())

    def test_run_owner_binding_and_runner_exception_remove_old_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            receipt = Path(env["XCONNECT_RUNTIME_RECEIPT_FILE"])
            receipt.write_text('{"status":"completed"}')
            receipt.chmod(0o600)
            env["XCONNECT_RUNTIME_ACTION_REF"] = "b" * 40
            with self.assertRaisesRegex(MODULE.ContractError, "action ref"):
                MODULE.execute(env, FakeRunner())
            self.assertFalse(receipt.exists())
            receipt.write_text('{"status":"completed"}')
            receipt.chmod(0o600)
            env["XCONNECT_RUNTIME_ACTION_REF"] = OWNER_SHA
            with self.assertRaisesRegex(RuntimeError, "runner exception"):
                MODULE.execute(env, FakeRunner(raise_ansible=True))
            self.assertFalse(receipt.exists())

    def test_local_action_binds_the_explicit_checkout_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(Path(directory))
            env["XCONNECT_RUNTIME_ACTION_REF"] = ""
            self.assertEqual(MODULE.execute(env, FakeRunner())["owner_sha"], OWNER_SHA)
            with self.assertRaisesRegex(MODULE.ContractError, "checked-out Playbooks SHA"):
                MODULE.execute(env, FakeRunner(checkout_sha="b" * 40))

    def test_cloud_variables_and_public_secrets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            variables = Path(env["XCONNECT_RUNTIME_VARIABLES_FILE"])
            variables.write_text('{"terraform_state":"forbidden"}')
            with self.assertRaisesRegex(MODULE.ContractError, "forbidden cloud"):
                MODULE.execute(env, FakeRunner())
            variables.write_text('{"ansible_host":"redirected.example"}')
            with self.assertRaisesRegex(MODULE.ContractError, "forbidden cloud"):
                MODULE.execute(env, FakeRunner())
            variables.write_text('{}')
            variables.chmod(0o644)
            with self.assertRaisesRegex(MODULE.ContractError, "group or other"):
                MODULE.execute(env, FakeRunner())

    def test_failed_ansible_leaves_no_success_receipt_or_private_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.environment(root)
            with self.assertRaisesRegex(MODULE.ContractError, "exit 4"):
                MODULE.execute(env, FakeRunner(ansible_returncode=4))
            self.assertFalse(Path(env["XCONNECT_RUNTIME_RECEIPT_FILE"]).exists())
            self.assertFalse(any(path.name.endswith("inventory.json") for path in root.iterdir()))
            self.assertTrue(Path(env["XCONNECT_RUNTIME_PRIVATE_KEY_FILE"]).exists())


if __name__ == "__main__":
    unittest.main()
