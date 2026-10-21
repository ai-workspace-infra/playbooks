"""Verify Phase 1b Dual-Link Observability contracts for Collector, Caddy, Dashboards, and Web-SaaS OTel wiring."""

import json
from pathlib import Path
import re
import unittest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
OBS_ROLE = ROOT / "roles/docker/observability-server"
FILES = OBS_ROLE / "files"
TEMPLATES = OBS_ROLE / "templates"


class DualLinkObservabilityContractTests(unittest.TestCase):
    def test_otel_collector_exports_to_victoria_traces_and_scrubs_sensitive_attributes(self):
        raw = (TEMPLATES / "otel-collector.yml.j2").read_text(encoding="utf-8")
        if yaml is not None:
            config = yaml.safe_load(raw)
            vt_exporter = config["exporters"]["otlphttp/victoria-traces"]
            self.assertEqual(
                vt_exporter["endpoint"],
                "http://victoria-traces:10428/insert/opentelemetry",
            )
            dimensions = {
                item["name"]
                for item in config["connectors"]["spanmetrics"]["dimensions"]
            }
        else:
            self.assertIn(
                "endpoint: http://victoria-traces:10428/insert/opentelemetry",
                raw,
            )
            dimensions = set(re.findall(r"^\s*-\s*name:\s*([^\s#]+)", raw, flags=re.M))
        self.assertNotIn("victoria-traces:4318", raw)

        for sensitive_key in (
            "db.statement",
            "db.query.text",
            "http.request.header.authorization",
            "http.request.header.x_service_token",
            "user.email",
            "account.uuid",
            "proxy.uuid",
        ):
            self.assertIn(f'delete_key(attributes, "{sensitive_key}")', raw)

        required_dimensions = {
            "agent.name",
            "agent.type",
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "gen_ai.operation.name",
            "tool.name",
            "mcp.server.name",
            "env",
            "environment",
            "transport",
            "stage",
            "outcome",
            "error_class",
            "http.route",
            "http.response.status_code",
            "db.system",
            "db.operation",
        }
        self.assertTrue(required_dimensions.issubset(dimensions))

        forbidden_high_cardinality = {
            "snapshot_ref",
            "batch_ref",
            "uuid",
            "email",
            "trace_id",
            "user.email",
            "account.uuid",
            "proxy.uuid",
            "db.statement",
        }
        self.assertFalse(dimensions & forbidden_high_cardinality)

    def test_observability_caddy_routes_otlp_prefixes_through_collector_without_shadowing(self):
        caddy = (TEMPLATES / "observability.caddy.j2").read_text(encoding="utf-8")
        self.assertIn("handle_path /otlp/* {\n        reverse_proxy 127.0.0.1:4319\n    }", caddy)
        self.assertIn("handle_path /ingest/otlp/* {\n        reverse_proxy 127.0.0.1:4319\n    }", caddy)

        for line in caddy.splitlines():
            stripped = line.strip()
            if stripped.startswith(("@otlp_metrics", "@otlp_logs", "@otlp_traces")):
                self.assertNotIn("/otlp/v1/", stripped)
                self.assertNotIn("/ingest/otlp/v1/", stripped)

    def test_dual_link_grafana_dashboards_are_linked_and_cover_both_chains(self):
        net_dashboard = json.loads(
            (FILES / "dual-link-network-latency-dashboard.json").read_text(encoding="utf-8")
        )
        traffic_dashboard = json.loads(
            (FILES / "dual-link-traffic-accounting-dashboard.json").read_text(encoding="utf-8")
        )

        self.assertEqual(net_dashboard["uid"], "dual-link-network-latency")
        self.assertEqual(
            net_dashboard["title"],
            "Network Latency Chain (RTT / DNS / TLS / Proxy / App / DB)",
        )
        self.assertEqual(traffic_dashboard["uid"], "dual-link-traffic-accounting")
        self.assertEqual(
            traffic_dashboard["title"],
            "Traffic Statistics & Billing Chain (Exporter → Vector → Billing → PG → Accounts)",
        )

        net_links = net_dashboard.get("links", [])
        self.assertTrue(
            any(
                "dual-link-traffic-accounting" in link.get("url", "")
                and link.get("keepTime") is True
                and link.get("includeVars") is True
                for link in net_links
            )
        )
        traffic_links = traffic_dashboard.get("links", [])
        self.assertTrue(
            any(
                "dual-link-network-latency" in link.get("url", "")
                and link.get("keepTime") is True
                and link.get("includeVars") is True
                for link in traffic_links
            )
        )

        net_exprs = "\n".join(
            t.get("expr", "")
            for p in net_dashboard["panels"]
            for t in p.get("targets", [])
        )
        for expected_metric in (
            "probe_http_duration_seconds",
            "probe_icmp_duration_seconds",
            "probe_success",
            "xray_snapshot_http_phase_duration_seconds_bucket",
            "caddy_http_response_duration_seconds_bucket",
            "xhttp_events_total",
            "node_netstat_Tcp_RetransSegs",
            "traces_span_metrics_duration_milliseconds_bucket",
            "sql\\\\.conn\\\\.(query|exec)",
        ):
            self.assertIn(expected_metric, net_exprs)

        traffic_exprs = "\n".join(
            t.get("expr", "")
            for p in traffic_dashboard["panels"]
            for t in p.get("targets", [])
        )
        for expected_metric in (
            "xray_up",
            "xray_snapshot_last_collected_timestamp_seconds",
            "xray_snapshot_collect_total",
            "xray_snapshot_push_total",
            "vector_component_received_events_total",
            "vector_component_sent_events_total",
            "vector_component_errors_total",
            "vector_buffer_events",
            "traces_span_metrics_calls_total",
            "billing\\\\.ingest\\\\..*",
            "web-saas-accounts",
        ):
            self.assertIn(expected_metric, traffic_exprs)

        for dashboard in (net_dashboard, traffic_dashboard):
            panels = dashboard["panels"]
            for i, left in enumerate(panels):
                a = left["gridPos"]
                for right in panels[i + 1 :]:
                    b = right["gridPos"]
                    self.assertFalse(
                        a["x"] < b["x"] + b["w"]
                        and b["x"] < a["x"] + a["w"]
                        and a["y"] < b["y"] + b["h"]
                        and b["y"] < a["y"] + a["h"]
                    )

    def test_web_saas_collector_accepts_otlp_grpc_and_http_to_single_backend(self):
        raw = (
            ROOT / "roles/vhosts/web_saas_host_config/templates/otel-collector.yaml.j2"
        ).read_text(encoding="utf-8")
        if yaml is not None:
            collector = yaml.safe_load(raw)
            protocols = collector["receivers"]["otlp"]["protocols"]
            self.assertEqual(protocols["grpc"]["endpoint"], "0.0.0.0:4317")
            self.assertEqual(protocols["http"]["endpoint"], "0.0.0.0:4318")
            self.assertEqual(
                collector["exporters"]["otlphttp/victoriatraces"]["traces_endpoint"],
                "https://observability.svc.plus/ingest/otlp/v1/traces",
            )
        else:
            self.assertIn("endpoint: 0.0.0.0:4317", raw)
            self.assertIn("endpoint: 0.0.0.0:4318", raw)
            self.assertIn(
                "traces_endpoint: https://observability.svc.plus/ingest/otlp/v1/traces",
                raw,
            )


if __name__ == "__main__":
    unittest.main()
