"""Unit and integration tests for OBS-05 Observability data-closure acceptance probe."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/verify_observability_closure.py"

spec = importlib.util.spec_from_file_location("verify_observability_closure", SCRIPT_PATH)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


def _build_valid_17_dashboards_payload() -> dict[str, dict]:
    payload: dict[str, dict] = {}
    for uid, meta in probe.EXPECTED_17_DASHBOARDS.items():
        expr = (
            'SELECT count(*) FROM cmdb_resources WHERE environment="$environment"'
            if meta["signal"] == "cmdb"
            else f'sum(up{{job="{uid}",environment="$environment"}})'
        )
        payload[uid] = {
            "dashboard": {
                "uid": uid,
                "title": meta["title"],
                "templating": {
                    "list": [
                        {
                            "name": "environment",
                            "type": "query",
                            "query": "label_values(up, environment)",
                        }
                    ]
                },
                "panels": [
                    {
                        "id": 1,
                        "title": f"{meta['title']} Panel 1",
                        "datasource": {"uid": meta["datasource_uid"]},
                        "targets": [{"refId": "A", "expr": expr}],
                    }
                ],
            },
            "meta": {"folderUid": ""},
        }
    return payload


def _build_valid_freshness_observations(ref_ts: float) -> dict[str, dict]:
    obs: dict[str, dict] = {}
    for uid, meta in probe.EXPECTED_17_DASHBOARDS.items():
        if meta.get("on_demand"):
            obs[uid] = {"latest_timestamp_sec": None, "idle_declared": True}
        elif meta["signal"] == "metrics":
            obs[uid] = {"latest_timestamp_sec": ref_ts - 60}
        elif meta["signal"] == "logs":
            obs[uid] = {"latest_timestamp_sec": ref_ts - 120}
        elif meta["signal"] == "cmdb":
            obs[uid] = {"latest_timestamp_sec": ref_ts - 600}
        else:
            obs[uid] = {"latest_timestamp_sec": ref_ts - 90}
    return obs


class ObservabilityClosureUnitTests(unittest.TestCase):
    def test_17_dashboard_baseline_inventory_is_complete(self):
        self.assertEqual(len(probe.EXPECTED_17_DASHBOARDS), 17)
        imported = [
            uid
            for uid, m in probe.EXPECTED_17_DASHBOARDS.items()
            if m["origin"] == "imported-16"
        ]
        retained = [
            uid
            for uid, m in probe.EXPECTED_17_DASHBOARDS.items()
            if m["origin"] == "retained-k3s-overview"
        ]
        self.assertEqual(len(imported), 16)
        self.assertEqual(retained, ["web-saas-k3s-v5-overview"])
        self.assertEqual(
            set(probe.REQUIRED_DATASOURCE_UIDS),
            {"victoriametrics", "victorialogs", "victoriatraces", "cmdb_postgres"},
        )

    def test_fixed_freshness_thresholds_enforce_metrics_logs_and_cmdb_bounds(self):
        ref_ts = 1_700_000_000.0
        # Metrics <= 180s
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 180, ref_ts, 180)["status"], "PASS"
        )
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 181, ref_ts, 180)["status"], "FAIL"
        )
        # Logs <= 300s
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 300, ref_ts, 300)["status"], "PASS"
        )
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 301, ref_ts, 300)["status"], "FAIL"
        )
        # CMDB <= 3600s
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 3600, ref_ts, 3600)["status"], "PASS"
        )
        self.assertEqual(
            probe.evaluate_freshness(ref_ts - 3601, ref_ts, 3600)["status"], "FAIL"
        )
        # Missing sample fails unless on-demand workload is explicitly idle
        self.assertEqual(
            probe.evaluate_freshness(None, ref_ts, 180)["status"], "FAIL"
        )
        self.assertEqual(
            probe.evaluate_freshness(
                None, ref_ts, 180, on_demand=True, idle_declared=True
            )["status"],
            "N/A",
        )

    def test_anti_masking_gate_rejects_or_vector_zero_in_panels_and_nested_rows(self):
        ref_ts = 1_700_000_000.0
        dashboards = _build_valid_17_dashboards_payload()
        freshness = _build_valid_freshness_observations(ref_ts)
        valid_res = probe.evaluate_dashboards(
            dashboards, freshness, probe.DEFAULT_FRESHNESS_THRESHOLDS, ref_ts
        )
        self.assertEqual(valid_res["status"], "PASS")
        self.assertEqual(
            valid_res["dashboards"]["k6-performance-capacity-overview"]["status"],
            "N/A",
        )

        # Inject forbidden masking expression inside a collapsed row panel
        dashboards["begqoward2epsf"]["dashboard"]["panels"].append(
            {
                "id": 99,
                "type": "row",
                "panels": [
                    {
                        "id": 100,
                        "title": "Masked Traffic",
                        "targets": [
                            {
                                "refId": "A",
                                "expr": "sum(rate(xray_traffic_bytes_total[5m])) or on() vector(0)",
                            }
                        ],
                    }
                ],
            }
        )
        masked_res = probe.evaluate_dashboards(
            dashboards, freshness, probe.DEFAULT_FRESHNESS_THRESHOLDS, ref_ts
        )
        self.assertEqual(masked_res["status"], "FAIL")
        self.assertEqual(
            masked_res["dashboards"]["begqoward2epsf"]["status"], "FAIL"
        )
        self.assertIn(
            100, masked_res["dashboards"]["begqoward2epsf"]["masked_panel_ids"]
        )

    def test_negative_isolation_gate_fails_on_cross_resource_or_env_fallback(self):
        self.assertEqual(probe.evaluate_negative_isolation(None)["status"], "NOT_RUN")

        passing_probes = {
            "metrics": {
                "resource_id": "__nonexistent_obs_probe__",
                "environment": "__invalid_env__",
                "nonexistent_resource_count": 0,
                "invalid_environment_count": 0,
            },
            "logs": {
                "resource_id": "__nonexistent_obs_probe__",
                "environment": "__invalid_env__",
                "nonexistent_resource_count": 0,
                "invalid_environment_count": 0,
            },
            "cmdb": {
                "resource_id": "__nonexistent_obs_probe__",
                "environment": "__invalid_env__",
                "nonexistent_resource_count": 0,
                "invalid_environment_count": 0,
            },
        }
        self.assertEqual(
            probe.evaluate_negative_isolation(passing_probes)["status"], "PASS"
        )

        # Simulate fallback leak on nonexistent resource_id
        leaking_probes = json.loads(json.dumps(passing_probes))
        leaking_probes["cmdb"]["nonexistent_resource_count"] = 3
        leak_res = probe.evaluate_negative_isolation(leaking_probes)
        self.assertEqual(leak_res["status"], "FAIL")
        self.assertIn("fallback vulnerability", leak_res["reason"])

    def test_continuity_30m_window_and_unapproved_recovery_drill_not_run(self):
        ref_ts = 1_700_001_800.0
        # 31 samples every 60s across 1800s (30 minutes)
        samples = [1_700_000_000.0 + i * 60 for i in range(31)]
        cont_pass = probe.evaluate_continuity_window(
            samples,
            window_seconds=1800,
            max_gap_seconds=180,
            reference_timestamp_sec=ref_ts,
        )
        self.assertEqual(cont_pass["status"], "PASS")

        # Introduce a 300s outage gap (> 180s threshold)
        gapped = [ts for ts in samples if not (1_700_000_600 < ts < 1_700_000_960)]
        cont_fail = probe.evaluate_continuity_window(
            gapped,
            window_seconds=1800,
            max_gap_seconds=180,
            reference_timestamp_sec=ref_ts,
        )
        self.assertEqual(cont_fail["status"], "FAIL")
        self.assertIn("gap", cont_fail["reason"])

        # Unapproved recovery drill MUST be NOT_RUN, never PASS
        self.assertEqual(probe.evaluate_recovery_drill(None)["status"], "NOT_RUN")
        self.assertEqual(
            probe.evaluate_recovery_drill({"authorized": False})["status"], "NOT_RUN"
        )

        # Authorized and timely recovery drill passes
        drill_ok = probe.evaluate_recovery_drill(
            {
                "authorized": True,
                "stop_ts": 1000.0,
                "outage_detected_ts": 1120.0,
                "start_ts": 1300.0,
                "recovered_detected_ts": 1410.0,
            },
            max_detection_seconds=180,
        )
        self.assertEqual(drill_ok["status"], "PASS")

        # Authorized but slow recovery detection fails
        drill_slow = probe.evaluate_recovery_drill(
            {
                "authorized": True,
                "stop_ts": 1000.0,
                "outage_detected_ts": 1250.0,
                "start_ts": 1300.0,
                "recovered_detected_ts": 1410.0,
            },
            max_detection_seconds=180,
        )
        self.assertEqual(drill_slow["status"], "FAIL")

    def test_release_rollback_redlines_block_retired_host_and_dns_cutover(self):
        default_gate = probe.evaluate_release_rollback_contract()
        self.assertEqual(default_gate["status"], "PASS")

        retired_host_gate = probe.evaluate_release_rollback_contract(
            {"rollback_target_host": "35.221.181.68"}
        )
        self.assertEqual(retired_host_gate["status"], "FAIL")
        self.assertIn("35.221.181.68", retired_host_gate["reason"])

        dns_gate = probe.evaluate_release_rollback_contract(
            {"dns_cutover_requested": True}
        )
        self.assertEqual(dns_gate["status"], "FAIL")

    def test_readonly_query_guard_and_redaction(self):
        probe.validate_readonly_query(
            "cmdb", "SELECT resource_id, environment FROM cmdb_assets LIMIT 10"
        )
        for bad_sql in (
            "DELETE FROM cmdb_assets",
            "SELECT 1; DROP TABLE cmdb_assets",
            "UPDATE cmdb_assets SET environment='prod'",
        ):
            with self.subTest(bad_sql=bad_sql):
                with self.assertRaises(ValueError):
                    probe.validate_readonly_query("cmdb", bad_sql)

        sanitized = probe.redact_sensitive(
            {
                "endpoint": "https://admin:supersecret@104.104.180.213/api/health",
                "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                "api_token": "plain-secret-token",
            }
        )
        dump = json.dumps(sanitized)
        self.assertNotIn("supersecret", dump)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", dump)
        self.assertNotIn("plain-secret-token", dump)
        self.assertNotIn("104.104.180.213", dump)
        self.assertIn("104.10***213", dump)


class ObservabilityClosureHttpAndRoleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.dashboards = _build_valid_17_dashboards_payload()
        self.require_auth_for_datasources = True
        self.expected_token = "readonly-test-token-123456"
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                if self.path == "/api/health":
                    self._send_json(
                        200,
                        {
                            "database": "ok",
                            "version": "13.2.4",
                            "commit": "0e79aef9d9",
                        },
                    )
                    return
                if self.path == "/login":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html>Grafana Login</html>")
                    return
                if self.path == "/api/datasources":
                    if (
                        fixture.require_auth_for_datasources
                        and auth != f"Bearer {fixture.expected_token}"
                    ):
                        self._send_json(401, {"message": "Unauthorized"})
                        return
                    self._send_json(
                        200,
                        [
                            {"uid": "victoriametrics", "type": "prometheus"},
                            {
                                "uid": "victorialogs",
                                "type": "victoriametrics-logs-datasource",
                            },
                            {"uid": "victoriatraces", "type": "jaeger"},
                            {"uid": "cmdb_postgres", "type": "postgres"},
                        ],
                    )
                    return
                if self.path.startswith("/api/dashboards/uid/"):
                    if auth != f"Bearer {fixture.expected_token}":
                        self._send_json(401, {"message": "Unauthorized"})
                        return
                    uid = self.path.split("/api/dashboards/uid/", 1)[1]
                    if uid in fixture.dashboards:
                        self._send_json(200, fixture.dashboards[uid])
                    else:
                        self._send_json(404, {"message": "Not found"})
                    return
                self._send_json(404, {"message": "Not found"})

            def _send_json(self, status: int, payload: object):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_unauthenticated_probe_reports_not_run_and_blocks_require_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc = probe.main(
                [
                    "--target-url",
                    self.base_url,
                    "--allow-loopback-http",
                    "--output-dir",
                    tmpdir,
                ]
            )
            self.assertEqual(rc, 0)
            evidence = json.loads((Path(tmpdir) / "evidence.json").read_text())
            self.assertEqual(evidence["overall_status"], "NOT_RUN")
            self.assertEqual(
                evidence["checks"]["endpoint_health"]["status"], "PASS"
            )
            self.assertEqual(
                evidence["checks"]["unauthenticated_boundary"]["status"], "PASS"
            )
            self.assertEqual(
                evidence["checks"]["datasources_contract"]["status"], "NOT_RUN"
            )
            self.assertEqual(
                evidence["checks"]["dashboards_17_coverage"]["status"], "NOT_RUN"
            )
            self.assertEqual(
                evidence["checks"]["controlled_recovery_drill"]["status"], "NOT_RUN"
            )

            # With --require-pass, NOT_RUN must exit non-zero (rc == 3)
            rc_strict = probe.main(
                [
                    "--target-url",
                    self.base_url,
                    "--allow-loopback-http",
                    "--require-pass",
                ]
            )
            self.assertEqual(rc_strict, 3)

    def test_authenticated_full_closure_passes_all_gates(self):
        ref_ts = 1_700_001_800.0
        config = {
            "reference_timestamp_sec": ref_ts,
            "auth": {"token": self.expected_token},
            "freshness_observations": _build_valid_freshness_observations(ref_ts),
            "signal_freshness": {
                "metrics_latest_ts": ref_ts - 45,
                "logs_latest_ts": ref_ts - 90,
                "cmdb_latest_ts": ref_ts - 300,
            },
            "negative_isolation_probes": {
                "metrics": {
                    "resource_id": "__nonexistent_obs_probe__",
                    "environment": "__invalid_env__",
                    "nonexistent_resource_count": 0,
                    "invalid_environment_count": 0,
                },
                "logs": {
                    "resource_id": "__nonexistent_obs_probe__",
                    "environment": "__invalid_env__",
                    "nonexistent_resource_count": 0,
                    "invalid_environment_count": 0,
                },
                "cmdb": {
                    "resource_id": "__nonexistent_obs_probe__",
                    "environment": "__invalid_env__",
                    "nonexistent_resource_count": 0,
                    "invalid_environment_count": 0,
                },
            },
            "continuity_timestamps_sec": [
                1_700_000_000.0 + i * 60 for i in range(31)
            ],
            "recovery_drill": {
                "authorized": True,
                "stop_ts": 1_700_000_100.0,
                "outage_detected_ts": 1_700_000_220.0,
                "start_ts": 1_700_000_300.0,
                "recovered_detected_ts": 1_700_000_410.0,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.json"
            out_dir = Path(tmpdir) / "bundle"
            cfg_file.write_text(json.dumps(config))
            rc = probe.main(
                [
                    "--target-url",
                    self.base_url,
                    "--allow-loopback-http",
                    "--config-json",
                    str(cfg_file),
                    "--output-dir",
                    str(out_dir),
                    "--require-pass",
                ]
            )
            self.assertEqual(rc, 0)
            evidence = json.loads((out_dir / "evidence.json").read_text())
            self.assertEqual(evidence["overall_status"], "PASS")
            self.assertNotIn(
                self.expected_token, (out_dir / "evidence.json").read_text()
            )
            self.assertTrue((out_dir / "report.zh.md").exists())
            self.assertTrue((out_dir / "manifest.json").exists())

    def test_ansible_role_executes_probe_and_respects_gate_contract(self):
        playbook_doc = yaml.safe_load(
            (ROOT / "observability_closure_acceptance.yml").read_text()
        )
        self.assertEqual(playbook_doc[0]["hosts"], "localhost")
        self.assertIn("observability_acceptance", playbook_doc[0]["roles"])

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "role-report"
            variables = {
                "observability_acceptance_environment": "uat",
                "observability_acceptance_target_urls": [self.base_url],
                "observability_acceptance_allow_loopback_http": True,
                "observability_acceptance_require_pass": False,
                "observability_acceptance_output_dir": str(out_dir),
            }
            result = subprocess.run(
                [
                    "ansible-playbook",
                    "-i",
                    "localhost,",
                    "-c",
                    "local",
                    "observability_closure_acceptance.yml",
                    "-e",
                    json.dumps(variables),
                ],
                cwd=ROOT,
                env=dict(os.environ, ANSIBLE_NOCOLOR="1"),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((out_dir / "evidence.json").exists())
            evidence = json.loads((out_dir / "evidence.json").read_text())
            self.assertEqual(evidence["overall_status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
