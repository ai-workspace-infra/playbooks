#!/usr/bin/env python3
"""Contract and regression tests for Fluxgate vhosts auto-merge and robust pull convergence."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FluxgateVhostsContractTest(unittest.TestCase):
    def test_fluxgate_defaults_merge_domains_and_agent_id(self) -> None:
        content = (ROOT / "roles/vhosts/fluxgate/defaults/main.yml").read_text()
        self.assertIn("fluxgate_legacy_vhosts:", content)
        self.assertIn("agent_proxy_domains", content)
        self.assertIn("agent_id", content)
        self.assertIn("agent_entry_point", content)
        self.assertIn("unique | list", content)

    def test_fluxgate_configure_resolves_vhosts_list(self) -> None:
        tasks = (ROOT / "roles/vhosts/fluxgate/tasks/configure.yml").read_text()
        template = (ROOT / "roles/vhosts/fluxgate/templates/xconnect.conf.j2").read_text()
        self.assertIn("fluxgate_resolved_vhosts", tasks)
        self.assertIn("fluxgate_resolved_vhosts", template)

    def test_haproxy_defaults_merge_domains_and_agent_id(self) -> None:
        content = (ROOT / "roles/vhosts/HAProxy/defaults/main.yml").read_text()
        self.assertIn("haproxy_legacy_vhosts:", content)
        self.assertIn("agent_proxy_domains", content)
        self.assertIn("agent_id", content)
        self.assertIn("agent_entry_point", content)
        self.assertIn("unique | list", content)

    def test_deploy_playbook_passes_effective_vhosts(self) -> None:
        content = (ROOT / "deploy_xray_proxy_server.yml").read_text()
        self.assertIn("agent_proxy_effective_vhosts:", content)
        self.assertIn("fluxgate_vhosts: \"{{ agent_proxy_effective_vhosts }}\"", content)
        self.assertIn("haproxy_vhosts: \"{{ agent_proxy_effective_vhosts }}\"", content)
        self.assertIn("Disable legacy or conflicting flux-limits pull timer on bare-metal node", content)

    def test_pull_gitops_declaration_ensures_directory_exists(self) -> None:
        content = (ROOT / "pull_gitops_declaration.yml").read_text()
        self.assertIn("Ensure the ansible-pull configuration directory exists", content)
        self.assertIn("path: \"{{ gitops_declaration_env_file | dirname }}\"", content)

    def test_flux_controller_limits_guards_kubectl(self) -> None:
        content = (ROOT / "roles/vhosts/flux_controllers/tasks/limits.yml").read_text()
        self.assertIn("Check if kubectl command exists", content)
        self.assertIn("Check if k3s kubeconfig exists", content)
        self.assertIn("Skipping Flux controller limits convergence", content)

    def test_vector_agent_adapts_to_local_snapshot_and_secrets(self) -> None:
        content = (ROOT / "roles/vhosts/vector-agent/tasks/main.yml").read_text()
        self.assertIn("Check existing xray-exporter configuration for billing snapshot integration", content)
        self.assertIn("vector_billing_snapshot_configured", content)
        self.assertIn("existing_xray_exporter_service_token", content)
        self.assertIn("vector_billing_ingest_enabled", content)


if __name__ == "__main__":
    unittest.main()
