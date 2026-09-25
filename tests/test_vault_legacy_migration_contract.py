import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE_DIR = ROOT / "roles/vhosts/vault_legacy_migration"
ENTRY = ROOT / "deploy_vault_legacy_migration.yml"


def load_tasks(name):
    return yaml.safe_load((ROLE_DIR / "tasks" / name).read_text(encoding="utf-8"))


def load_text(name):
    return (ROLE_DIR / "tasks" / name).read_text(encoding="utf-8")


class VaultLegacyMigrationContractTest(unittest.TestCase):
    def test_all_task_files_and_the_entry_playbook_parse(self):
        for name in ("main.yml", "convert.yml", "rollback.yml", "retire.yml"):
            self.assertTrue(load_tasks(name), f"{name} did not parse to a non-empty task list")
        self.assertTrue(yaml.safe_load(ENTRY.read_text(encoding="utf-8")))

    def test_action_dispatch_covers_exactly_three_actions(self):
        main = load_text("main.yml")
        self.assertIn("in ['convert', 'rollback', 'retire']", main)
        for action, task_file in (("convert", "convert.yml"), ("rollback", "rollback.yml"), ("retire", "retire.yml")):
            self.assertIn(f"vault_legacy_migration_action == '{action}'", main)
            self.assertIn(task_file, main)

    def test_every_destructive_action_requires_its_exact_confirm_phrase(self):
        for task_file, phrase in (
            ("convert.yml", "CONVERT-VAULT-TO-RAFT"),
            ("rollback.yml", "ROLLBACK-VAULT-TO-POSTGRESQL"),
            ("retire.yml", "REMOVE-LEGACY-VAULT-PEER"),
        ):
            source = load_text(task_file)
            self.assertIn(f"vault_legacy_migration_confirm == '{phrase}'", source)
            # The confirm assertion must be the first task, before any change.
            self.assertLess(source.index(phrase), source.index("systemd") if "systemd" in source else len(source))

    def test_convert_reads_postgresql_only_over_loopback_and_never_restarts_it(self):
        source = load_text("convert.yml")
        self.assertIn("127.0.0.1", source)
        self.assertNotIn("postgresql.service", source)
        self.assertNotIn("state: restarted", source)
        # No swap of `sslmode` or remote host; the connection string is pinned.
        self.assertIn("@127.0.0.1:", source)

    def test_convert_never_touches_vault_init_json_or_unseal(self):
        source = load_text("convert.yml")
        self.assertNotIn("vault_init.json", source)
        self.assertNotIn("operator init", source)
        self.assertNotIn("operator unseal", source)

    def test_convert_refuses_to_overwrite_existing_raft_data(self):
        source = load_text("convert.yml")
        self.assertIn("vault_legacy_migration_data_dir_listing.files | length == 0", source)

    def test_convert_requires_a_private_overlay_address(self):
        source = load_text("convert.yml")
        self.assertIn(r"'^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)'", source)

    def test_convert_delegates_the_actual_migrate_call_to_the_reviewed_operator_tool(self):
        source = load_text("convert.yml")
        self.assertIn("vault_legacy_migration_operator_data_script", source)
        self.assertIn("migrate-offline", source)
        self.assertIn("MIGRATE-POSTGRESQL-TO-RAFT", source)
        # Rescue path restores service state without regenerating any key.
        self.assertIn("rescue:", source)
        self.assertIn("state: started", source)

    def test_convert_is_idempotent_via_a_live_backend_check_not_a_marker_file(self):
        source = load_text("convert.yml")
        self.assertIn("end_host", source)
        self.assertIn("vault_legacy_migration_current_backend == 'raft'", source)

    def test_convert_installs_the_port_guard(self):
        source = load_text("convert.yml")
        self.assertIn("vhosts/vault_port_guard", source)
        self.assertIn("vault_port_guard_enabled: true", source)

    def test_rollback_restores_from_the_backup_convert_wrote(self):
        source = load_text("rollback.yml")
        self.assertIn("vault.hcl.postgresql-", source)
        self.assertIn("vault_legacy_migration_backups.files | length > 0", source)

    def test_retire_requires_raft_backend_and_only_stops_the_service(self):
        source = load_text("retire.yml")
        self.assertIn('storage "raft"', source)
        self.assertIn("enabled: false", source)
        self.assertNotIn("state: absent", source)  # data directory is preserved

    def test_entry_playbook_targets_exactly_one_host_and_chains_to_single_raft(self):
        source = ENTRY.read_text(encoding="utf-8")
        self.assertIn("length == 1", source)
        self.assertIn("import_playbook: deploy_vault_single_raft.yml", source)
        for tag in ("vault-legacy-convert", "vault-legacy-rollback", "vault-legacy-retire"):
            self.assertIn(f"tags: [{tag}]", source)


if __name__ == "__main__":
    unittest.main()
