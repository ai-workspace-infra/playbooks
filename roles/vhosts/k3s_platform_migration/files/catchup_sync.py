#!/usr/bin/env python3
"""Second-level incremental PostgreSQL catch-up sync over WireGuard with clean schema reset.

Replaces /tmp/final_catchup_and_verify.py with GitOps-driven host/table declarations
and Vault KV SSH authentication.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

from audit_business_tables import audit_tables
from common import (
    KUBE_RESOLVE_PREFIX,
    HostConnection,
    build_manifest_arg_parser,
    detect_wireguard_ip,
    log,
    parse_host_manifest,
    parse_tables_manifest,
)


def run_catchup(
    source_cfg,
    target_cfg,
    db_cfg,
    allow_drift=None,
    transfer_mode="wireguard",
    keep_dump=False,
    dry_run=False,
    emit_json=False,
):
    """Dump source DB, transfer to target, reset schemas cleanly, restore, and audit."""
    t0 = time.time()
    ns = db_cfg["namespace"]
    pod = db_cfg["postgres_pod"]
    admin = db_cfg["postgres_admin"]
    db_name = db_cfg["database_name"]
    remote_dump = f"/tmp/{db_name}_cutover.dump"

    with HostConnection(source_cfg) as src_conn, HostConnection(target_cfg) as tgt_conn:
        wg_target_ip = detect_wireguard_ip(tgt_conn)
        log(f"Discovered target WireGuard IP: {wg_target_ip} (transfer_mode={transfer_mode})")

        if dry_run:
            summary = {
                "status": "DRY_RUN",
                "source_host": source_cfg["public_ipv4"],
                "target_host": target_cfg["public_ipv4"],
                "wireguard_target_ip": wg_target_ip,
                "database": db_name,
                "tables_declared": len(db_cfg["tables"]),
            }
            if emit_json:
                print(json.dumps(summary, indent=2))
            else:
                log(f"Dry-run OK: {summary}")
            return summary

        # 1. Dump database on source
        dump_cmd = (
            f"{KUBE_RESOLVE_PREFIX}; "
            f"$KUBE_CMD exec -n {ns} {pod} -- "
            f"sh -c 'PGPASSWORD=\"$POSTGRES_PASSWORD\" pg_dump -U {admin} -Fc -Z 4 -d {db_name}' "
            f"> {remote_dump}"
        )
        log(f"Dumping '{db_name}' from source {source_cfg['public_ipv4']}...")
        src_conn.run(dump_cmd, timeout=300)

        # 2. Transfer dump to target
        if transfer_mode == "wireguard":
            tgt_user = target_cfg["ansible_user"]
            scp_cmd = (
                f"scp -o BatchMode=yes -o StrictHostKeyChecking=no "
                f"{remote_dump} {tgt_user}@{wg_target_ip}:{remote_dump}"
            )
            log(f"Transferring dump over WireGuard -> {wg_target_ip}:{remote_dump}...")
            src_conn.run(scp_cmd, timeout=300)
        elif transfer_mode == "controller":
            local_tmp = tempfile.NamedTemporaryFile(delete=False)
            local_tmp.close()
            try:
                log("Relaying dump through controller using Vault-authenticated SCP...")
                src_scp = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
                if src_conn._key_file:
                    src_scp += ["-i", src_conn._key_file]
                src_scp += [f"{src_conn.user}@{src_conn.host}:{remote_dump}", local_tmp.name]
                subprocess.run(src_scp, check=True)

                tgt_scp = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
                if tgt_conn._key_file:
                    tgt_scp += ["-i", tgt_conn._key_file]
                tgt_scp += [local_tmp.name, f"{tgt_conn.user}@{tgt_conn.host}:{remote_dump}"]
                subprocess.run(tgt_scp, check=True)
            finally:
                if os.path.exists(local_tmp.name):
                    os.unlink(local_tmp.name)
        else:
            raise ValueError(f"Unsupported transfer_mode: {transfer_mode}")

        transfer_elapsed = round(time.time() - t0, 2)
        log(f"Dump & transfer completed in {transfer_elapsed}s")

        # 3. Clean target schemas before pg_restore to prevent duplicate index errors
        reset_sql = (
            "DROP SCHEMA public CASCADE;\n"
            "CREATE SCHEMA public;\n"
            f"GRANT ALL ON SCHEMA public TO {admin};\n"
            "GRANT ALL ON SCHEMA public TO public;\n"
            "DROP SCHEMA IF EXISTS accounts_migration_api CASCADE;\n"
            "DROP SCHEMA IF EXISTS pgmq CASCADE;\n"
            "DROP SCHEMA IF EXISTS auth CASCADE;\n"
        )
        reset_inner = (
            f"sh -c 'PGPASSWORD=\"$POSTGRES_PASSWORD\" psql -U {admin} -d {db_name}'"
        )
        log(f"Resetting schemas on target {target_cfg['public_ipv4']}...")
        tgt_conn.kubectl_exec(
            ns, pod, reset_inner, timeout=120, stdin_data=reset_sql
        )

        # 4. Restore dump on target
        restore_cmd = (
            f"{KUBE_RESOLVE_PREFIX}; "
            f"$KUBE_CMD exec -i -n {ns} {pod} -- "
            f"sh -c 'PGPASSWORD=\"$POSTGRES_PASSWORD\" pg_restore --no-owner -d {db_name}' < {remote_dump}"
        )
        log("Restoring dump onto target PostgreSQL...")
        tgt_conn.run(restore_cmd, timeout=300)

        if not keep_dump:
            src_conn.run(f"rm -f {remote_dump}", check=False)
            tgt_conn.run(f"rm -f {remote_dump}", check=False)

    total_elapsed = round(time.time() - t0, 2)
    log(f"Restore finished in {total_elapsed}s; running {len(db_cfg['tables'])}-table parity audit...")
    audit_res = audit_tables(
        source_cfg,
        target_cfg,
        db_cfg,
        allow_drift=allow_drift,
        target_only=False,
        emit_json=False,
    )

    summary = {
        "status": audit_res["status"],
        "transfer_elapsed_seconds": transfer_elapsed,
        "total_elapsed_seconds": total_elapsed,
        "wireguard_target_ip": wg_target_ip,
        "audit": audit_res,
    }
    if emit_json:
        print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = build_manifest_arg_parser(
        "Run second-level incremental PostgreSQL catch-up sync and 42-table audit",
        __file__,
    )
    parser.add_argument(
        "--allow-drift",
        default="billing_ledger,traffic_minute_buckets",
        help="Comma-separated live streaming tables allowed to drift slightly while source takes traffic",
    )
    parser.add_argument(
        "--transfer-mode",
        choices=["wireguard", "controller"],
        default="wireguard",
        help="Transfer dump directly over WireGuard or relay via controller",
    )
    parser.add_argument(
        "--keep-dump",
        action="store_true",
        help="Retain temporary dump files on source/target after restore",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connectivity and WireGuard discovery without modifying target database",
    )
    args = parser.parse_args()

    source_cfg = parse_host_manifest(args.source_manifest)
    target_cfg = parse_host_manifest(args.target_manifest)
    db_cfg = parse_tables_manifest(args.tables_manifest)
    allow_drift = [t.strip() for t in args.allow_drift.split(",") if t.strip()]

    res = run_catchup(
        source_cfg,
        target_cfg,
        db_cfg,
        allow_drift=allow_drift,
        transfer_mode=args.transfer_mode,
        keep_dump=args.keep_dump,
        dry_run=args.dry_run,
        emit_json=args.json,
    )
    sys.exit(0 if res["status"] in ("PASSED", "DRY_RUN") else 1)


if __name__ == "__main__":
    main()
