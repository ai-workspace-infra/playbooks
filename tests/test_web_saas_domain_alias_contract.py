from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOST_VARS = (ROOT / "host_vars/web-saas-prod/web_saas.yml").read_text()
DEFAULTS = (ROOT / "roles/vhosts/web_saas_host_config/defaults/main.yml").read_text()
CADDY = (ROOT / "roles/vhosts/web_saas_host_config/templates/Caddyfile.j2").read_text()


class WebSaasDomainAliasContractTest(unittest.TestCase):
    def test_prod_host_declares_selfhost_and_canonical_identities(self):
        for domain in (
            "console-selfhost-prod.svc.plus",
            "console.svc.plus",
            "accounts-selfhost-prod.svc.plus",
            "accounts.svc.plus",
            "billing-selfhost-prod.svc.plus",
            "billing.svc.plus",
            "postgresql-selfhost-prod.svc.plus",
            "postgresql.svc.plus",
            "xworktech.com",
        ):
            self.assertIn(domain, HOST_VARS)

    def test_caddy_accepts_canonical_http_aliases(self):
        self.assertIn(
            "@accounts host {{ web_saas_host_config_accounts_domain }} {{ web_saas_host_config_accounts_public_domain }}",
            CADDY,
        )
        self.assertIn("web_saas_host_config_billing_public_domain", CADDY)
        self.assertIn("web_saas_host_config_brand_domain", CADDY)
        self.assertIn("reverse_proxy console:3000", CADDY)

    def test_postgresql_is_not_added_to_http_caddy(self):
        self.assertIn("postgresql.svc.plus", HOST_VARS)
        self.assertNotIn("postgresql:5432", CADDY)
        self.assertNotIn("postgresql-selfhost-prod.svc.plus {", CADDY)

    def test_brand_domain_is_opt_in_and_origin_allowlist_supports_aliases(self):
        self.assertIn("web_saas_host_config_brand_domain", DEFAULTS)
        self.assertIn("ACP_ALLOWED_ORIGINS", DEFAULTS)
        self.assertIn("web_saas_host_config_console_public_domain", DEFAULTS)


if __name__ == "__main__":
    unittest.main()
