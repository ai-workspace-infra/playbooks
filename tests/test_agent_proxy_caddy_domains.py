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

    def test_prod_compatibility_hostname_is_environment_gated(self) -> None:
        content = PLAYBOOK.read_text()
        self.assertIn("agent_proxy_environment:", content)
        self.assertIn("agent_proxy_environment == 'prod'", content)
        self.assertIn("xconnect_pool | default('') == 'jp'", content)

    def test_caddy_fragments_are_reconciled_and_use_shared_conf_dir(self) -> None:
        tasks = (ROOT / "roles/vhosts/tky-proxy/tasks/main.yml").read_text()
        template = (ROOT / "roles/vhosts/tky-proxy/templates/agent-proxy.Caddyfile.j2").read_text()
        self.assertIn("agent_proxy_caddy_conf_dir", tasks)
        self.assertIn("force: true", tasks)
        self.assertIn("Remove PROD-only compatibility fragment outside PROD", tasks)
        self.assertIn("import {{ agent_proxy_caddy_conf_dir }}/*.caddy", template)

    def test_shared_xconnect_route_is_separate_from_agent_proxy_socket(self) -> None:
        content = TEMPLATE.read_text()
        self.assertIn("xconnect_gateway_caddy_enabled", content)
        self.assertIn("xconnect_gateway_caddy_path", content)
        self.assertIn("xconnect_gateway_caddy_socket", content)
        self.assertIn("path /split /split/*", content)
        self.assertIn("path {{ xconnect_gateway_caddy_path }} {{ xconnect_gateway_caddy_path }}/*", content)
        self.assertIn("unix//{{ xconnect_gateway_caddy_socket", content)
        self.assertIn("unix//dev/shm/xray.sock", content)


if __name__ == "__main__":
    unittest.main()
