#!/usr/bin/env python3
"""Persist a validated IaC observation artifact without changing cloud facts.

Produces one transactional PostgreSQL script for the cmdb_writer identity.
Credentials and database execution belong to the Playbooks caller.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import quote


SERVERLESS_KINDS = {
    "serverless",
    "serverless_service",
    "serverless_revision",
    "serverless_worker",
    "serverless_pages",
    "cloud_run",
    "cloudflare_worker",
    "cloudflare_pages",
    "supabase",
    "managed_database",
}
PROVIDER_ALIASES = {
    "gcp-cloud": "gcp",
    "aws-cloud": "aws",
    "vultr-vps": "vultr",
    "akamai-cloud": "akamai",
    "supabase-cloud": "supabase",
    "ali-cloud": "alicloud",
    "azure-cloud": "azure",
}


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if "\x00" in text:
        raise ValueError("NUL is not allowed in a PostgreSQL value")
    return "'" + text.replace("'", "''") + "'"


def json_literal(value):
    return literal(json.dumps(value, sort_keys=True, separators=(",", ":"))) + "::jsonb"


def _parse_ts(text: str) -> datetime:
    parsed = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _normalize_observation(item: dict, run: dict) -> dict:
    run_scope = str(run.get("scope") or "unknown").strip()
    item_scope = str(item.get("scope") or run_scope).strip()
    if run_scope != "unknown" and item_scope != run_scope:
        raise ValueError(f"observation scope {item_scope!r} conflicts with run scope {run_scope!r}")
    item_env = str(item.get("environment") or item_scope).strip()
    if item_env != item_scope:
        raise ValueError(f"observation environment {item_env!r} conflicts with scope {item_scope!r}")

    raw_provider = str(item.get("provider") or "").strip().lower()
    provider = PROVIDER_ALIASES.get(raw_provider, raw_provider)
    if not provider:
        raise ValueError("observation provider is required")

    project_ref = str(item.get("project_ref") or run.get("project_ref") or "").strip()
    if run.get("project_ref") and project_ref and project_ref != str(run["project_ref"]).strip():
        raise ValueError("observation project_ref conflicts with run.project_ref")
    account_ref = str(item.get("account_ref") or project_ref or run.get("account_ref") or "").strip()

    attrs = dict(item.get("attributes") or {})
    zone = str(item.get("zone") or attrs.get("zone") or "").strip()
    zone_region = zone.rsplit("-", 1)[0] if "-" in zone and len(zone.split("-")) >= 3 else ""
    explicit_region = str(item.get("region") or "").strip()
    if explicit_region and zone_region and explicit_region != zone_region:
        raise ValueError(f"observation region {explicit_region!r} conflicts with zone {zone!r}")
    run_region = str(run.get("region_ref") or "").strip()
    region = explicit_region or zone_region or (
        run_region if run_region not in ("", "all-zones", "multi-region") else ""
    )
    if not region and provider == "cloudflare":
        region = "global"
    if not region:
        raise ValueError("observation region is required")
    if run_region and run_region not in ("all-zones", "global", "multi-region") and region != run_region:
        raise ValueError(f"observation region {region!r} conflicts with run.region_ref {run_region!r}")

    raw_kind = str(item.get("resource_kind") or "compute").strip().lower()
    explicit_form = str(item.get("runtime_form") or "").strip().lower()
    is_serverless = raw_kind in SERVERLESS_KINDS or provider in {"cloudflare", "supabase"}
    is_k3s = (
        raw_kind in {"k3s_node", "k3s", "k3s_server", "k3s_agent"}
        or str(attrs.get("deployment_mode") or "").strip().lower() == "kubernetes"
        or bool(str(attrs.get("k8s_cluster_name") or item.get("cluster") or "").strip())
    )
    runtime_form = explicit_form or ("serverless" if is_serverless else ("k3s_node" if is_k3s else "vm"))
    if runtime_form == "serverless" and raw_kind in {"compute", "vm", "k3s_node"}:
        raise ValueError("serverless runtime_form conflicts with compute resource_kind")
    if runtime_form in {"vm", "k3s_node"} and raw_kind in SERVERLESS_KINDS:
        raise ValueError(f"{runtime_form} runtime_form conflicts with serverless resource_kind")

    pub_ep = item.get("public_endpoint")
    priv_ep = item.get("private_endpoint")
    raw_ssh = item.get("ssh_host")
    raw_pip = item.get("primary_ip")
    if runtime_form == "serverless":
        if raw_ssh not in (None, "") or raw_pip not in (None, ""):
            raise ValueError("serverless observation cannot define ssh_host or primary_ip")
        if priv_ep not in (None, "") and raw_kind != "managed_database":
            raise ValueError("serverless observation cannot define private_endpoint")
        ssh_host = None
        primary_ip = None
        priv_ep = priv_ep if raw_kind == "managed_database" else None
    else:
        primary_ip = raw_pip or pub_ep or priv_ep
        ssh_host = raw_ssh or pub_ep or priv_ep

    native_id = str(item.get("native_resource_id") or "").strip()
    if not native_id:
        raise ValueError("observation native_resource_id is required")

    acct_or_proj = project_ref or account_ref or "default"
    rkey = str(item.get("resource_key") or f"{provider}:{acct_or_proj}:{region}:{native_id}")
    norm_kind = "compute" if raw_kind in {"vm", "k3s_node"} else (
        "serverless_service" if raw_kind == "serverless" else raw_kind
    )
    cid = str(item.get("canonical_id") or (
        "cmdb://" + "/".join(
            quote(part, safe="") or "_"
            for part in (provider, account_ref, project_ref, norm_kind, native_id)
        )
    ))

    obs_at = str(item["observed_at"]).strip()
    last_obs_at = str(item.get("last_observed_at") or obs_at).strip()
    _parse_ts(obs_at)
    _parse_ts(last_obs_at)

    obs_status = "active" if run.get("outcome") == "success" else "collection_failed"
    attrs.update(
        {
            "resource_key": rkey,
            "runtime_form": runtime_form,
            "environment": item_env,
            "service": str(item.get("service") or attrs.get("service") or item.get("name") or ""),
            "cluster": str(item.get("cluster") or attrs.get("cluster") or ""),
            "namespace": str(item.get("namespace") or attrs.get("namespace") or ""),
            "node": str(item.get("node") or attrs.get("node") or (item.get("name") or "" if runtime_form != "serverless" else "")),
            "ssh_host": ssh_host,
            "primary_ip": primary_ip,
            "last_observed_at": last_obs_at,
            "observation_status": obs_status,
        }
    )

    normalized = dict(item)
    normalized.update(
        {
            "provider": provider,
            "account_ref": account_ref,
            "project_ref": project_ref,
            "resource_kind": norm_kind,
            "runtime_form": runtime_form,
            "native_resource_id": native_id,
            "canonical_id": cid,
            "resource_key": rkey,
            "region": region,
            "scope": item_scope,
            "environment": item_env,
            "public_endpoint": pub_ep,
            "private_endpoint": priv_ep,
            "ssh_host": ssh_host,
            "primary_ip": primary_ip,
            "observed_at": obs_at,
            "last_observed_at": last_obs_at,
            "attributes": attrs,
        }
    )
    return normalized


def _deduplicate_observations(observations: list[dict], run: dict) -> list[dict]:
    by_key: dict[str, dict] = {}
    for raw in observations:
        norm = _normalize_observation(raw, run)
        rkey = norm["resource_key"]
        prev = by_key.get(rkey)
        if prev is not None:
            if prev["scope"] != norm["scope"] or prev["runtime_form"] != norm["runtime_form"]:
                raise ValueError(f"conflicting duplicate observation for {rkey}")
            if _parse_ts(norm["last_observed_at"]) < _parse_ts(prev["last_observed_at"]):
                continue
        by_key[rkey] = norm
    return list(by_key.values())


def render(document):
    run = document["run"]
    observations = _deduplicate_observations(list(document.get("observations") or []), run)
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    run_id = literal(run["run_id"])
    scope_key = json.dumps([run["collector"], run["scope"], run.get("account_ref", ""), run.get("project_ref", ""), run.get("region_ref", ""), run.get("resource_kind", "unknown")])
    statements = [
        "\\set ON_ERROR_STOP on", "BEGIN;", "SET LOCAL standard_conforming_strings = on;",
        "SET LOCAL statement_timeout = '30s';", "SET LOCAL lock_timeout = '10s';",
        f"SELECT pg_advisory_xact_lock(hashtextextended({literal(scope_key)}, 0));",
        # A repeated immutable run may replay, but may not change its payload.
        f"SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM cmdb.collection_runs WHERE run_id={run_id} AND receipt->>'envelope_sha256' IS DISTINCT FROM {literal(digest)}) THEN 0 ELSE 1 END;",
    ]
    columns = ["run_id", "collector", "owner_sha", "scope", "account_ref", "project_ref", "region_ref", "resource_kind", "started_at", "completed_at", "outcome", "scope_complete", "resources_seen", "error_class", "receipt"]
    values = [literal(run.get(key, "")) for key in columns[:7]]
    values += [literal(run.get("resource_kind", "unknown")), literal(run["started_at"]), literal(run["completed_at"]), literal(run["outcome"]), literal(run["scope_complete"]), literal(len(observations)), literal(run.get("error_class")), json_literal({"envelope_sha256": digest})]
    statements.append(f"INSERT INTO cmdb.collection_runs ({','.join(columns)}) VALUES ({','.join(values)}) ON CONFLICT (run_id) DO NOTHING;")

    seen_canonicals: list[str] = []
    for item in observations:
        columns = ["provider", "account_ref", "project_ref", "resource_kind", "native_resource_id", "canonical_id", "region", "scope", "name", "provider_state", "provider_state_raw", "public_endpoint", "private_endpoint", "source", "last_seen_at", "last_successful_run_id", "attributes"]
        values = [literal(item.get(key, "")) for key in columns[:11]]
        values += [literal(item.get("public_endpoint")), literal(item.get("private_endpoint")), literal(item["source"]), literal(item["last_observed_at"]), run_id, json_literal(item.get("attributes", {}))]
        updates = ",".join(f"{key}=EXCLUDED.{key}" for key in ["region", "scope", "name", "provider_state", "provider_state_raw", "public_endpoint", "private_endpoint", "source", "last_seen_at", "last_successful_run_id", "attributes"])
        canon = literal(item["canonical_id"])
        seen_canonicals.append(canon)
        rkey_lit = literal(item["resource_key"])
        # Retire any legacy duplicate rows that represent the same authoritative resource_key under a different canonical_id
        statements.append(
            f"UPDATE cmdb.resources SET lifecycle='retired', attributes=jsonb_set(cmdb.resources.attributes, '{{observation_status}}', '\"deleted\"'::jsonb, true) "
            f"WHERE canonical_id <> {canon} AND lifecycle = 'present' AND ("
            f"cmdb.resources.attributes->>'resource_key' = {rkey_lit} OR ("
            f"provider = {literal(item['provider'])} AND project_ref = {literal(item['project_ref'])} AND region = {literal(item['region'])} AND native_resource_id = {literal(item['native_resource_id'])}"
            f"));"
        )
        statements.append(f"INSERT INTO cmdb.resources ({','.join(columns)}) VALUES ({','.join(values)}) ON CONFLICT (canonical_id) DO UPDATE SET {updates},lifecycle='present' WHERE cmdb.resources.last_seen_at <= EXCLUDED.last_seen_at;")
        statements.append("INSERT INTO cmdb.resource_observations (run_id,canonical_id,observed_at,provider_state,provider_state_raw,attributes) VALUES (" + ",".join([run_id, canon, literal(item["last_observed_at"]), literal(item["provider_state"]), literal(item.get("provider_state_raw", "")), json_literal(item.get("attributes", {}))]) + ") ON CONFLICT (run_id,canonical_id) DO NOTHING;")
        payload_hash = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        statements.append("INSERT INTO cmdb.raw_snapshots (run_id,canonical_id,payload_version,payload,payload_sha256) SELECT " + ",".join([run_id, canon, literal(document["schema_version"]), json_literal(item), literal(payload_hash)]) + f" WHERE NOT EXISTS (SELECT 1 FROM cmdb.raw_snapshots WHERE run_id={run_id} AND canonical_id={canon});")

    # Complete scope reconciliation: only mark missing resources in this exact scope as deletion_confirmed
    # when the collection run succeeded and proved scope_complete = True.
    # Partial or failed runs NEVER mark existing assets as deleted.
    if run.get("outcome") == "success" and run.get("scope_complete") is True:
        collector_name = str(run.get("collector") or "")
        run_provider = PROVIDER_ALIASES.get(
            str(run.get("provider") or (collector_name.split("-", 1)[0] if "-" in collector_name else "")).strip().lower(),
            str(run.get("provider") or (collector_name.split("-", 1)[0] if "-" in collector_name else "")).strip().lower(),
        )
        run_project = str(run.get("project_ref") or "").strip()
        run_scope = str(run.get("scope") or "").strip()
        run_kind = str(run.get("resource_kind") or "").strip()
        run_region = str(run.get("region_ref") or "").strip()

        scope_predicates = [
            "lifecycle = 'present'",
            f"scope = {literal(run_scope)}",
            f"last_seen_at <= {literal(run['completed_at'])}",
        ]
        if run_provider:
            scope_predicates.append(f"provider = {literal(run_provider)}")
        if run_project:
            scope_predicates.append(f"project_ref = {literal(run_project)}")
        if run_kind and run_kind != "unknown":
            scope_predicates.append(f"resource_kind = {literal(run_kind)}")
        if run_region and run_region not in ("all-zones", "global", "multi-region"):
            scope_predicates.append(f"region = {literal(run_region)}")
        if seen_canonicals:
            scope_predicates.append(f"canonical_id NOT IN ({','.join(seen_canonicals)})")

        statements.append(
            "UPDATE cmdb.resources SET lifecycle='deletion_confirmed', "
            "attributes=jsonb_set(cmdb.resources.attributes, '{observation_status}', '\"deleted\"'::jsonb, true) "
            f"WHERE {' AND '.join(scope_predicates)};"
        )

    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-dir", type=Path, required=True, help="Fixed-version IaC scripts directory")
    parser.add_argument("envelope", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("cmdb_observations_v1", args.contract_dir / "cmdb_observations_v1.py")
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    print(render(contract.validate_envelope(json.loads(args.envelope.read_text()))), end="")


if __name__ == "__main__":
    main()
