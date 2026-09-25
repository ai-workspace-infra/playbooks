import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE_DIR = ROOT / "roles/vhosts/vault_gateway_frontend"
ENTRYPOINT = (ROOT / "deploy_vault_shared_services.yml").read_text(encoding="utf-8")
GATEWAY_DEFAULTS = (ROOT / "roles/vhosts/xconnect_gateway/defaults/main.yml").read_text(encoding="utf-8")


class VaultGatewayFrontendContractTest(unittest.TestCase):
    def setUp(self):
        self.defaults = yaml.safe_load((ROLE_DIR / "defaults/main.yml").read_text(encoding="utf-8"))
        self.tasks_text = (ROLE_DIR / "tasks/main.yml").read_text(encoding="utf-8")
        self.site = (ROLE_DIR / "templates/vault-gateway.caddy.j2").read_text(encoding="utf-8")

    def test_task_file_parses(self):
        self.assertTrue(yaml.safe_load(self.tasks_text))

    def test_gateway_has_its_own_hostname_not_the_vault_service_name(self):
        self.assertEqual(self.defaults["vault_gateway_frontend_domain"], "vault-xconnect.svc.plus")
        self.assertIn("vault_gateway_frontend_domain != 'vault.svc.plus'", self.tasks_text)

    def test_socket_matches_the_gateway_role(self):
        self.assertIn(
            f"xconnect_gateway_listen_socket: {self.defaults['vault_gateway_frontend_socket']}",
            GATEWAY_DEFAULTS,
        )

    def test_only_the_xhttp_path_is_proxied(self):
        self.assertEqual(self.site.count("reverse_proxy"), 1)
        self.assertIn("reverse_proxy unix//", self.site)
        self.assertIn("versions h2c 2", self.site)
        self.assertIn("respond 404", self.site)
        for port in ("8200", "8201"):
            self.assertNotIn(port, self.site)

    def test_tls_comes_from_vault_material_and_never_acme(self):
        self.assertIn("tls {{ vault_gateway_frontend_tls_dir }}/fullchain.pem", self.site)
        self.assertIn("VAULT_GATEWAY_TLS_FULLCHAIN_B64", (ROLE_DIR / "defaults/main.yml").read_text(encoding="utf-8"))
        self.assertIn("never falls back to ACME", self.tasks_text)
        # Secrets are never logged.
        self.assertGreaterEqual(self.tasks_text.count("no_log: true"), 2)

    def test_certificate_is_checked_and_config_validated_before_reload(self):
        self.assertIn("-checkend 86400", self.tasks_text)
        self.assertIn("-checkhost", self.tasks_text)
        self.assertIn("caddy validate", self.tasks_text)
        self.assertLess(self.tasks_text.index("caddy validate"), self.tasks_text.index("flush_handlers"))

    def test_conf_d_exists_before_caddy_starts(self):
        self.assertLess(self.tasks_text.index("conf.d"), self.tasks_text.index("name: vhosts/caddy"))

    def test_entrypoint_runs_the_frontend_before_the_gateway(self):
        self.assertIn("tags: [vault-gateway-frontend]", ENTRYPOINT)
        self.assertLess(
            ENTRYPOINT.index("vhosts/vault_gateway_frontend"),
            ENTRYPOINT.index("import_playbook: deploy_xconnect_gateway.yml"),
        )


if __name__ == "__main__":
    unittest.main()
