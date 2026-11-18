#!/usr/bin/env python3
"""Contract tests for the 8 decoupled XConnect edge roles and billing tags."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
VHOSTS = ROOT / "roles/vhosts"


class XConnectRoleDecompositionContractTest(unittest.TestCase):
    def test_eight_roles_exist_and_parse(self) -> None:
        expected_roles = [
            "xray-core",
            "caddy",
            "fluxgate",
            "haproxy",
            "xconnect_proxy",
            "xconnect_gateway",
            "xray-exporter",
            "xconnect-edge-agent",
        ]
        for role in expected_roles:
            main = VHOSTS / role / "tasks/main.yml"
            self.assertTrue(main.exists(), f"missing tasks/main.yml in {role}")
            self.assertIsInstance(yaml.safe_load(main.read_text()), list)

    def test_xray_core_is_zero_dependency_l0_foundation(self) -> None:
        meta = yaml.safe_load((VHOSTS / "xray-core/meta/main.yml").read_text())
        self.assertEqual(meta["dependencies"], [])
        tasks_text = (VHOSTS / "xray-core/tasks/main.yml").read_text()
        self.assertIn("geoip.dat", tasks_text)
        self.assertIn("geosite.dat", tasks_text)
        self.assertIn("Notify xray-core binary changed", tasks_text)

    def test_ingress_engines_export_contract_and_reload_handler(self) -> None:
        for engine in ("caddy", "fluxgate", "haproxy"):
            configure = (VHOSTS / engine / "tasks/configure.yml").read_text()
            handlers = (VHOSTS / engine / "handlers/main.yml").read_text()
            self.assertIn("xconnect_ingress_conf_d_dir", configure, engine)
            self.assertIn("xconnect_ingress_tls_dir", configure, engine)
            self.assertIn("listen: reload ingress", handlers, engine)

        fluxgate_defaults = yaml.safe_load((VHOSTS / "fluxgate/defaults/main.yml").read_text())
        self.assertFalse(fluxgate_defaults["fluxgate_vhost_enabled"])

    def test_xconnect_proxy_depends_on_xray_core_and_has_no_agent_install(self) -> None:
        meta = yaml.safe_load((VHOSTS / "xconnect_proxy/meta/main.yml").read_text())
        dep_roles = [d.get("role") if isinstance(d, dict) else d for d in meta["dependencies"]]
        self.assertIn("roles/vhosts/xray-core", dep_roles)

        deploy_text = (VHOSTS / "xconnect_proxy/tasks/deploy.yml").read_text()
        self.assertNotIn("install_xray.yml", deploy_text)
        self.assertNotIn("agent-base-config.yaml.j2", deploy_text)
        self.assertNotIn("/api/agent-server/v1/status", deploy_text)
        self.assertIn("service': 'xconnect-proxy'", deploy_text)
        self.assertIn("billing_scope': 'proxy'", deploy_text)
        self.assertIn("proxy-xhttp-in", deploy_text)
        self.assertIn("proxy-tcp-in", deploy_text)

    def test_xconnect_edge_agent_is_pure_control_plane_without_include_role(self) -> None:
        main_text = (VHOSTS / "xconnect-edge-agent/tasks/main.yml").read_text()
        deploy_text = (VHOSTS / "xconnect-edge-agent/tasks/deploy.yml").read_text()
        self.assertNotIn("include_role", main_text)
        self.assertNotIn("include_role", deploy_text)
        self.assertNotIn("install_xray.yml", deploy_text)
        self.assertIn("agent-base-config.yaml.j2", deploy_text)
        self.assertIn("xconnect_edge_agent_profiles", deploy_text)

    def test_xconnect_gateway_enforces_loopback_only_wireguard_firewall(self) -> None:
        main_text = (VHOSTS / "xconnect_gateway/tasks/main.yml").read_text()
        self.assertIn('iifname "lo" udp dport', main_text)
        self.assertIn("service': 'xconnect-gateway'", main_text)
        self.assertIn("billing_scope': 'zero'", main_text)
        self.assertIn("xconnect-vless-in", main_text)

        frontend_text = (VHOSTS / "xconnect_gateway/tasks/frontend.yml").read_text()
        self.assertIn("xconnect-gateway-vhost.conf.j2", frontend_text)
        self.assertNotIn("agent-proxy.conf.j2", frontend_text)

    def test_render_xray_inbound_tags_and_exporter_const_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            playbook = out / "render.yml"
            playbook.write_text(
                yaml.safe_dump(
                    [
                        {
                            "hosts": "localhost",
                            "gather_facts": False,
                            "vars": {
                                "xray_exporter_const_labels_enabled": True,
                                "xconnect_region": "PH",
                                "xray_exporter_env_name": "prod",
                            },
                            "tasks": [
                                {"ansible.builtin.include_vars": str(VHOSTS / "xconnect_proxy/defaults/main.yml")},
                                {"ansible.builtin.include_vars": str(VHOSTS / "xray-exporter/defaults/main.yml")},
                                {
                                    "ansible.builtin.set_fact": {
                                        "xray_exporter_const_labels_enabled": True,
                                        "xconnect_region": "PH",
                                        "xray_exporter_env_name": "prod",
                                    }
                                },
                                {
                                    "ansible.builtin.template": {
                                        "src": str(VHOSTS / "xconnect_proxy/templates/xray.xhttp.template.json.j2"),
                                        "dest": str(out / "xhttp.json"),
                                    }
                                },
                                {
                                    "ansible.builtin.template": {
                                        "src": str(VHOSTS / "xconnect_proxy/templates/xray.tcp.template.json.j2"),
                                        "dest": str(out / "tcp.json"),
                                    }
                                },
                                {
                                    "ansible.builtin.template": {
                                        "src": str(VHOSTS / "xray-exporter/templates/xray-exporter.service.j2"),
                                        "dest": str(out / "exporter.service"),
                                    },
                                    "loop": "{{ xray_exporter_instances[:1] }}",
                                    "loop_control": {"loop_var": "xray_exporter_instance"},
                                },
                            ],
                        }
                    ]
                )
            )
            subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
                check=True,
                capture_output=True,
                text=True,
            )

            xhttp = json.loads((out / "xhttp.json").read_text())
            tcp = json.loads((out / "tcp.json").read_text())
            self.assertEqual([i.get("tag") for i in xhttp["inbounds"]], ["api", "proxy-xhttp-in"])
            self.assertEqual([i.get("tag") for i in tcp["inbounds"]], ["api", "proxy-tcp-in"])

            service_unit = (out / "exporter.service").read_text()
            self.assertIn("--const-label billing_scope=proxy", service_unit)
            self.assertIn("--const-label service=xconnect-proxy", service_unit)
            self.assertIn("--const-label inbound_tag=proxy-xhttp-in", service_unit)
            self.assertIn("--const-label region=PH", service_unit)


if __name__ == "__main__":
    unittest.main()
