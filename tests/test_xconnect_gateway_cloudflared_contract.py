#!/usr/bin/env python3
"""Contract tests for xconnect_gateway_cloudflared and xconnect_one pg_forward."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLOUDFLARED_ROLE = ROOT / "roles/vhosts/xconnect_gateway_cloudflared"
CLOUDFLARED_ALIAS = ROOT / "roles/xconnect_gateway_cloudflared"
ONE_ROLE = ROOT / "roles/vhosts/xconnect_one"
GATEWAY_PLAYBOOK = ROOT / "deploy_xconnect_gateway.yml"
ONE_PLAYBOOK = ROOT / "deploy_xconnect_one.yml"


class XConnectGatewayCloudflaredContractTest(unittest.TestCase):
    def test_role_paths_and_yaml_parse(self) -> None:
        self.assertTrue(CLOUDFLARED_ROLE.is_dir())
        self.assertTrue(CLOUDFLARED_ALIAS.is_symlink())
        self.assertEqual(CLOUDFLARED_ALIAS.resolve(), CLOUDFLARED_ROLE.resolve())

        yaml_files = [
            GATEWAY_PLAYBOOK,
            ONE_PLAYBOOK,
            CLOUDFLARED_ROLE / "defaults/main.yml",
            CLOUDFLARED_ROLE / "meta/main.yml",
            CLOUDFLARED_ROLE / "handlers/main.yml",
            CLOUDFLARED_ROLE / "tasks/main.yml",
            CLOUDFLARED_ROLE / "tasks/preflight.yml",
            ONE_ROLE / "defaults/main.yml",
            ONE_ROLE / "handlers/main.yml",
            ONE_ROLE / "tasks/main.yml",
            ONE_ROLE / "tasks/pg_forward.yml",
        ]
        for path in yaml_files:
            self.assertTrue(path.exists(), f"missing {path}")
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(parsed, f"empty YAML in {path}")

    def test_opt_in_defaults_and_playbook_gating(self) -> None:
        cf_defaults = yaml.safe_load((CLOUDFLARED_ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
        self.assertIs(cf_defaults["xconnect_gateway_cloudflared_enabled"], False)
        self.assertEqual(
            cf_defaults["xconnect_gateway_control_plane_url"],
            "https://accounts-selfhost-prod-v6.svc.plus",
        )
        self.assertEqual(
            cf_defaults["xconnect_gateway_cloudflared_service_name"],
            "cloudflared-xconnect",
        )

        one_defaults = yaml.safe_load((ONE_ROLE / "defaults/main.yml").read_text(encoding="utf-8"))
        self.assertIs(one_defaults["xconnect_one_pg_forward_enabled"], False)
        self.assertEqual(one_defaults["xconnect_one_pg_forward_interface"], "xconzero0")
        self.assertEqual(one_defaults["xconnect_one_pg_forward_bind_address"], "10.67.0.2")
        self.assertEqual(one_defaults["xconnect_one_pg_forward_listen_port"], 5432)
        self.assertEqual(one_defaults["xconnect_one_pg_forward_target_address"], "10.55.35.251")
        self.assertEqual(one_defaults["xconnect_one_pg_forward_target_port"], 5432)
        self.assertEqual(one_defaults["xconnect_one_pg_forward_allowed_source_cidr"], "10.67.0.1/32")

        gw_play = yaml.safe_load(GATEWAY_PLAYBOOK.read_text(encoding="utf-8"))
        reconcile_play = gw_play[1]
        cf_role_entry = next(
            r for r in reconcile_play["roles"]
            if isinstance(r, dict) and r.get("role") == "vhosts/xconnect_gateway_cloudflared"
        )
        self.assertIn("xconnect_gateway_cloudflared_enabled", cf_role_entry["when"])

        one_main_tasks = yaml.safe_load((ONE_ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
        pg_include = next(
            t for t in one_main_tasks
            if t.get("ansible.builtin.include_tasks") == "pg_forward.yml"
        )
        self.assertIn("xconnect_one_pg_forward_enabled", pg_include["when"])

        one_play = yaml.safe_load(ONE_PLAYBOOK.read_text(encoding="utf-8"))
        pg_play = next(
            p for p in one_play
            if "xconnect-one-pg-forward" in (p.get("tags") or [])
        )
        self.assertIn("never", pg_play["tags"])
        self.assertIn("xconnect_one_pg_forward_enabled", pg_play["tasks"][0]["when"])

    def test_vault_token_and_env_file_are_mode_0600_with_no_log(self) -> None:
        tasks = yaml.safe_load((CLOUDFLARED_ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
        block_tasks = tasks[0]["block"]
        by_name = {t.get("name"): t for t in block_tasks}

        vault_read = by_name["Read Cloudflare Tunnel token from Vault KV v2"]
        self.assertIn("community.hashi_vault.vault_kv2_get", vault_read)
        self.assertIs(vault_read["no_log"], True)

        for protected_name in (
            "Validate Vault KV v2 source for Cloudflare Tunnel token",
            "Resolve Cloudflare Tunnel token",
            "Require non-empty Cloudflare Tunnel token",
            "Write protected cloudflared tunnel token environment file",
        ):
            self.assertIs(by_name[protected_name]["no_log"], True, protected_name)

        env_task = by_name["Write protected cloudflared tunnel token environment file"]
        self.assertEqual(env_task["ansible.builtin.template"]["mode"], "0600")
        self.assertEqual(env_task["ansible.builtin.template"]["owner"], "root")
        self.assertEqual(env_task["ansible.builtin.template"]["group"], "root")

        unit_tpl = (CLOUDFLARED_ROLE / "templates/cloudflared-xconnect.service.j2").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile={{ xconnect_gateway_cloudflared_env_file }}", unit_tpl)
        self.assertNotIn("TUNNEL_TOKEN", unit_tpl)
        self.assertNotIn("--token ", unit_tpl)

    def test_readme_documents_wrangler_vpc_and_hyperdrive_commands(self) -> None:
        readme = (CLOUDFLARED_ROLE / "README.md").read_text(encoding="utf-8")
        self.assertIn("wrangler vpc service create", readme)
        self.assertIn("--tcp-port 5432", readme)
        self.assertIn("--app-protocol postgresql", readme)
        self.assertIn("--ipv4 10.67.0.2", readme)
        self.assertIn("wrangler hyperdrive create", readme)
        self.assertIn("accounts-selfhost-prod-v6.svc.plus", readme)

    def _run_local_playbook(self, tasks: list, extra_vars: dict | None = None) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            pb = Path(directory) / "test.yml"
            pb.write_text(
                yaml.safe_dump(
                    [
                        {
                            "hosts": "localhost",
                            "gather_facts": False,
                            "vars": extra_vars or {},
                            "tasks": tasks,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(pb)],
                cwd=ROOT,
                env=dict(os.environ, ANSIBLE_NOCOLOR="1"),
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_anti_deadlock_preflight_allows_go_origin_and_rejects_worker_routes(self) -> None:
        preflight_tasks = [
            {
                "ansible.builtin.include_role": {
                    "name": "vhosts/xconnect_gateway_cloudflared",
                    "tasks_from": "preflight",
                }
            },
        ]

        ok = self._run_local_playbook(
            preflight_tasks,
            {"xconnect_gateway_control_plane_url": "https://accounts-selfhost-prod-v6.svc.plus"},
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

        for bad_url in (
            "https://accounts.svc.plus",
            "https://accounts-worker.svc.workers.dev",
            "http://accounts-selfhost-prod-v6.svc.plus",
            "",
        ):
            fail = self._run_local_playbook(
                preflight_tasks,
                {"xconnect_gateway_control_plane_url": bad_url},
            )
            self.assertNotEqual(fail.returncode, 0, f"expected failure for {bad_url!r}")
            self.assertIn("Anti-deadlock guard triggered", fail.stdout + fail.stderr)

        # Also catch misconfigured xconnect_gateway_controller even if control_plane_url is default
        controller_fail = self._run_local_playbook(
            preflight_tasks,
            {"xconnect_gateway_controller": "https://accounts.svc.plus"},
        )
        self.assertNotEqual(controller_fail.returncode, 0)
        self.assertIn("Anti-deadlock guard triggered", controller_fail.stdout + controller_fail.stderr)

    def test_cloudflared_role_is_noop_when_disabled(self) -> None:
        res = self._run_local_playbook(
            [{"ansible.builtin.include_role": {"name": "vhosts/xconnect_gateway_cloudflared"}}]
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("changed=0", res.stdout)

    def test_pg_forward_templates_bind_only_private_ip_and_restrict_source_peer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            res = self._run_local_playbook(
                [
                    {"ansible.builtin.include_vars": str(ONE_ROLE / "defaults/main.yml")},
                    {
                        "ansible.builtin.template": {
                            "src": str(ONE_ROLE / "templates/xconnect-one-pg-forward.service.j2"),
                            "dest": str(out / "pg-forward.service"),
                        }
                    },
                    {
                        "ansible.builtin.template": {
                            "src": str(ONE_ROLE / "templates/pg-forward-firewall.sh.j2"),
                            "dest": str(out / "pg-forward-firewall.sh"),
                        }
                    },
                ]
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

            service = (out / "pg-forward.service").read_text(encoding="utf-8")
            self.assertIn("TCP-LISTEN:5432,bind=10.67.0.2,fork,reuseaddr,range=10.67.0.1/32", service)
            self.assertIn("TCP:10.55.35.251:5432", service)
            self.assertNotIn("0.0.0.0", service)

            fw = (out / "pg-forward-firewall.sh").read_text(encoding="utf-8")
            self.assertIn('iifname "xconzero0" ip saddr 10.67.0.1/32 ip daddr 10.67.0.2 tcp dport 5432 accept', fw)
            self.assertIn("ip daddr 10.67.0.2 tcp dport 5432 drop", fw)

    def test_pg_forward_rejects_wildcard_bind_and_source_cidr(self) -> None:
        pg_tasks = yaml.safe_load((ONE_ROLE / "tasks/pg_forward.yml").read_text(encoding="utf-8"))
        validate_task = pg_tasks[0]
        self.assertEqual(validate_task["name"], "Validate private PostgreSQL TCP forwarding inputs")

        for bad_vars in (
            {"xconnect_one_pg_forward_enabled": True, "xconnect_one_pg_forward_bind_address": "0.0.0.0"},
            {"xconnect_one_pg_forward_enabled": True, "xconnect_one_pg_forward_allowed_source_cidr": "0.0.0.0/0"},
            {"xconnect_one_pg_forward_enabled": True, "xconnect_one_pg_forward_allowed_source_cidr": "10.67.0.0/24"},
        ):
            res = self._run_local_playbook(
                [
                    {"ansible.builtin.include_vars": str(ONE_ROLE / "defaults/main.yml")},
                    {"ansible.builtin.set_fact": bad_vars},
                    validate_task,
                ],
            )
            self.assertNotEqual(res.returncode, 0, f"expected rejection for {bad_vars!r}")
            self.assertIn("xconnect_one_pg_forward must bind a non-wildcard", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
