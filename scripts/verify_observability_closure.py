#!/usr/bin/env python3
"""Read-only Observability data-closure acceptance probe and gate validator.

Enforces zero side-effects (GET-only endpoint probes and read-only queries),
fixed freshness thresholds (Metrics <= 180s, Logs <= 300s, CMDB <= 3600s),
17-dashboard baseline verification, negative isolation probes, 30-minute
continuity window checks, unapproved recovery-drill NOT_RUN enforcement,
and sanitized evidence output.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

SCHEMA_VERSION = "observability-closure-acceptance/v1"
ALLOWED_STATUSES = ("PASS", "FAIL", "NOT_RUN", "N/A")
REQUIRED_DATASOURCE_UIDS = (
    "victoriametrics",
    "victorialogs",
    "victoriatraces",
    "cmdb_postgres",
)
NEGATIVE_PROBE_RESOURCE_ID = "__nonexistent_obs_probe__"
NEGATIVE_PROBE_ENVIRONMENT = "__invalid_env__"
FORBIDDEN_ROLLBACK_HOST = "35.221.181.68"

DEFAULT_FRESHNESS_THRESHOLDS = {
    "metrics_max_age_seconds": 180,
    "logs_max_age_seconds": 300,
    "cmdb_max_age_seconds": 3600,
    "continuity_window_seconds": 1800,
    "scrape_interval_seconds": 60,
}

EXPECTED_17_DASHBOARDS: dict[str, dict[str, Any]] = {
    "StarsL-JOB-node": {
        "title": "Node Exporter Dashboard 20240520 通用JOB分组版",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "agent-ai-observability": {
        "title": "Agent / AI Application Observability",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "ai-aggregator-overview": {
        "title": "AI Aggregator Overview",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "begqoward2epsf": {
        "title": "Xray Dashboard",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "blackbox-exporter-overview": {
        "title": "Blackbox Exporter & SSL Probe Overview",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "cmdb-resource-overview": {
        "title": "CMDB · 多云资源总览",
        "signal": "cmdb",
        "datasource_uid": "cmdb_postgres",
        "on_demand": False,
        "origin": "imported-16",
    },
    "homepage-navigation": {
        "title": "平台导航与总览 (Navigation Homepage)",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "k6-performance-capacity-overview": {
        "title": "k6 Performance & Capacity Stress Test Dashboard",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": True,
        "origin": "imported-16",
    },
    "postgres-exporter-overview": {
        "title": "PostgreSQL Database Overview",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "process-exporter-with-tree": {
        "title": "process exporter dashboard with treemap",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "serverless-fullstack-architecture": {
        "title": "全栈无服务器与边缘架构总览 (Serverless & Edge Full-Stack Topology)",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "victoria-logs-overview": {
        "title": "Victoria Logs Overview",
        "signal": "logs",
        "datasource_uid": "victorialogs",
        "on_demand": False,
        "origin": "imported-16",
    },
    "victoria-traces-overview": {
        "title": "VictoriaTraces Distributed Tracing APM",
        "signal": "traces",
        "datasource_uid": "victoriatraces",
        "on_demand": False,
        "origin": "imported-16",
    },
    "xconnect-comprehensive": {
        "title": "XConnect 综合监控",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "xconnect-user-connection-diagnostics": {
        "title": "XConnect 用户慢连接诊断",
        "signal": "traces",
        "datasource_uid": "victoriatraces",
        "on_demand": False,
        "origin": "imported-16",
    },
    "xhttp-edge-reliability": {
        "title": "XHTTP Edge Reliability",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "imported-16",
    },
    "web-saas-k3s-v5-overview": {
        "title": "Web-SaaS K3s V5 PROD Cluster Overview",
        "signal": "metrics",
        "datasource_uid": "victoriametrics",
        "on_demand": False,
        "origin": "retained-k3s-overview",
    },
}

MASKING_QUERY_RE = re.compile(
    r"\bor\s+(?:on\s*\([^)]*\)\s*)?vector\s*\(\s*0(?:\.0+)?\s*\)",
    re.IGNORECASE,
)
MUTATING_SQL_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|CALL|EXECUTE)\b",
    re.IGNORECASE,
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SECRET_HEADER_RE = re.compile(
    r"(?i)(?:Bearer|Basic)\s+[A-Za-z0-9/+_.=-]{8,}"
)
URL_CREDENTIAL_RE = re.compile(r"(https?://)[^\s/:\"']+:[^\s/@\"']+@")
KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api_key|private_key)\s*([=:])\s*([^\s,;\"']+)"
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def redact_ip(ip_text: str) -> str:
    if ip_text in ("127.0.0.1", "0.0.0.0", FORBIDDEN_ROLLBACK_HOST):
        return ip_text
    parts = ip_text.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1][:2]}***{parts[3]}"
    return "***"


def redact_sensitive(value: Any) -> Any:
    """Recursively sanitize credentials, tokens, and live IPv4 addresses."""
    if isinstance(value, str):
        text = URL_CREDENTIAL_RE.sub(r"\1***:***@", value)
        text = SECRET_HEADER_RE.sub("[REDACTED_AUTH]", text)
        text = KEY_VALUE_SECRET_RE.sub(r"\1\2[REDACTED]", text)
        text = IPV4_RE.sub(lambda m: redact_ip(m.group(0)), text)
        return text
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for k, v in value.items():
            key_lower = str(k).lower()
            if any(
                s in key_lower
                for s in ("password", "secret", "token", "private_key", "authorization")
            ):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = redact_sensitive(v)
        return sanitized
    return value


def validate_readonly_query(kind: str, expr: str) -> None:
    """Reject any mutating or unsafe query expression before execution."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("Query expression must be a non-empty string")
    if kind == "cmdb":
        stripped = expr.strip()
        if ";" in stripped.rstrip(";"):
            raise ValueError("Multi-statement SQL queries are forbidden")
        if not re.match(r"^(?:SELECT|WITH)\b", stripped, re.IGNORECASE):
            raise ValueError("CMDB probe queries must begin with SELECT or WITH")
        if MUTATING_SQL_RE.search(stripped):
            raise ValueError("Mutating SQL keyword detected in read-only probe")


