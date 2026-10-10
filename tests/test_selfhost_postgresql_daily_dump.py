"""Offline safety contract checks for the production daily dump role."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/selfhost_postgresql_daily_dump"


class DailyPostgreSQLDumpTests(unittest.TestCase):
    def test_prod_caller_enables_only_for_prod(self) -> None:
        caller = (ROOT / "setup-web-saas-domain.yml").read_text()
        host_vars = (ROOT / "host_vars/web-saas-prod/web_saas.yml").read_text()
        self.assertIn("roles/selfhost_postgresql_daily_dump", caller)
        self.assertIn("when: web_saas_deployment_environment == 'prod'", caller)
        self.assertIn("selfhost_pg_daily_dump_enabled: true", host_vars)

    def test_role_gates_on_persistent_mount_healthy_db_and_active_cron(self) -> None:
        tasks = (ROLE / "tasks/main.yml").read_text()
        self.assertIn("selfhost_pg_daily_dump_mount.stdout | trim == selfhost_pg_daily_dump_expected_mountpoint", tasks)
        self.assertIn("selfhost_pg_daily_dump_container_state.stdout | trim == 'running healthy'", tasks)
        self.assertIn("selfhost_pg_daily_dump_cron_service.stdout | trim == 'active'", tasks)

    def test_schedule_is_daily_at_two_shanghai_time_and_retains_seven_days(self) -> None:
        defaults = (ROLE / "defaults/main.yml").read_text()
        cron = (ROLE / "templates/cron.j2").read_text()
        runner = (ROLE / "templates/run_daily_dump.sh.j2").read_text()
        self.assertIn("selfhost_pg_daily_dump_schedule: '0 2 * * *'", defaults)
        self.assertIn("selfhost_pg_daily_dump_timezone: Asia/Shanghai", defaults)
        self.assertIn("selfhost_pg_daily_dump_retention_days: 7", defaults)
        self.assertIn("CRON_TZ={{ selfhost_pg_daily_dump_timezone }}", cron)
        self.assertIn('date -d "$((retention_days - 1)) days ago"', runner)

    def test_runner_is_full_dump_only_and_scopes_pruning_to_named_artifacts(self) -> None:
        runner = (ROLE / "templates/run_daily_dump.sh.j2").read_text()
        self.assertIn('pg_dumpall | gzip -1', runner)
        self.assertIn('gzip -t "$tmp"', runner)
        self.assertIn('sha256sum --check --status', runner)
        self.assertIn('web-saas-prod-pg_dumpall-????-??-??.sql.gz', runner)
        self.assertIn('rm -f -- "$candidate" "$candidate.sha256"', runner)
        self.assertNotRegex(runner, re.compile(r'\b(pg_restore|pg_basebackup|pg_combinebackup)\b'))
        self.assertNotIn("--password", runner)


if __name__ == "__main__":
    unittest.main()
