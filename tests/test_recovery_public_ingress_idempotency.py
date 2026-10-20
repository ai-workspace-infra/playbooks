"""Prevent recovery prepare from downgrading explicitly activated Billing ingress."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class RecoveryPublicIngressIdempotencyTests(unittest.TestCase):
    def test_public_ingress_is_opt_in_and_records_only_explicit_activation(self):
        defaults = yaml.safe_load((ROOT / "roles/web_saas_public_ingress/defaults/main.yml").read_text())
        tasks = yaml.safe_load((ROOT / "roles/web_saas_public_ingress/tasks/main.yml").read_text())
        self.assertIs(defaults["web_saas_public_ingress_activate"], False)
        activation = next(task for task in tasks if "Record that public ingress" in task.get("name", ""))
        self.assertIn("web_saas_public_ingress_activate | bool", activation["when"])
        self.assertEqual(
            activation["ansible.builtin.copy"]["dest"],
            "{{ web_saas_public_ingress_state_file }}",
        )
        self.assertEqual(defaults["web_saas_public_ingress_state_file"],
                         "/etc/xcontrol/web-saas-recovery/public-ingress-active.yml")

    def test_recovery_preserves_only_a_matching_activated_public_configuration(self):
        tasks = yaml.safe_load((ROOT / "roles/web_saas_generation_recovery/tasks/prepare.yml").read_text())
        state_check = next(task for task in tasks if "Check whether public Billing ingress" in task.get("name", ""))
        identity_check = next(task for task in tasks if "Require the activation record" in task.get("name", ""))
        config_check = next(task for task in tasks if "Refuse to overwrite an activated public-ingress" in task.get("name", ""))
        render = next(task for task in tasks if "Render probe-only Caddy configuration" in task.get("name", ""))
        self.assertEqual(state_check["ansible.builtin.stat"]["path"],
                         "/etc/xcontrol/web-saas-recovery/public-ingress-active.yml")
        self.assertIn("recovery.target.generation", identity_check["ansible.builtin.assert"]["that"][0])
        self.assertIn("inventory_hostname", identity_check["ansible.builtin.assert"]["that"][1])
        self.assertTrue(any("managed shared PROD public SNI" in item for item in config_check["ansible.builtin.assert"]["that"]))
        self.assertEqual(render["when"], "not recovery_public_ingress_active | bool")


if __name__ == "__main__":
    unittest.main()
