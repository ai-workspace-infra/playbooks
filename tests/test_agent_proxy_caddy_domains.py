#!/usr/bin/env python3
"""Regression checks for Agent Proxy Caddy regional entrypoint domain configuration."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "deploy_xray_proxy_server.yml"
TEMPLATE = ROOT / "roles/vhosts/tky-proxy/templates/agent-proxy.conf.j2"


class AgentProxyCaddyDomainsTest(unittest.TestCase):
    def test_regional_domain_mapping_regex(self) -> None:
        pattern = r"^agent-proxy-selfhost-[^-]+-([a-z0-9]+)\.(.*)$"

        cases = {
            "agent-proxy-selfhost-prod-jp.svc.plus": "jp-xconnect.svc.plus",
            "agent-proxy-selfhost-prod-hk.svc.plus": "hk-xconnect.svc.plus",
            "agent-proxy-selfhost-prod-us.svc.plus": "us-xconnect.svc.plus",
            "agent-proxy-selfhost-uat-jp.onwalk.net": "jp-xconnect.onwalk.net",
            "agent-proxy-selfhost-uat-hk.onwalk.net": "hk-xconnect.onwalk.net",
            "agent-proxy-selfhost-uat-us.onwalk.net": "us-xconnect.onwalk.net",
        }

        for node_id, expected_entrypoint in cases.items():
            m = re.match(pattern, node_id)
            self.assertIsNotNone(m, f"regex should match {node_id}")
            derived = f"{m.group(1)}-xconnect.{m.group(2)}"
            self.assertEqual(derived, expected_entrypoint)

    def test_playbook_declares_regional_domain_in_primary_domains(self) -> None:
        content = PLAYBOOK.read_text()
        self.assertIn("agent_proxy_regional_domain:", content)
        self.assertIn("{{ agent_proxy_regional_domain }}", content)
        self.assertIn("AGENT_PROXY_REGIONAL_DOMAIN", content)

    def test_template_joins_domains_with_comma(self) -> None:
        content = TEMPLATE.read_text()
        first_line = content.splitlines()[0]
        self.assertIn("join(', ')", first_line)


if __name__ == "__main__":
    unittest.main()
