#!/usr/bin/env python3
"""Audit business table row-count parity between source and target K3s PostgreSQL clusters.

Replaces ad-hoc /tmp/audit_42_tables.py with dynamic GitOps manifest loading and
Vault KV SSH authentication.
"""

import json
import sys

from common import (
    HostConnection,
    build_manifest_arg_parser,
    parse_host_manifest,
    parse_tables_manifest,
    validate_sql_identifier,
)


def build_count_sql(tables):
    """Build a single deterministic UNION ALL query across validated table names."""
    parts = []
    for table in tables:
        safe = validate_sql_identifier(table)
        parts.append(f"SELECT '{safe}', count(*) FROM public.\"{safe}\"")
    return " UNION ALL ".join(parts) + ";"


def fetch_table_counts(host_cfg, db_cfg, tables, timeout=60):
    """Query PostgreSQL pod on the specified cluster and return {table: count}."""
    sql = build_count_sql(tables)
    inner = (
        f"sh -c 'PGPASSWORD=\"$POSTGRES_PASSWORD\" psql -U {db_cfg['postgres_admin']} "
        f"-d {db_cfg['database_name']} -t -A'"
    )
    with HostConnection(host_cfg) as conn:
        out, _, _ = conn.kubectl_exec(
            db_cfg["namespace"],
            db_cfg["postgres_pod"],
            inner,
            timeout=timeout,
            stdin_data=sql + "\n",
        )
    counts = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        name, raw_count = line.split("|", 1)
        counts[name.strip()] = int(raw_count.strip())
    return counts


def audit_tables(
    source_cfg,
    target_cfg,
    db_cfg,
    allow_drift=None,
    target_only=False,
    emit_json=False,
):
    """Compare declared business table counts and return structured audit summary."""
    allow_drift = set(allow_drift or [])
    tables = db_cfg["tables"]

    target_counts = fetch_table_counts(target_cfg, db_cfg, tables)
    source_counts = {} if target_only else fetch_table_counts(source_cfg, db_cfg, tables)

    rows = []
    mismatches = []
    drifted = []

    for table in sorted(tables):
        tgt_val = target_counts.get(table)
        if target_only:
            status = "PRESENT" if tgt_val is not None else "MISSING"
            if tgt_val is None:
                mismatches.append(table)
            rows.append({"table": table, "target": tgt_val, "status": status})
            continue

        src_val = source_counts.get(table)
        if src_val is None or tgt_val is None:
            status = "MISSING"
            mismatches.append(table)
        elif src_val == tgt_val:
            status = "MATCH"
        elif table in allow_drift:
            status = "DRIFT_ALLOWED"
            drifted.append(table)
        else:
            status = "MISMATCH"
            mismatches.append(table)

        rows.append(
            {
                "table": table,
                "source": src_val,
                "target": tgt_val,
                "delta": (tgt_val - src_val) if (src_val is not None and tgt_val is not None) else None,
                "status": status,
            }
        )

    passed = len(mismatches) == 0 and len(target_counts) == len(tables)
    summary = {
        "status": "PASSED" if passed else "FAILED",
        "expected_table_count": len(tables),
        "target_table_count": len(target_counts),
        "source_table_count": len(source_counts) if not target_only else None,
        "target_only": target_only,
        "mismatches": mismatches,
        "drifted_allowed": drifted,
        "tables": rows,
    }

    if emit_json:
        print(json.dumps(summary, indent=2))
    else:
        if target_only:
            print(f"Total audited tables (target-only): {len(target_counts)}/{len(tables)}")
            for row in rows:
                print(f"  {row['table']:<38} | Tgt: {str(row['target']):<8} | {row['status']}")
        else:
            print(
                f"Total tables: Source={len(source_counts)}, "
                f"Target={len(target_counts)} (Declared={len(tables)})"
            )
            for row in rows:
                print(
                    f"  {row['table']:<38} | Src: {str(row['source']):<8} | "
                    f"Tgt: {str(row['target']):<8} | {row['status']}"
                )
        verdict = (
            f"100% PARITY ACROSS ALL {len(tables)} TABLES"
            if passed
            else f"DISCREPANCY DETECTED ({len(mismatches)} mismatch(es))"
        )
        print(f"\nAudit Result: {verdict}")

    return summary


def main():
    parser = build_manifest_arg_parser(
        "Audit declared business PostgreSQL tables between source and target K3s clusters",
        __file__,
    )
    parser.add_argument(
        "--allow-drift",
        default="",
        help="Comma-separated table names allowed to lag while source accepts live traffic",
    )
    parser.add_argument(
        "--target-only",
        action="store_true",
        help="Inspect target cluster only (useful after source workloads are scaled down)",
    )
    args = parser.parse_args()

    source_cfg = parse_host_manifest(args.source_manifest)
    target_cfg = parse_host_manifest(args.target_manifest)
    db_cfg = parse_tables_manifest(args.tables_manifest)
    allow_drift = [t.strip() for t in args.allow_drift.split(",") if t.strip()]

    result = audit_tables(
        source_cfg,
        target_cfg,
        db_cfg,
        allow_drift=allow_drift,
        target_only=args.target_only,
        emit_json=args.json,
    )
    sys.exit(0 if result["status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
