#!/usr/bin/env python3
"""Regression checks preventing ansible-pull (-i localhost,) from resetting hostnames to localhost."""

import importlib.machinery
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AnsiblePullHostnameGuardTest(unittest.TestCase):
    def test_common_does_not_overwrite_hostname_when_inventory_is_localhost(self) -> None:
        tasks = (ROOT / "roles/vhosts/common/tasks/main.yml").read_text(encoding="utf-8")
        self.assertIn("common_resolved_hostname", tasks)
        self.assertIn("enable_set_hostname | default(true) | bool", tasks)
        self.assertIn("common_resolved_hostname not in ['localhost', 'localhost.localdomain', '127.0.0.1', '::1']", tasks)

    def test_pull_observability_agent_resolves_canonical_hostname_for_common_and_vector(self) -> None:
        pb = (ROOT / "pull_observability_agent.yml").read_text(encoding="utf-8")
        self.assertIn("Resolve real node hostname for local ansible-pull convergence", pb)
        self.assertIn("common_target_hostname:", pb)
        self.assertIn("vector_observability_host:", pb)
        self.assertIn("vector_observability_node_identity:", pb)

    def test_vector_agent_domain_env_supports_split_conf_d(self) -> None:
        script_path = ROOT / "roles/vhosts/vector-agent/files/vector-agent-domain-env"
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_dir = Path(tmpdir)
            conf_d = agent_dir / "conf.d"
            conf_d.mkdir()
            (agent_dir / "account-agent.yaml").write_text("mode: agent\nagent:\n  region: PH\n", encoding="utf-8")
            (conf_d / "01-agent-proxy.yaml").write_text(
                "agent:\n  id: \"ph-xconnect.svc.plus\"\n  role: \"agent-proxy\"\n", encoding="utf-8"
            )
            (conf_d / "02-ph-xconnect-gateway.yaml").write_text(
                "agent:\n  id: \"ph-xconnect-gateway-01\"\n  role: \"gateway\"\n", encoding="utf-8"
            )
            res = subprocess.run(
                ["python3", str(script_path), "--print", str(agent_dir / "account-agent.yaml")],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.stdout.strip(), "ph-xconnect.svc.plus")


if __name__ == "__main__":
    unittest.main()
