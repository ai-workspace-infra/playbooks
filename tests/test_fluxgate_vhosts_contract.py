#!/usr/bin/env python3
"""Contract and regression tests for Fluxgate vhosts auto-merge, unified ansible-pull, and robust pull convergence."""

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
        self.assertIn('fluxgate_version: "0.2.1"', content)
        self.assertIn("fluxgate_h2_stream_window_size: 4194304", content)
        self.assertIn("fluxgate_h2_connection_window_size: 16777216", content)
        self.assertIn("fluxgate_h2_ping_interval_secs: 15", content)
        self.assertIn("fluxgate_max_h2_streams: 256", content)
        self.assertIn("fluxgate_read_timeout_secs: 3600", content)
        self.assertIn("fluxgate_idle_timeout_secs: 3600", content)
        self.assertIn('fluxgate_release_repo: "ai-workspace-xstream/fluxgate"', content)
        self.assertIn("fluxgate_release_checksums:", content)
        self.assertIn("fluxgate_download_url:", content)

    def test_fluxgate_install_and_service_are_idempotent(self) -> None:
        install_tasks = (ROOT / "roles/vhosts/fluxgate/tasks/install.yml").read_text()
        self.assertIn("fluxgate_needs_download", install_tasks)
        self.assertIn("fluxgate_resolved_checksum", install_tasks)
        self.assertIn("--version", install_tasks)

        service_tasks = (ROOT / "roles/vhosts/fluxgate/tasks/service.yml").read_text()
        self.assertIn("ansible.builtin.service_facts", service_tasks)
        self.assertIn("ansible_facts.services", service_tasks)

    def test_fluxgate_configure_resolves_vhosts_list(self) -> None:
        tasks = (ROOT / "roles/vhosts/fluxgate/tasks/vhost.yml").read_text()
        template = (ROOT / "roles/vhosts/fluxgate/templates/xconnect.conf.j2").read_text()
        self.assertIn("fluxgate_resolved_vhosts", tasks)
        self.assertIn("Ensure target Fluxgate config is not immutable", tasks)
        self.assertIn("fluxgate_resolved_vhosts", template)

    def test_haproxy_defaults_merge_domains_and_agent_id(self) -> None:
        content = (ROOT / "roles/vhosts/haproxy/defaults/main.yml").read_text()
        self.assertIn("haproxy_legacy_vhosts:", content)
        self.assertIn("agent_proxy_domains", content)
        self.assertIn("agent_id", content)
        self.assertIn("agent_entry_point", content)
        self.assertIn("unique | list", content)
        self.assertIn("haproxy_xhttp_send_proxy_v2: false", content)
        self.assertIn("haproxy_gateway_enabled:", content)

    def test_caddy_defaults_and_vhost_align_with_fluxgate(self) -> None:
        defaults = (ROOT / "roles/vhosts/caddy/defaults/main.yml").read_text()
        self.assertIn("caddy_vhost_enabled: false", defaults)
        self.assertIn("caddy_legacy_vhosts:", defaults)
        self.assertIn("agent_proxy_domains", defaults)
        self.assertIn("agent_id", defaults)
        self.assertIn("agent_entry_point", defaults)
        self.assertIn("unique | list", defaults)

        vhost_tasks = (ROOT / "roles/vhosts/caddy/tasks/vhost.yml").read_text()
        vhost_tpl = (ROOT / "roles/vhosts/caddy/templates/xconnect.conf.j2").read_text()
        self.assertIn("caddy_resolved_vhosts", vhost_tasks)
        self.assertIn("Ensure target Caddy vhost config is not immutable", vhost_tasks)
        self.assertIn("caddy_resolved_vhosts", vhost_tpl)

    def test_deploy_playbook_passes_effective_vhosts(self) -> None:
        content = (ROOT / "deploy_xray_proxy_server.yml").read_text()
        self.assertIn("agent_proxy_effective_vhosts:", content)
        self.assertIn("caddy_vhosts: \"{{ agent_proxy_effective_vhosts }}\"", content)
        self.assertIn("fluxgate_vhosts: \"{{ agent_proxy_effective_vhosts }}\"", content)
        self.assertIn("haproxy_vhosts: \"{{ agent_proxy_effective_vhosts }}\"", content)
        self.assertIn("Disable legacy or conflicting separate pull timers on bare-metal node", content)

    def test_pull_gitops_declaration_ensures_directory_exists(self) -> None:
        content = (ROOT / "pull_gitops_declaration.yml").read_text()
        self.assertIn("Ensure the ansible-pull configuration directory exists", content)
        self.assertIn("path: \"{{ gitops_declaration_env_file | dirname }}\"", content)

    def test_flux_controller_limits_guards_kubectl(self) -> None:
        limits_task = (ROOT / "roles/vhosts/flux_controllers/tasks/limits.yml").read_text()
        self.assertIn("Check if kubectl command exists", limits_task)
        self.assertIn("Check if k3s kubeconfig exists", limits_task)
        self.assertIn("Skipping Flux controller limits convergence", limits_task)

        limits_playbook = (ROOT / "pull_flux_controller_limits.yml").read_text()
        self.assertIn("container_mode", limits_playbook)
        self.assertIn("Skipping Flux controller limits convergence: container_mode is set to false via -e", limits_playbook)

    def test_vector_agent_adapts_to_local_snapshot_and_secrets(self) -> None:
        content = (ROOT / "roles/vhosts/vector-agent/tasks/main.yml").read_text()
        self.assertIn("Check existing xray-exporter configuration for billing snapshot integration", content)
        self.assertIn("vector_billing_snapshot_configured", content)
        self.assertIn("existing_xray_exporter_service_token", content)
        self.assertIn("vector_billing_ingest_enabled", content)

    def test_unified_ansible_pull_convergence_entrypoint(self) -> None:
        pull_conv = (ROOT / "pull_convergence.yml").read_text()
        self.assertIn("pull_gitops_declaration.yml", pull_conv)
        self.assertIn("pull_flux_controller_limits.yml", pull_conv)
        self.assertIn("pull_observability_agent.yml", pull_conv)

        timer_defaults = (ROOT / "roles/vhosts/ansible_pull_timer/defaults/main.yml").read_text()
        self.assertIn("ansible_pull_timer_name: ansible-pull", timer_defaults)
        self.assertIn("ansible_pull_playbook: pull_convergence.yml", timer_defaults)

        timer_tasks = (ROOT / "roles/vhosts/ansible_pull_timer/tasks/main.yml").read_text()
        self.assertIn("Stop, disable, and clean up legacy separate ansible-pull units", timer_tasks)
        self.assertIn("ansible-pull-flux-limits.timer", timer_tasks)
        self.assertIn("ansible-pull-observability.timer", timer_tasks)


if __name__ == "__main__":
    unittest.main()
