#!/usr/bin/env python3
"""Private UAT repair sentinels, serving identity, and aggregate evidence only."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import urlopen

TAG = "daily-build-2026.10.04-r3"
SOURCE = "c2c343fe2c91bb31e7f7f9b4fa60512a66e2b4c9"
DIGEST = "sha256:4d69adfd3c71eced9ddf711bebed6a65e7ca1dca21bd572901c11cf3d02f0274"
FINANCE = {"finance_invoices", "finance_payments", "finance_refunds",
           "finance_operations", "finance_operation_events"}
TRIGGERS = {"finance_invoice_subscription_owner", "finance_invoices_append_only",
            "finance_invoices_no_truncate", "finance_payments_append_only",
            "finance_payments_no_truncate", "finance_refunds_append_only",
            "finance_refunds_no_truncate", "finance_operations_no_delete",
            "finance_operations_no_truncate", "finance_operation_events_append_only",
            "finance_operation_events_no_truncate"}


class Blocked(Exception):
    pass


def connection(raw):
    u = urlsplit(raw)
    ref = os.environ.get("PROJECT_REF", "")
    modes = parse_qs(u.query, keep_blank_values=True).get("sslmode", [])
    if not (re.fullmatch(r"[a-z0-9]{20}", ref) and u.scheme in {"postgres", "postgresql"}
            and (u.hostname or "").endswith(".pooler.supabase.com") and u.port == 5432
            and unquote(u.username or "") == "postgres." + ref and u.password
            and u.path == "/postgres" and not u.fragment
            and (not modes or (len(modes) == 1 and modes[0] in {"require", "verify-ca", "verify-full"}))):
        raise Blocked("uat_connection_identity_invalid")
    return {"PGHOST": u.hostname, "PGPORT": "5432", "PGDATABASE": "postgres",
            "PGUSER": unquote(u.username), "PGPASSWORD": unquote(u.password),
            "PGSSLMODE": modes[0] if modes else "require", "PGCONNECT_TIMEOUT": "15"}


def command(args, env=None):
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise Blocked("external_command_failed")
    return r.stdout


def query(sql):
    env = dict(os.environ, **connection(os.environ["TARGET_DSN"]), PGOPTIONS="")
    return json.loads(command(["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-c", "BEGIN READ ONLY", "-c", "SET LOCAL statement_timeout='60s'",
        "-c", "SET LOCAL lock_timeout='10s'", "-c", sql, "-c", "COMMIT"], env))


def serving_identity(service, revision, target_connection):
    status = service.get("status", {})
    ready = status.get("latestReadyRevisionName")
    traffic = [t for t in status.get("traffic", []) if t.get("percent", 0) > 0]
    if (not ready or ready != status.get("latestCreatedRevisionName") or len(traffic) != 1
            or traffic[0].get("percent") != 100 or traffic[0].get("revisionName") != ready
            or revision.get("metadata", {}).get("name") != ready):
        raise Blocked("serving_revision_not_stable")
    image = f"{os.environ['GCP_REGION']}-docker.pkg.dev/{os.environ['GCP_PROJECT_ID']}/serverless/accounts"
    containers = revision.get("spec", {}).get("containers", [])
    if (len(containers) != 1 or containers[0].get("image") not in {image + ":" + TAG, image + "@" + DIGEST}
            or revision.get("status", {}).get("imageDigest") != image + "@" + DIGEST
            or not any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in revision.get("status", {}).get("conditions", []))):
        raise Blocked("serving_artifact_not_expected_digest")
    runtime = {v["name"]: v.get("value") for v in containers[0].get("env", [])}
    raw = runtime.get("SUPABASE_CONNECT_URI")
    if not raw or connection(raw) != target_connection:
        raise Blocked("serving_database_does_not_match_uat_vault_target")
    url = status.get("url", "")
    if urlsplit(url).scheme != "https" or not (urlsplit(url).hostname or "").endswith(".run.app"):
        raise Blocked("unexpected_accounts_health_origin")
    for route in ("/readyz", "/api/ping"):
        with urlopen(url.rstrip("/") + route, timeout=30) as response:
            if response.status != 200:
                raise Blocked("accounts_health_failed")
            payload = response.read(65536)
            if route == "/api/ping" and json.loads(payload).get("status") != "ok":
                raise Blocked("accounts_ping_failed")
    return {"revision": ready, "image": image + "@" + DIGEST,
            "source_sha": SOURCE, "snapshot_tag": TAG, "database_identity_matches": True,
            "readyz": 200, "api_ping": 200}


def observe_serving():
    project, region = os.environ["GCP_PROJECT_ID"], os.environ["GCP_REGION"]
    if project != "open-platform-uat":
        raise Blocked("gcp_target_not_authorized_uat_project")
    common = ["--project", project, "--region", region, "--format=json"]
    service = json.loads(command(["gcloud", "run", "services", "describe", "uat-accounts", *common]))
    ready = service.get("status", {}).get("latestReadyRevisionName", "")
    if not re.fullmatch(r"uat-accounts-[a-z0-9-]+", ready):
        raise Blocked("invalid_accounts_revision_reference")
    revision = json.loads(command(["gcloud", "run", "revisions", "describe", ready, *common]))
    return serving_identity(service, revision, connection(os.environ["TARGET_DSN"]))


def capture(key):
    names = query("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('schema_migrations','system_release_checkpoints')")
    if not {"users", "identities", "subscriptions"}.issubset(names):
        raise Blocked("required_retained_tables_missing")
    rows = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        value = query(f"SELECT json_build_object('count',count(*),'rows',coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text) FROM public.{quoted} t")
        # Hash PostgreSQL's canonical bytes, never round-trip money/JSON numeric
        # values through Python floats or confuse numeric and string types.
        private = value["rows"].encode()
        rows[name] = {"count": value["count"], "hmac": hmac.new(key, private, hashlib.sha256).hexdigest()}
    migration = query("SELECT coalesce(json_agg(json_build_object('version',version,'dirty',dirty)),'[]') FROM public.schema_migrations")
    if len(migration) != 1 or migration[0]["dirty"] is not False:
        raise Blocked("migration_tracking_not_exactly_one_clean_row")
    return {"rows": rows, "migration": migration[0]}


def verify_finance():
    tables = query("SELECT coalesce(json_agg(relname),'[]') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname LIKE 'finance_%' AND c.relkind='r' AND c.relrowsecurity")
    triggers = query("SELECT coalesce(json_agg(tgname),'[]') FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname LIKE 'finance_%' AND NOT t.tgisinternal AND t.tgenabled='O'")
    access = query("SELECT to_json((EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE n.nspname='public' AND c.relname LIKE 'finance_%' AND c.relkind='r' AND a.grantee=0) OR EXISTS(SELECT 1 FROM pg_roles r CROSS JOIN pg_tables t WHERE r.rolname IN ('anon','authenticated') AND t.schemaname='public' AND t.tablename LIKE 'finance_%' AND has_table_privilege(r.oid,format('%I.%I',t.schemaname,t.tablename),'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))))")
    constraints = query("SELECT coalesce(json_object_agg(conname,contype::text),'{}') FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND conname LIKE 'finance_%' AND convalidated")
    required = {
        "finance_invoices_payment_contract_uk": "u", "finance_invoices_provider_external_uk": "u",
        "finance_invoices_account_uuid_fkey": "f", "finance_invoices_subscription_uuid_fkey": "f",
        "finance_payments_invoice_contract_fk": "f", "finance_payments_invoice_uk": "u",
        "finance_payments_id_currency_uk": "u", "finance_payments_provider_external_uk": "u",
        "finance_refunds_payment_id_fkey": "f", "finance_refunds_payment_currency_fk": "f",
        "finance_refunds_provider_external_uk": "u", "finance_operations_provider_external_uk": "u",
        "finance_operation_events_operation_id_fkey": "f",
        **{name + "_pkey": "p" for name in FINANCE},
    }
    if (set(tables) != FINANCE or set(triggers) != TRIGGERS or access is not False
            or any(constraints.get(k) != v for k, v in required.items())):
        raise Blocked("finance_schema_or_client_access_protection_incomplete")


def compare(before, after):
    if after["migration"] != {"version": 2026092801, "dirty": False}:
        raise Blocked("artifact_migration_target_not_reached")
    for name, row in before["rows"].items():
        if after["rows"].get(name) != row:
            raise Blocked("retained_data_changed")
    added = set(after["rows"]) - set(before["rows"])
    if not added.issubset(FINANCE) or any(after["rows"][n]["count"] != 0 for n in added):
        raise Blocked("unexpected_new_table_or_finance_data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("before", "after"))
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    if os.environ.get("VAULT_ENV_PATH") != "uat":
        raise Blocked("repair_is_uat_only")
    state_path = Path(args.state)
    identity = observe_serving()
    if args.stage == "before":
        key = secrets.token_bytes(32)
        observation = capture(key)
        if observation["migration"]["version"] not in (2026092703, 2026092801):
            raise Blocked("unexpected_repair_baseline_version")
        with os.fdopen(os.open(state_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as f:
            json.dump({"key": key.hex(), "observation": observation, "identity": identity}, f)
        print("UAT serving digest/database/health verified; private retained-data baseline captured.")
        return
    if os.environ.get("OFFICIAL_MIGRATOR_REPEAT_VERIFIED") != "true":
        raise Blocked("official_migrator_repeat_evidence_missing")
    before = json.loads(state_path.read_text())
    after = capture(bytes.fromhex(before["key"]))
    compare(before["observation"], after)
    verify_finance()
    if identity != before["identity"]:
        raise Blocked("serving_identity_changed_during_schema_repair")
    report = {"stage": "schema_repair_not_business_acceptance", "environment": "uat",
        "artifact": identity, "before_migration": before["observation"]["migration"],
        "after_migration": after["migration"], "retained_tables_unchanged": True,
        "retained_table_count": len(before["observation"]["rows"]),
        "users": after["rows"]["users"]["count"], "identities": after["rows"]["identities"]["count"],
        "subscriptions": after["rows"]["subscriptions"]["count"],
        "finance_protections_verified": True, "official_migrator_repeat_verified": True,
        "eligible_for_prod": False,
        "business_acceptance": "blocked_pending_old_release_baseline_nonempty_subscription_and_manual_logins"}
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Blocked as error:
        print(json.dumps({"blocked": str(error), "eligible_for_prod": False}))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"blocked": "repair_probe_failed_details_suppressed", "eligible_for_prod": False}))
        raise SystemExit(1)
