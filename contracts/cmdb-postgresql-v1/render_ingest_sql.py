#!/usr/bin/env python3
"""Persist a validated IaC observation artifact without changing cloud facts.

Produces one transactional PostgreSQL script for the cmdb_writer identity.
Credentials and database execution belong to the Playbooks caller.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


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


def render(document):
    run = document["run"]
    observations = document["observations"]
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
    for item in observations:
        columns = ["provider", "account_ref", "project_ref", "resource_kind", "native_resource_id", "canonical_id", "region", "scope", "name", "provider_state", "provider_state_raw", "public_endpoint", "private_endpoint", "source", "last_seen_at", "last_successful_run_id", "attributes"]
        values = [literal(item.get(key, "")) for key in columns[:11]]
        values += [literal(item.get("public_endpoint")), literal(item.get("private_endpoint")), literal(item["source"]), literal(item["observed_at"]), run_id, json_literal(item.get("attributes", {}))]
        updates = ",".join(f"{key}=EXCLUDED.{key}" for key in ["region", "scope", "name", "provider_state", "provider_state_raw", "public_endpoint", "private_endpoint", "source", "last_seen_at", "last_successful_run_id", "attributes"])
        statements.append(f"INSERT INTO cmdb.resources ({','.join(columns)}) VALUES ({','.join(values)}) ON CONFLICT (canonical_id) DO UPDATE SET {updates},lifecycle='present' WHERE cmdb.resources.last_seen_at <= EXCLUDED.last_seen_at;")
        canon = literal(item["canonical_id"])
        statements.append("INSERT INTO cmdb.resource_observations (run_id,canonical_id,observed_at,provider_state,provider_state_raw,attributes) VALUES (" + ",".join([run_id, canon, literal(item["observed_at"]), literal(item["provider_state"]), literal(item.get("provider_state_raw", "")), json_literal(item.get("attributes", {}))]) + ") ON CONFLICT (run_id,canonical_id) DO NOTHING;")
        payload_hash = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        statements.append("INSERT INTO cmdb.raw_snapshots (run_id,canonical_id,payload_version,payload,payload_sha256) SELECT " + ",".join([run_id, canon, literal(document["schema_version"]), json_literal(item), literal(payload_hash)]) + f" WHERE NOT EXISTS (SELECT 1 FROM cmdb.raw_snapshots WHERE run_id={run_id} AND canonical_id={canon});")
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
