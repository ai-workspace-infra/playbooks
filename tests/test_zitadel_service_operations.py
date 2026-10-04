"""ZITADEL service operations: executor behaviour with stubbed host commands and
local playbook simulation. Never contacts a real host or domain."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/docker/zitadel_server_operations"
HOST = ROLE / "files/verify_host_service.sh"
PUBLIC = ROLE / "files/verify_public_oidc.sh"
DIAGNOSE = ROLE / "files/diagnose_service.sh"
DOMAIN = "iam.example.test"
DISCOVERY = '{"issuer":"https://iam.example.test","jwks_uri":"https://iam.example.test/oauth/v2/keys"}'
PAT = "pat-content-must-never-be-read"

STUBS = {
    "docker": r'''#!/bin/sh
printf 'docker %s\n' "$*" >> "$TEST_LOG"
case "$*" in
  *"{{.State.Health.Status}}"*shared-zitadel-zitadel-1) printf '%s\n' "${TEST_ZITADEL_HEALTH:-healthy}" ;;
  *"{{.State.Health.Status}}"*shared-zitadel-login-1) printf '%s\n' "${TEST_LOGIN_HEALTH:-healthy}" ;;
  *"{{json .State.Health}}"*) printf '{"Status":"unhealthy","Log":[{"Output":"Authorization: Bearer leaked-probe-token"}]}\n' ;;
  "ps "*) printf 'shared-zitadel-zitadel-1 img Up\n' ;;
esac
''',
    "curl": r'''#!/bin/sh
printf 'curl %s\n' "$*" >> "$TEST_LOG"
case "$*" in
  *"%{http_code}"*) printf '200\n' ;;
  *"http://127.0.0.1:"*) exit "${TEST_API_EXIT:-0}" ;;
  *"--resolve"*) [ "${TEST_LOCAL_EXIT:-0}" = 0 ] || exit "$TEST_LOCAL_EXIT"; printf '%s' "${TEST_LOCAL_BODY}" ;;
  *) [ "${TEST_PUBLIC_EXIT:-0}" = 0 ] || exit "$TEST_PUBLIC_EXIT"; printf '%s' "${TEST_PUBLIC_BODY}" ;;
esac
''',
    "systemctl": r'''#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$TEST_LOG"
case "$*" in
  "is-active --quiet caddy") exit "${TEST_CADDY_EXIT:-0}" ;;
  "is-active caddy") echo active ;;
  *) echo 'ActiveState=active' ;;
esac
''',
    "ss": "#!/bin/sh\nprintf 'ss %s\\n' \"$*\" >> \"$TEST_LOG\"\necho 'LISTEN 0 4096 *:443'\n",
    "journalctl": "#!/bin/sh\nprintf 'journalctl %s\\n' \"$*\" >> \"$TEST_LOG\"\n"
                  "echo 'caddy: upstream Authorization: Bearer leaked-journal-token hvs.leakedvaulttoken'\n",
    "sleep": "#!/bin/sh\nexit 0\n",
}


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name, content in STUBS.items():
            path = self.bin / name
            path.write_text(content)
            path.chmod(0o700)
        self.workspace = self.dir / "zitadel"
        self.workspace.mkdir()
        (self.workspace / "login-client.pat").write_text(PAT)
        (self.dir / "conf.d").mkdir()
        self.log = self.dir / "commands.log"
        self.env = dict(
            os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", TEST_LOG=str(self.log),
            TEST_LOCAL_BODY=DISCOVERY, TEST_PUBLIC_BODY=DISCOVERY,
            ZITADEL_DOMAIN=DOMAIN, ZITADEL_WORKSPACE=str(self.workspace), ZITADEL_API_PORT="19080",
            ZITADEL_CADDY_CONF_DIR=str(self.dir / "conf.d"),
            ZITADEL_READY_TIMEOUT_SECONDS="1", ZITADEL_READY_POLL_SECONDS="1",
        )

    def run_script(self, script, **overrides):
        return subprocess.run(["bash", str(script)], env=dict(self.env, **overrides),
                              capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""


class HostVerification(Base):
    def test_healthy_service_passes_with_strict_tls_on_the_exact_domain(self):
        result = self.run_script(HOST)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("shared-zitadel-zitadel-1", calls)
        self.assertIn("shared-zitadel-login-1", calls)
        self.assertIn("systemctl is-active --quiet caddy", calls)
        self.assertIn(f"--resolve {DOMAIN}:443:127.0.0.1", calls)
        self.assertIn("http://127.0.0.1:19080/.well-known/openid-configuration", calls)
        for line in calls.splitlines():
            if line.startswith("curl "):
                self.assertNotIn(" -k", line)
                self.assertNotIn("--insecure", line)

    def test_each_unhealthy_condition_fails(self):
        cases = {
            "API container": {"TEST_ZITADEL_HEALTH": "unhealthy"},
            "Login container": {"TEST_LOGIN_HEALTH": "starting"},
            "Caddy": {"TEST_CADDY_EXIT": "3"},
            "loopback API": {"TEST_API_EXIT": "7"},
            "local TLS transport": {"TEST_LOCAL_EXIT": "60"},
            "foreign issuer": {"TEST_LOCAL_BODY": DISCOVERY.replace("https://iam.example.test\"", "http://iam.example.test\"", 1)},
            "foreign keys": {"TEST_LOCAL_BODY": DISCOVERY.replace("https://iam.example.test/oauth", "https://evil.example.test/oauth")},
            "non-JSON": {"TEST_LOCAL_BODY": "<html>"},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                result = self.run_script(HOST, **overrides)
                self.assertNotEqual(result.returncode, 0, label)

    def test_missing_pat_means_bootstrap_incomplete(self):
        (self.workspace / "login-client.pat").unlink()
        result = self.run_script(HOST)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap did not complete", result.stderr)

    def test_invalid_inputs_fail_before_any_host_command(self):
        for overrides in ({"ZITADEL_DOMAIN": "iam.example.test;id"}, {"ZITADEL_DOMAIN": ""},
                          {"ZITADEL_WORKSPACE": "relative"}, {"ZITADEL_API_PORT": "0"},
                          {"ZITADEL_READY_TIMEOUT_SECONDS": "x"}):
            with self.subTest(overrides=overrides):
                result = self.run_script(HOST, **overrides)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), "")


class PublicVerification(Base):
    def test_issuer_and_keys_on_the_declared_domain_pass(self):
        result = self.run_script(PUBLIC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"https://{DOMAIN}/.well-known/openid-configuration", self.calls())
        self.assertNotIn("docker", self.calls())

    def test_wrong_document_or_unreachable_endpoint_fails(self):
        for overrides in ({"TEST_PUBLIC_BODY": DISCOVERY.replace(DOMAIN, "other.example.test")},
                          {"TEST_PUBLIC_BODY": "{}"}, {"TEST_PUBLIC_EXIT": "22"}):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_script(PUBLIC, **overrides).returncode, 0)


class Diagnostics(Base):
    def test_evidence_is_read_only_redacted_and_never_reads_the_pat(self):
        result = self.run_script(DIAGNOSE, TEST_CADDY_EXIT="3")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        for section in ("ZITADEL stack containers", "Login client PAT present", "caddy service",
                        "listeners", "local HTTPS via Caddy", "local API", "caddy journal"):
            self.assertIn(section, output)
        for leaked in ("leaked-journal-token", "leaked-probe-token", "hvs.leakedvaulttoken", PAT):
            self.assertNotIn(leaked, output)
        # Only read-only subcommands reach Docker and systemd.
        for line in self.calls().splitlines():
            command, subcommand = line.split()[:2]
            if command == "docker":
                self.assertIn(subcommand, ("inspect", "ps"), line)
            if command == "systemctl":
                self.assertIn(subcommand, ("is-active", "show"), line)


class Playbook(Base):
    def playbook(self, *extra, operation="verify_host", target="localhost"):
        args = ["ansible-playbook", "-i", "localhost,", "-c", "local", "zitadel_operations.yml",
                "-e", "ansible_become=false", "-e", f"zitadel_operation={operation}",
                "-e", f"zitadel_operations_domain={DOMAIN}",
                "-e", f"zitadel_operations_workspace={self.workspace}",
                "-e", f"zitadel_operations_caddy_conf_dir={self.dir / 'conf.d'}",
                "-e", "zitadel_operations_ready_timeout_seconds=1",
                "-e", "zitadel_operations_ready_poll_seconds=1", *extra]
        if target is not None:
            args += ["-e", f"zitadel_operations_target={target}"]
        return subprocess.run(args, cwd=ROOT, env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300)

    def test_host_operation_verifies_host_then_public_endpoint(self):
        result = self.playbook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertLess(calls.index("--resolve"), calls.index(f"curl --fail --silent --show-error --retry 6"))
        self.assertNotIn("journalctl", calls)

    def test_failed_host_check_collects_evidence_and_still_fails(self):
        self.env["TEST_CADDY_EXIT"] = "3"
        result = self.playbook()
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls()
        self.assertIn("journalctl -u caddy", calls)
        self.assertNotIn("--retry 6", calls)  # public check never ran
        self.assertIn("Host evidence", result.stdout)
        self.assertNotIn("leaked-journal-token", result.stdout + result.stderr)

    def test_failed_public_check_also_collects_evidence_and_fails(self):
        self.env["TEST_PUBLIC_EXIT"] = "22"
        result = self.playbook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("journalctl -u caddy", self.calls())

    def test_public_operation_needs_no_host_commands(self):
        result = self.playbook(operation="verify_public")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertIn("--retry 6", calls)
        for host_command in ("docker", "systemctl", "--resolve"):
            self.assertNotIn(host_command, calls)

    def test_unsupported_operation_or_domain_fails_before_any_command(self):
        for kwargs, extra in (({"operation": "deploy"}, ()), ({"operation": "restart"}, ()),
                              ({}, ("-e", "zitadel_operations_domain=Bad_Domain"))):
            with self.subTest(kwargs=kwargs, extra=extra):
                result = self.playbook(*extra, **kwargs)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), "")

    def test_unknown_or_missing_target_fails_instead_of_matching_no_hosts(self):
        for target in ("iam-shared-0", "all", None):
            with self.subTest(target=target):
                result = self.playbook(target=target)
                self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), "")


class Contract(unittest.TestCase):
    def test_role_tasks_are_read_only(self):
        allowed = {"ansible.builtin.assert", "ansible.builtin.command", "ansible.builtin.script",
                   "ansible.builtin.include_tasks", "ansible.builtin.fail"}
        for path in (ROLE / "tasks").glob("*.yml"):
            for task in yaml.safe_load(path.read_text()):
                for step in task.get("block", [task]) + task.get("rescue", []):
                    modules = allowed & set(step)
                    self.assertTrue(modules, f"{path.name}: {step.get('name')} uses an unexpected module")
        for script in (HOST, PUBLIC, DIAGNOSE):
            text = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
            for verb in ("restart", "reload", "docker compose", "docker rm", "docker stop", "docker start",
                         "docker exec", "volume", "psql", "DROP", "cat ", "login-client.pat\" |"):
                self.assertNotIn(verb, text, f"{script.name}: {verb}")

    def test_rescue_collects_evidence_without_masking_the_failure(self):
        tasks = yaml.safe_load((ROLE / "tasks/verify_host.yml").read_text())
        gate = next(t for t in tasks if "block" in t)
        evidence, final = gate["rescue"]
        self.assertIs(evidence["failed_when"], False)
        self.assertIs(evidence["changed_when"], False)
        self.assertIn("ansible.builtin.fail", final)
        names = [step["name"] for step in gate["block"]]
        self.assertEqual(names, ["Verify stack health, bootstrap completion, Caddy and local TLS discovery",
                                 "Verify public OIDC discovery after the host checks"])

    def test_defaults_match_the_deploy_role_and_container_names(self):
        ours = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        deploy = yaml.safe_load((ROOT / "roles/docker/zitadel/defaults/main.yml").read_text())
        self.assertEqual(ours["zitadel_operations_workspace"], deploy["zitadel_deploy_dir"])
        self.assertEqual(ours["zitadel_operations_api_port"], deploy["zitadel_api_port"])
        self.assertEqual(ours["zitadel_operations_domain"], "")
        doco = (ROOT / "roles/docker/zitadel/tasks/doco-cd.yml").read_text()
        self.assertIn("shared-zitadel-{{ item.service }}-1", doco)
        self.assertIn("{{ zitadel_workspace }}/login-client.pat", doco)
        self.assertIn('"shared-zitadel-${service}-1"', HOST.read_text())


if __name__ == "__main__":
    unittest.main()