def contains_masking_fallback(query: str) -> bool:
    """Detect forbidden 'or vector(0)' fallbacks that mask missing telemetry."""
    if not isinstance(query, str):
        return False
    return bool(MASKING_QUERY_RE.search(query))


def extract_panel_queries(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract queries recursively from panels and collapsed row sub-panels."""
    extracted: list[dict[str, Any]] = []
    for panel in panels or []:
        if not isinstance(panel, dict):
            continue
        panel_id = panel.get("id")
        panel_title = panel.get("title", "")
        nested = panel.get("panels")
        if isinstance(nested, list) and nested:
            extracted.extend(extract_panel_queries(nested))
        for target in panel.get("targets", []) or []:
            if not isinstance(target, dict):
                continue
            query = (
                target.get("expr")
                or target.get("rawSql")
                or target.get("query")
                or ""
            )
            if isinstance(query, str) and query.strip():
                extracted.append(
                    {
                        "panel_id": panel_id,
                        "panel_title": panel_title,
                        "ref_id": target.get("refId", ""),
                        "query": query.strip(),
                    }
                )
    return extracted


def evaluate_freshness(
    latest_timestamp_sec: float | int | None,
    reference_timestamp_sec: float | int,
    max_age_seconds: int,
    *,
    on_demand: bool = False,
    idle_declared: bool = False,
) -> dict[str, Any]:
    """Evaluate whether a signal's latest timestamp satisfies the freshness limit."""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if latest_timestamp_sec is None:
        if on_demand and idle_declared:
            return {
                "status": "N/A",
                "age_seconds": None,
                "max_age_seconds": max_age_seconds,
                "reason": "on-demand workload is idle with explicit idle/last-run state",
            }
        return {
            "status": "FAIL",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
            "reason": "missing telemetry sample",
        }
    age_seconds = float(reference_timestamp_sec) - float(latest_timestamp_sec)
    if age_seconds < -60.0:
        return {
            "status": "FAIL",
            "age_seconds": round(age_seconds, 3),
            "max_age_seconds": max_age_seconds,
            "reason": "sample timestamp is in the future beyond clock skew tolerance",
        }
    if age_seconds <= float(max_age_seconds):
        return {
            "status": "PASS",
            "age_seconds": round(max(0.0, age_seconds), 3),
            "max_age_seconds": max_age_seconds,
            "reason": "fresh within threshold",
        }
    return {
        "status": "FAIL",
        "age_seconds": round(age_seconds, 3),
        "max_age_seconds": max_age_seconds,
        "reason": f"stale sample age {age_seconds:.1f}s exceeds limit {max_age_seconds}s",
    }


def evaluate_negative_isolation(probe_results: dict[str, Any] | None) -> dict[str, Any]:
    """Verify nonexistent resource_id and environment queries return strictly empty sets."""
    if probe_results is None:
        return {
            "status": "NOT_RUN",
            "probed_resource_id": NEGATIVE_PROBE_RESOURCE_ID,
            "probed_environment": NEGATIVE_PROBE_ENVIRONMENT,
            "reason": "authenticated datasource query access unavailable in current run",
            "signals": {},
        }
    required_signals = ("metrics", "logs", "cmdb")
    signals_summary: dict[str, Any] = {}
    violations: list[str] = []
    missing_signals: list[str] = []

    for sig in required_signals:
        entry = probe_results.get(sig)
        if not isinstance(entry, dict):
            missing_signals.append(sig)
            continue
        res_id = entry.get("resource_id", NEGATIVE_PROBE_RESOURCE_ID)
        env_val = entry.get("environment", NEGATIVE_PROBE_ENVIRONMENT)
        res_count = entry.get("nonexistent_resource_count")
        env_count = entry.get("invalid_environment_count")
        if res_id != NEGATIVE_PROBE_RESOURCE_ID or env_val != NEGATIVE_PROBE_ENVIRONMENT:
            violations.append(f"{sig}: invalid negative probe filter constants")
            signals_summary[sig] = {"status": "FAIL", "reason": "filter constant mismatch"}
            continue
        if not isinstance(res_count, int) or not isinstance(env_count, int):
            missing_signals.append(sig)
            continue
        if res_count > 0 or env_count > 0:
            violations.append(
                f"{sig}: non-empty result (resource_count={res_count}, env_count={env_count})"
            )
            signals_summary[sig] = {
                "status": "FAIL",
                "nonexistent_resource_count": res_count,
                "invalid_environment_count": env_count,
            }
        else:
            signals_summary[sig] = {
                "status": "PASS",
                "nonexistent_resource_count": 0,
                "invalid_environment_count": 0,
            }

    if violations:
        return {
            "status": "FAIL",
            "probed_resource_id": NEGATIVE_PROBE_RESOURCE_ID,
            "probed_environment": NEGATIVE_PROBE_ENVIRONMENT,
            "reason": "Cross-resource/cross-environment fallback vulnerability: "
            + "; ".join(violations),
            "signals": signals_summary,
        }
    if missing_signals:
        return {
            "status": "NOT_RUN",
            "probed_resource_id": NEGATIVE_PROBE_RESOURCE_ID,
            "probed_environment": NEGATIVE_PROBE_ENVIRONMENT,
            "reason": "Incomplete negative isolation signals: "
            + ", ".join(missing_signals),
            "signals": signals_summary,
        }
    return {
        "status": "PASS",
        "probed_resource_id": NEGATIVE_PROBE_RESOURCE_ID,
        "probed_environment": NEGATIVE_PROBE_ENVIRONMENT,
        "reason": "All nonexistent resource_id and environment queries returned empty sets",
        "signals": signals_summary,
    }


def evaluate_continuity_window(
    timestamps_sec: list[float | int] | None,
    *,
    window_seconds: int = 1800,
    max_gap_seconds: int = 180,
    reference_timestamp_sec: float | int | None = None,
) -> dict[str, Any]:
    """Verify at least 30 minutes of continuous sampling without gaps exceeding threshold."""
    if timestamps_sec is None:
        return {
            "status": "NOT_RUN",
            "window_seconds": window_seconds,
            "max_allowed_gap_seconds": max_gap_seconds,
            "reason": "authenticated 30-minute range query not executed",
        }
    cleaned = sorted({float(ts) for ts in timestamps_sec})
    if len(cleaned) < 2:
        return {
            "status": "FAIL",
            "window_seconds": window_seconds,
            "max_allowed_gap_seconds": max_gap_seconds,
            "sample_count": len(cleaned),
            "reason": "fewer than 2 samples in continuity window",
        }
    covered_seconds = cleaned[-1] - cleaned[0]
    max_observed_gap = max(
        cleaned[i + 1] - cleaned[i] for i in range(len(cleaned) - 1)
    )
    min_required_span = max(0, window_seconds - max_gap_seconds)
    if covered_seconds < min_required_span:
        return {
            "status": "FAIL",
            "window_seconds": window_seconds,
            "max_allowed_gap_seconds": max_gap_seconds,
            "sample_count": len(cleaned),
            "covered_seconds": round(covered_seconds, 3),
            "max_observed_gap_seconds": round(max_observed_gap, 3),
            "reason": (
                f"continuity span {covered_seconds:.1f}s is shorter than required "
                f"{min_required_span}s"
            ),
        }
    if max_observed_gap > float(max_gap_seconds):
        return {
            "status": "FAIL",
            "window_seconds": window_seconds,
            "max_allowed_gap_seconds": max_gap_seconds,
            "sample_count": len(cleaned),
            "covered_seconds": round(covered_seconds, 3),
            "max_observed_gap_seconds": round(max_observed_gap, 3),
            "reason": (
                f"sampling gap {max_observed_gap:.1f}s exceeds limit {max_gap_seconds}s"
            ),
        }
    if reference_timestamp_sec is not None:
        tail_age = float(reference_timestamp_sec) - cleaned[-1]
        if tail_age > float(max_gap_seconds):
            return {
                "status": "FAIL",
                "window_seconds": window_seconds,
                "max_allowed_gap_seconds": max_gap_seconds,
                "sample_count": len(cleaned),
                "covered_seconds": round(covered_seconds, 3),
                "max_observed_gap_seconds": round(max_observed_gap, 3),
                "tail_age_seconds": round(tail_age, 3),
                "reason": (
                    f"latest sample is stale by {tail_age:.1f}s (limit {max_gap_seconds}s)"
                ),
            }
    return {
        "status": "PASS",
        "window_seconds": window_seconds,
        "max_allowed_gap_seconds": max_gap_seconds,
        "sample_count": len(cleaned),
        "covered_seconds": round(covered_seconds, 3),
        "max_observed_gap_seconds": round(max_observed_gap, 3),
        "reason": "continuous sampling verified across window without gaps",
    }


def evaluate_recovery_drill(
    drill_observation: dict[str, Any] | None,
    *,
    max_detection_seconds: int = 180,
) -> dict[str, Any]:
    """Evaluate controlled agent stop/start recovery drill; strictly NOT_RUN if unapproved."""
    if not isinstance(drill_observation, dict) or not drill_observation.get(
        "authorized", False
    ):
        return {
            "status": "NOT_RUN",
            "authorized": False,
            "max_detection_seconds": max_detection_seconds,
            "reason": (
                "Target stop/recovery drill is not authorized in read-only mode; "
                "strictly marked NOT_RUN per gate policy"
            ),
        }
    required_keys = (
        "stop_ts",
        "outage_detected_ts",
        "start_ts",
        "recovered_detected_ts",
    )
    if any(
        not isinstance(drill_observation.get(k), (int, float))
        for k in required_keys
    ):
        return {
            "status": "FAIL",
            "authorized": True,
            "max_detection_seconds": max_detection_seconds,
            "reason": "Authorized recovery drill is missing required stop/detect/start/recover timestamps",
        }
    stop_ts = float(drill_observation["stop_ts"])
    outage_ts = float(drill_observation["outage_detected_ts"])
    start_ts = float(drill_observation["start_ts"])
    recovered_ts = float(drill_observation["recovered_detected_ts"])

    outage_delay = outage_ts - stop_ts
    recovery_delay = recovered_ts - start_ts

    if outage_delay < 0 or recovery_delay < 0 or start_ts <= outage_ts:
        return {
            "status": "FAIL",
            "authorized": True,
            "max_detection_seconds": max_detection_seconds,
            "outage_detection_seconds": round(outage_delay, 3),
            "recovery_detection_seconds": round(recovery_delay, 3),
            "reason": "Invalid recovery drill timestamp ordering",
        }
    if outage_delay > max_detection_seconds or recovery_delay > max_detection_seconds:
        return {
            "status": "FAIL",
            "authorized": True,
            "max_detection_seconds": max_detection_seconds,
            "outage_detection_seconds": round(outage_delay, 3),
            "recovery_detection_seconds": round(recovery_delay, 3),
            "reason": (
                f"Detection latency exceeded {max_detection_seconds}s "
                f"(outage={outage_delay:.1f}s, recovery={recovery_delay:.1f}s)"
            ),
        }
    return {
        "status": "PASS",
        "authorized": True,
        "max_detection_seconds": max_detection_seconds,
        "outage_detection_seconds": round(outage_delay, 3),
        "recovery_detection_seconds": round(recovery_delay, 3),
        "reason": "Controlled outage and recovery converged within threshold",
    }


def evaluate_dashboards(
    dashboards_payload: dict[str, dict[str, Any]] | None,
    freshness_observations: dict[str, dict[str, Any]] | None,
    thresholds: dict[str, int],
    reference_timestamp_sec: float | int,
) -> dict[str, Any]:
    """Validate all 17 baseline dashboards, anti-masking rules, and query freshness."""
    per_dashboard: dict[str, dict[str, Any]] = {}
    if dashboards_payload is None:
        for uid, spec in EXPECTED_17_DASHBOARDS.items():
            per_dashboard[uid] = {
                "uid": uid,
                "expected_title": spec["title"],
                "origin": spec["origin"],
                "signal": spec["signal"],
                "status": "NOT_RUN",
                "reason": "Grafana API authentication required to inspect live dashboard model and panel queries",
            }
        return {
            "status": "NOT_RUN",
            "expected_count": len(EXPECTED_17_DASHBOARDS),
            "verified_count": 0,
            "dashboards": per_dashboard,
            "reason": "17-dashboard model and freshness verification pending read-only API token",
        }

    signal_threshold_map = {
        "metrics": thresholds["metrics_max_age_seconds"],
        "logs": thresholds["logs_max_age_seconds"],
        "cmdb": thresholds["cmdb_max_age_seconds"],
        "traces": thresholds["logs_max_age_seconds"],
    }

    any_fail = False
    any_not_run = False
    verified_count = 0

    for uid, spec in EXPECTED_17_DASHBOARDS.items():
        model_entry = dashboards_payload.get(uid)
        if not isinstance(model_entry, dict):
            any_fail = True
            per_dashboard[uid] = {
                "uid": uid,
                "expected_title": spec["title"],
                "origin": spec["origin"],
                "signal": spec["signal"],
                "status": "FAIL",
                "reason": "Dashboard UID missing from target",
            }
            continue

        dashboard_obj = model_entry.get("dashboard", model_entry)
        actual_title = dashboard_obj.get("title", "")
        panels = dashboard_obj.get("panels", [])
        queries = extract_panel_queries(panels)
        templating_list = (
            dashboard_obj.get("templating", {}).get("list", [])
            if isinstance(dashboard_obj.get("templating"), dict)
            else []
        )

        if not panels:
            any_fail = True
            per_dashboard[uid] = {
                "uid": uid,
                "expected_title": spec["title"],
                "actual_title": actual_title,
                "origin": spec["origin"],
                "signal": spec["signal"],
                "status": "FAIL",
                "reason": "Dashboard has no panels",
            }
            continue

        masked = [
            q["panel_id"] for q in queries if contains_masking_fallback(q["query"])
        ]
        if masked:
            any_fail = True
            per_dashboard[uid] = {
                "uid": uid,
                "expected_title": spec["title"],
                "actual_title": actual_title,
                "origin": spec["origin"],
                "signal": spec["signal"],
                "status": "FAIL",
                "masked_panel_ids": masked,
                "reason": f"Forbidden masking expression 'or vector(0)' in panels {masked}",
            }
            continue

        if freshness_observations is None or uid not in freshness_observations:
            any_not_run = True
            per_dashboard[uid] = {
                "uid": uid,
                "expected_title": spec["title"],
                "actual_title": actual_title,
                "origin": spec["origin"],
                "signal": spec["signal"],
                "panel_count": len(panels),
                "query_count": len(queries),
                "variable_count": len(templating_list),
                "status": "NOT_RUN",
                "reason": "Dashboard structure valid; live query freshness not sampled",
            }
            continue

        obs = freshness_observations[uid]
        max_age = signal_threshold_map[spec["signal"]]
        freshness = evaluate_freshness(
            obs.get("latest_timestamp_sec"),
            reference_timestamp_sec,
            max_age,
            on_demand=bool(spec.get("on_demand", False)),
            idle_declared=bool(obs.get("idle_declared", False)),
        )
        status = freshness["status"]
        if status == "FAIL":
            any_fail = True
        elif status == "NOT_RUN":
            any_not_run = True
        else:
            verified_count += 1

        per_dashboard[uid] = {
            "uid": uid,
            "expected_title": spec["title"],
            "actual_title": actual_title,
            "origin": spec["origin"],
            "signal": spec["signal"],
            "panel_count": len(panels),
            "query_count": len(queries),
            "variable_count": len(templating_list),
            "freshness": freshness,
            "status": status,
            "reason": freshness["reason"],
        }

    overall = "FAIL" if any_fail else ("NOT_RUN" if any_not_run else "PASS")
    return {
        "status": overall,
        "expected_count": len(EXPECTED_17_DASHBOARDS),
        "verified_count": verified_count,
        "dashboards": per_dashboard,
        "reason": (
            "All 17 dashboards verified"
            if overall == "PASS"
            else "One or more dashboards failed or remain NOT_RUN"
        ),
    }


def evaluate_release_rollback_contract(
    plan_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate release dependency ordering and rollback safety redlines."""
    plan = {
        "dependency_chain": [
            "OBS-00: Baseline & Unified Resource Key Contract (gitops#558)",
            "OBS-01/02/03: Producer & Collector Adaptation (iac_modules#471, playbooks#766, artifacts#221)",
            "OBS-04: 16 Imported Dashboards + K3s Overview Alignment (gitops#559)",
            "UAT/Test Acceptance: Read-only Closure Probe Gate Verification",
            "Authorized Production Release: GitOps Flux Reconcile & Scoped Playbooks Rollout",
            "OBS-05: 30-Minute Continuity & Controlled Agent Recovery Drill (playbooks#767)",
        ],
        "rollback_target_host": "gitops-helmrelease-and-configmap-revert",
        "dns_cutover_requested": False,
        "delete_legacy_data_requested": False,
        "expand_vault_policy_requested": False,
        "enable_prod_alert_channels_requested": False,
    }
    if isinstance(plan_overrides, dict):
        plan.update(plan_overrides)

    violations: list[str] = []
    if FORBIDDEN_ROLLBACK_HOST in json.dumps(plan):
        violations.append(
            f"Retired host {FORBIDDEN_ROLLBACK_HOST} is deleted and forbidden as a rollback target"
        )
    if plan.get("dns_cutover_requested"):
        violations.append("DNS cutover is forbidden in Observability repair scope")
    if plan.get("delete_legacy_data_requested"):
        violations.append("Deleting legacy resources or telemetry data is forbidden")
    if plan.get("expand_vault_policy_requested"):
        violations.append("Expanding Vault permissions is forbidden")
    if plan.get("enable_prod_alert_channels_requested"):
        violations.append("Enabling production notification channels is forbidden")

    return {
        "status": "FAIL" if violations else "PASS",
        "dependency_chain": plan["dependency_chain"],
        "rollback_mechanism": (
            "GitOps revert of HelmRelease chart digest / ConfigMap bundle SHA "
            "and Playbooks role commit rollback; retired host 35.221.181.68 is strictly excluded"
        ),
        "violations": violations,
        "reason": (
            "Release dependency chain and rollback safety redlines verified"
            if not violations
            else "; ".join(violations)
        ),
    }


def _http_get(
    base_url: str,
    path: str,
    *,
    auth_header: str | None = None,
    timeout: int = 10,
    allow_loopback_http: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.username or parsed.password:
        raise ValueError("Embedded URL credentials are forbidden")
    is_loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if parsed.scheme != "https" and not (
        allow_loopback_http and parsed.scheme == "http" and is_loopback
    ):
        raise ValueError("HTTPS is required for Observability endpoint probes")
    if not path.startswith("/") or ".." in path:
        raise ValueError("Invalid probe path")

    headers = {
        "Accept": "application/json, text/html;q=0.8",
        "User-Agent": "obs-05-closure-acceptance-probe/1.0",
    }
    if auth_header:
        headers["Authorization"] = auth_header

    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers=headers,
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(2_000_000)
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, body
    except urllib.error.HTTPError as err:
        try:
            body = err.read(65_536) if err.fp else b""
            hdrs = {k.lower(): v for k, v in err.headers.items()} if err.headers else {}
            return err.code, hdrs, body
        finally:
            err.close()


def _build_auth_header(auth: dict[str, str] | None) -> str | None:
    if not isinstance(auth, dict):
        return None
    token = auth.get("token", "").strip()
    if token:
        return f"Bearer {token}"
    user = auth.get("username", "").strip()
    password = auth.get("password", "")
    if user and password:
        encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
    return None


def probe_live_endpoint(
    base_url: str,
    *,
    auth: dict[str, str] | None = None,
    timeout: int = 10,
    allow_loopback_http: bool = False,
) -> dict[str, Any]:
    """Execute read-only GET probes against an Observability Grafana endpoint."""
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolved_ips: list[str] = []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        resolved_ips = sorted({redact_ip(item[4][0]) for item in infos})
    except OSError:
        resolved_ips = []

    try:
        health_code, health_hdrs, health_raw = _http_get(
            base_url,
            "/api/health",
            timeout=timeout,
            allow_loopback_http=allow_loopback_http,
        )
        health_json = json.loads(health_raw.decode("utf-8")) if health_code == 200 else {}
        if not isinstance(health_json, dict):
            health_json = {}
    except (ValueError, OSError, urllib.error.URLError) as exc:
        return {
            "target_url": base_url,
            "resolved_ips_redacted": resolved_ips,
            "endpoint_health": {
                "status": "FAIL",
                "reason": f"Health endpoint probe failed: {type(exc).__name__}",
            },
            "unauthenticated_boundary": {
                "status": "FAIL",
                "reason": "Unreachable endpoint",
            },
            "datasources_contract": {
                "status": "FAIL",
                "reason": "Unreachable endpoint",
            },
            "dashboards_payload": None,
        }

    try:
        login_code, _, _ = _http_get(
            base_url,
            "/login",
            timeout=timeout,
            allow_loopback_http=allow_loopback_http,
        )
    except (ValueError, OSError, urllib.error.URLError):
        login_code = 0

    try:
        unauth_ds_code, _, _ = _http_get(
            base_url,
            "/api/datasources",
            timeout=timeout,
            allow_loopback_http=allow_loopback_http,
        )
    except (ValueError, OSError, urllib.error.URLError):
        unauth_ds_code = 0

    health_ok = (
        health_code == 200
        and health_json.get("database") == "ok"
        and login_code == 200
    )
    endpoint_health = {
        "status": "PASS" if health_ok else "FAIL",
        "http_status": health_code,
        "login_http_status": login_code,
        "database": health_json.get("database", "unknown"),
        "version": health_json.get("version", "unknown"),
        "commit": health_json.get("commit", "unknown"),
        "via": health_hdrs.get("via", ""),
        "reason": (
            "Health and login endpoints returned HTTP 200 with database=ok"
            if health_ok
            else "Endpoint health or login verification failed"
        ),
    }

    unauth_ok = unauth_ds_code in (401, 403)
    unauthenticated_boundary = {
        "status": "PASS" if unauth_ok else "FAIL",
        "unauthenticated_datasources_http_status": unauth_ds_code,
        "reason": (
            f"Unauthenticated /api/datasources rejected with HTTP {unauth_ds_code}"
            if unauth_ok
            else f"Unauthenticated /api/datasources returned unexpected HTTP {unauth_ds_code}"
        ),
    }

    auth_header = _build_auth_header(auth)
    if not auth_header:
        return {
            "target_url": base_url,
            "resolved_ips_redacted": resolved_ips,
            "endpoint_health": endpoint_health,
            "unauthenticated_boundary": unauthenticated_boundary,
            "datasources_contract": {
                "status": "NOT_RUN",
                "required_uids": list(REQUIRED_DATASOURCE_UIDS),
                "reason": "Read-only Grafana API token not provided; authenticated datasource check skipped as NOT_RUN",
            },
            "dashboards_payload": None,
        }

    try:
        ds_code, _, ds_raw = _http_get(
            base_url,
            "/api/datasources",
            auth_header=auth_header,
            timeout=timeout,
            allow_loopback_http=allow_loopback_http,
        )
        ds_list = json.loads(ds_raw.decode("utf-8")) if ds_code == 200 else []
        if not isinstance(ds_list, list):
            ds_list = []
    except (ValueError, OSError, urllib.error.URLError):
        ds_code = 0
        ds_list = []

    found_uids = sorted(
        {
            str(item.get("uid"))
            for item in ds_list
            if isinstance(item, dict) and item.get("uid")
        }
    )
    missing_uids = [
        uid for uid in REQUIRED_DATASOURCE_UIDS if uid not in found_uids
    ]
    ds_ok = ds_code == 200 and not missing_uids
    datasources_contract = {
        "status": "PASS" if ds_ok else "FAIL",
        "http_status": ds_code,
        "required_uids": list(REQUIRED_DATASOURCE_UIDS),
        "found_uids": found_uids,
        "missing_uids": missing_uids,
        "reason": (
            "All required datasource UIDs verified"
            if ds_ok
            else f"Datasource verification failed (HTTP {ds_code}, missing={missing_uids})"
        ),
    }

    dashboards_payload: dict[str, dict[str, Any]] = {}
    for uid in EXPECTED_17_DASHBOARDS:
        try:
            d_code, _, d_raw = _http_get(
                base_url,
                "/api/dashboards/uid/" + urllib.parse.quote(uid, safe=""),
                auth_header=auth_header,
                timeout=timeout,
                allow_loopback_http=allow_loopback_http,
            )
            if d_code == 200:
                parsed_d = json.loads(d_raw.decode("utf-8"))
                if isinstance(parsed_d, dict):
                    dashboards_payload[uid] = parsed_d
        except (ValueError, OSError, urllib.error.URLError):
            continue

    return {
        "target_url": base_url,
        "resolved_ips_redacted": resolved_ips,
        "endpoint_health": endpoint_health,
        "unauthenticated_boundary": unauthenticated_boundary,
        "datasources_contract": datasources_contract,
        "dashboards_payload": dashboards_payload,
    }


def compute_overall_status(checks: dict[str, dict[str, Any]]) -> str:
    """Compute strict fail-closed overall status across all gates."""
    statuses = [c.get("status", "NOT_RUN") for c in checks.values()]
    if any(s not in ALLOWED_STATUSES for s in statuses):
        return "FAIL"
    if any(s == "FAIL" for s in statuses):
        return "FAIL"
    if any(s == "NOT_RUN" for s in statuses):
        return "NOT_RUN"
    return "PASS"


def run_acceptance_probe(config: dict[str, Any]) -> dict[str, Any]:
    """Run the full Observability closure acceptance probe and return sanitized report."""
    now_dt = datetime.now(timezone.utc)
    reference_ts = float(config.get("reference_timestamp_sec", now_dt.timestamp()))
    queried_dt = datetime.fromtimestamp(reference_ts, tz=timezone.utc)

    thresholds = dict(DEFAULT_FRESHNESS_THRESHOLDS)
    if isinstance(config.get("freshness_thresholds"), dict):
        for k in DEFAULT_FRESHNESS_THRESHOLDS:
            if k in config["freshness_thresholds"]:
                thresholds[k] = int(config["freshness_thresholds"][k])

    target_urls: list[str] = config.get("target_urls") or []
    if not target_urls and config.get("target_url"):
        target_urls = [str(config["target_url"])]
    if not target_urls:
        target_urls = [
            "https://observability.svc.plus",
            "https://observability-k3s-v5.svc.plus",
        ]

    allow_loopback_http = bool(config.get("allow_loopback_http", False))
    timeout = int(config.get("timeout", 10))
    auth = config.get("auth")

    endpoint_observations: list[dict[str, Any]] = []
    if config.get("skip_network_probe"):
        endpoint_observations = config.get("mock_endpoint_observations", [])
    else:
        for url in target_urls:
            endpoint_observations.append(
                probe_live_endpoint(
                    url,
                    auth=auth,
                    timeout=timeout,
                    allow_loopback_http=allow_loopback_http,
                )
            )

    if not endpoint_observations:
        endpoint_health_gate = {
            "status": "NOT_RUN",
            "reason": "No endpoint observations collected",
            "targets": [],
        }
        unauth_gate = {
            "status": "NOT_RUN",
            "reason": "No endpoint observations collected",
        }
        datasources_gate = {
            "status": "NOT_RUN",
            "required_uids": list(REQUIRED_DATASOURCE_UIDS),
            "reason": "No endpoint observations collected",
        }
        dashboards_payload = config.get("dashboards_payload")
    else:
        eh_statuses = [
            obs["endpoint_health"]["status"] for obs in endpoint_observations
        ]
        ub_statuses = [
            obs["unauthenticated_boundary"]["status"]
            for obs in endpoint_observations
        ]
        ds_statuses = [
            obs["datasources_contract"]["status"]
            for obs in endpoint_observations
        ]
        endpoint_health_gate = {
            "status": (
                "FAIL"
                if "FAIL" in eh_statuses
                else ("NOT_RUN" if "NOT_RUN" in eh_statuses else "PASS")
            ),
            "targets": [
                {
                    "target_url": obs["target_url"],
                    "resolved_ips_redacted": obs.get("resolved_ips_redacted", []),
                    **obs["endpoint_health"],
                }
                for obs in endpoint_observations
            ],
            "reason": "All target endpoints healthy"
            if all(s == "PASS" for s in eh_statuses)
            else "One or more target endpoints failed health verification",
        }
        unauth_gate = {
            "status": (
                "FAIL"
                if "FAIL" in ub_statuses
                else ("NOT_RUN" if "NOT_RUN" in ub_statuses else "PASS")
            ),
            "targets": [
                {
                    "target_url": obs["target_url"],
                    **obs["unauthenticated_boundary"],
                }
                for obs in endpoint_observations
            ],
            "reason": "Unauthenticated API access properly rejected across targets"
            if all(s == "PASS" for s in ub_statuses)
            else "Unauthenticated boundary check failed",
        }
        datasources_gate = {
            "status": (
                "FAIL"
                if "FAIL" in ds_statuses
                else ("NOT_RUN" if "NOT_RUN" in ds_statuses else "PASS")
            ),
            "required_uids": list(REQUIRED_DATASOURCE_UIDS),
            "targets": [
                {
                    "target_url": obs["target_url"],
                    **obs["datasources_contract"],
                }
                for obs in endpoint_observations
            ],
            "reason": endpoint_observations[0]["datasources_contract"].get(
                "reason", ""
            ),
        }
        dashboards_payload = (
            config.get("dashboards_payload")
            if "dashboards_payload" in config
            else endpoint_observations[0].get("dashboards_payload")
        )

    freshness_obs = config.get("freshness_observations")
    dashboards_gate = evaluate_dashboards(
        dashboards_payload,
        freshness_obs,
        thresholds,
        reference_ts,
    )

    signal_summary = config.get("signal_freshness")
    if isinstance(signal_summary, dict):
        metrics_f = evaluate_freshness(
            signal_summary.get("metrics_latest_ts"),
            reference_ts,
            thresholds["metrics_max_age_seconds"],
        )
        logs_f = evaluate_freshness(
            signal_summary.get("logs_latest_ts"),
            reference_ts,
            thresholds["logs_max_age_seconds"],
        )
        cmdb_f = evaluate_freshness(
            signal_summary.get("cmdb_latest_ts"),
            reference_ts,
            thresholds["cmdb_max_age_seconds"],
        )
        sub_statuses = [metrics_f["status"], logs_f["status"], cmdb_f["status"]]
        sig_status = (
            "FAIL"
            if "FAIL" in sub_statuses
            else ("NOT_RUN" if "NOT_RUN" in sub_statuses else "PASS")
        )
        signal_freshness_gate = {
            "status": sig_status,
            "metrics": metrics_f,
            "logs": logs_f,
            "cmdb": cmdb_f,
            "reason": (
                "Metrics, Logs, and CMDB freshness within thresholds"
                if sig_status == "PASS"
                else "One or more signals exceeded freshness threshold or missing"
            ),
        }
    else:
        signal_freshness_gate = {
            "status": "NOT_RUN",
            "metrics": {
                "status": "NOT_RUN",
                "max_age_seconds": thresholds["metrics_max_age_seconds"],
            },
            "logs": {
                "status": "NOT_RUN",
                "max_age_seconds": thresholds["logs_max_age_seconds"],
            },
            "cmdb": {
                "status": "NOT_RUN",
                "max_age_seconds": thresholds["cmdb_max_age_seconds"],
            },
            "reason": "Authenticated store query credentials not provided; freshness check marked NOT_RUN",
        }

    negative_isolation_gate = evaluate_negative_isolation(
        config.get("negative_isolation_probes")
    )
    continuity_gate = evaluate_continuity_window(
        config.get("continuity_timestamps_sec"),
        window_seconds=thresholds["continuity_window_seconds"],
        max_gap_seconds=thresholds["metrics_max_age_seconds"],
        reference_timestamp_sec=reference_ts,
    )
    recovery_gate = evaluate_recovery_drill(
        config.get("recovery_drill"),
        max_detection_seconds=thresholds["metrics_max_age_seconds"],
    )
    release_rollback_gate = evaluate_release_rollback_contract(
        config.get("release_plan")
    )

    checks = {
        "endpoint_health": endpoint_health_gate,
        "unauthenticated_boundary": unauth_gate,
        "datasources_contract": datasources_gate,
        "dashboards_17_coverage": dashboards_gate,
        "signal_freshness": signal_freshness_gate,
        "negative_isolation": negative_isolation_gate,
        "continuity_30m": continuity_gate,
        "controlled_recovery_drill": recovery_gate,
        "release_and_rollback_contract": release_rollback_gate,
    }
    overall_status = compute_overall_status(checks)

    window_from_dt = datetime.fromtimestamp(
        reference_ts - thresholds["continuity_window_seconds"], tz=timezone.utc
    )
    collected_at = config.get("collected_at") or queried_dt.isoformat()
    ingested_at = config.get("ingested_at") or "NOT_RUN"

    report = {
        "schema_version": SCHEMA_VERSION,
        "environment": config.get("environment", "prod"),
        "overall_status": overall_status,
        "query_window": {
            "from": window_from_dt.isoformat(),
            "to": queried_dt.isoformat(),
            "window_seconds": thresholds["continuity_window_seconds"],
        },
        "timestamps": {
            "collected_at": collected_at,
            "ingested_at": ingested_at,
            "queried_at": queried_dt.isoformat(),
        },
        "freshness_thresholds": thresholds,
        "checks": checks,
    }
    return redact_sensitive(report)


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a concise, human/AI-reviewable Markdown acceptance evidence report."""
    checks = report.get("checks", {})
    thresholds = report.get("freshness_thresholds", DEFAULT_FRESHNESS_THRESHOLDS)
    timestamps = report.get("timestamps", {})
    window = report.get("query_window", {})

    lines = [
        "# Observability 数据闭环只读验收报告 (OBS-05)",
        "",
        f"- **Schema 版本**：`{report.get('schema_version')}`",
        f"- **目标环境**：`{report.get('environment')}`",
        f"- **总体门禁状态**：**`{report.get('overall_status')}`**（含任何 `FAIL` 则为 `FAIL`；无 `FAIL` 但含未授权/缺凭据项则严格标 `NOT_RUN`，不伪造 `PASS`）",
        f"- **查询窗口（UTC）**：`{window.get('from')}` → `{window.get('to')}`（`{window.get('window_seconds')}s`）",
        f"- **采集 / 入库 / 查询时间（UTC）**：`{timestamps.get('collected_at')}` / `{timestamps.get('ingested_at')}` / `{timestamps.get('queried_at')}`",
        f"- **固定新鲜度门槛**：Metrics `<= {thresholds.get('metrics_max_age_seconds')}s`（3 个采样周期） | Logs `<= {thresholds.get('logs_max_age_seconds')}s`（5 分钟） | CMDB `<= {thresholds.get('cmdb_max_age_seconds')}s`",
        "",
        "## 1. 核心门禁汇总",
        "",
        "| 检查项 ID | 状态 | 说明 |",
        "|---|---|---|",
    ]
    for check_id, payload in checks.items():
        lines.append(
            f"| `{check_id}` | `{payload.get('status')}` | {payload.get('reason', '')} |"
        )

    lines += [
        "",
        "## 2. 端点健康与版本核验",
        "",
        "| 目标站点 | 解析 IP（脱敏） | `/api/health` | `/login` | 数据库状态 | Grafana 版本 | 未认证 `/api/datasources` |",
        "|---|---|---|---|---|---|---|",
    ]
    unauth_by_url = {
        t.get("target_url"): t.get("unauthenticated_datasources_http_status")
        for t in checks.get("unauthenticated_boundary", {}).get("targets", [])
    }
    for target in checks.get("endpoint_health", {}).get("targets", []):
        url = target.get("target_url", "")
        ips = ", ".join(target.get("resolved_ips_redacted", [])) or "N/A"
        lines.append(
            f"| `{url}` | `{ips}` | HTTP `{target.get('http_status')}` | HTTP `{target.get('login_http_status')}` | `{target.get('database')}` | `{target.get('version')}` (`{str(target.get('commit', ''))[:10]}`) | HTTP `{unauth_by_url.get(url, 'N/A')}` |"
        )

    lines += [
        "",
        "## 3. 17 个 Dashboard 覆盖与新鲜度基线",
        "",
        "| Dashboard UID | 标题 | 来源类别 | 信号类型 | 状态 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    dashboards = checks.get("dashboards_17_coverage", {}).get("dashboards", {})
    for uid, d_info in dashboards.items():
        lines.append(
            f"| `{uid}` | {d_info.get('expected_title')} | `{d_info.get('origin')}` | `{d_info.get('signal')}` | `{d_info.get('status')}` | {d_info.get('reason', '')} |"
        )

    rr = checks.get("release_and_rollback_contract", {})
    lines += [
        "",
        "## 4. 分阶段发布与回滚门禁检查清单",
        "",
        f"- **门禁状态**：`{rr.get('status')}`",
        f"- **回滚策略**：{rr.get('rollback_mechanism')}",
        "- **发布依赖链**：",
    ]
    for step in rr.get("dependency_chain", []):
        lines.append(f"  1. {step}")

    return "\n".join(lines) + "\n"


def write_evidence_bundle(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write sanitized evidence.json, report.zh.md, and manifest.json to output_dir."""
    out_path = Path(output_dir)
    if out_path.is_symlink():
        raise ValueError("Symlink output directory is forbidden")
    out_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_path, 0o700)

    evidence_file = out_path / "evidence.json"
    markdown_file = out_path / "report.zh.md"
    manifest_file = out_path / "manifest.json"

    evidence_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_file.write_text(render_markdown_report(report), encoding="utf-8")

    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((evidence_file, markdown_file), key=lambda x: x.name)
    }
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for p in (evidence_file, markdown_file, manifest_file):
        p.chmod(0o600)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Observability data-closure acceptance probe (OBS-05)"
    )
    parser.add_argument(
        "--target-url",
        action="append",
        dest="target_urls",
        help="Target HTTPS Grafana URL (may be specified multiple times)",
    )
    parser.add_argument(
        "--environment",
        default="prod",
        choices=("sit", "uat", "prod"),
        help="Target environment name",
    )
    parser.add_argument(
        "--config-json",
        help="Optional path to JSON input configuration / observation fixture",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional directory to write evidence.json, report.zh.md, and manifest.json",
    )
    parser.add_argument(
        "--metrics-max-age",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLDS["metrics_max_age_seconds"],
    )
    parser.add_argument(
        "--logs-max-age",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLDS["logs_max_age_seconds"],
    )
    parser.add_argument(
        "--cmdb-max-age",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLDS["cmdb_max_age_seconds"],
    )
    parser.add_argument(
        "--continuity-window",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLDS["continuity_window_seconds"],
    )
    parser.add_argument(
        "--allow-loopback-http",
        action="store_true",
        help="Allow http://127.0.0.1 or http://localhost for local delegated probes/tests",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless overall_status is strictly PASS (blocks NOT_RUN and FAIL)",
    )
    args = parser.parse_args(argv)

    config: dict[str, Any] = {}
    if args.config_json:
        config = json.loads(Path(args.config_json).read_text(encoding="utf-8"))
    if args.target_urls:
        config["target_urls"] = args.target_urls
    config["environment"] = args.environment
    config["allow_loopback_http"] = bool(
        args.allow_loopback_http or config.get("allow_loopback_http", False)
    )
    thresholds = dict(config.get("freshness_thresholds") or {})
    thresholds.setdefault("metrics_max_age_seconds", args.metrics_max_age)
    thresholds.setdefault("logs_max_age_seconds", args.logs_max_age)
    thresholds.setdefault("cmdb_max_age_seconds", args.cmdb_max_age)
    thresholds.setdefault("continuity_window_seconds", args.continuity_window)
    config["freshness_thresholds"] = thresholds

    token_env = os.environ.get("OBSERVABILITY_GRAFANA_API_TOKEN", "").strip()
    if token_env and "auth" not in config:
        config["auth"] = {"token": token_env}

    report = run_acceptance_probe(config)
    if args.output_dir:
        write_evidence_bundle(report, args.output_dir)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["overall_status"] == "FAIL":
        return 2
    if args.require_pass and report["overall_status"] != "PASS":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
