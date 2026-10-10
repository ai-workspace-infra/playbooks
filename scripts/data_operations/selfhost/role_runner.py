#!/usr/bin/env python3
"""UAT component-role adapter; no import, schema initialization or promotion.

CMDB provenance is checked by verify_caller_cmdb.py before this adapter runs.
Only nonsecret request metadata is written to Ansible vars. Backup encryption
material is resolved at runtime from the process environment by the role.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
TAG = re.compile(r"(?:uat-)?daily-build-\d{4}\.\d{2}\.\d{2}(?:-r[1-9]\d*)?|v\d[0-9.r-]*")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}")
CONFIG_KEYS = {"execution_path", "target_host", "caller_run_id", "source_database_id",
               "baseline_id", "authorized_subscription_sample_id", "confirm_backup",
               "expected_schema_version", "target_schema_version", "migration_sha256",
               "accounts_source_revision", "migration_request", "baseline_manifest",
               "backup_evidence", "restore_evidence"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate(env):
    config = json.loads(env.get("CONFIG_JSON", "{}"))
    require(isinstance(config, dict) and set(config) <= CONFIG_KEYS, "Unsupported role request fields")
    require(env.get("REQUESTED_ENVIRONMENT") == "uat", "Component role runner is UAT-only")
    phase = env.get("REQUESTED_OPERATION", "")
    require(phase in {"preflight", "backup", "migration"}, "Unsupported Selfhost component phase")
    require(config.get("execution_path") == "selfhost_roles", "Explicit selfhost_roles execution path required")
    require(config.get("target_host") == "web-saas-uat", "Canonical UAT host required")
    require(re.fullmatch(r"[1-9][0-9]*", str(config.get("caller_run_id", ""))), "Trusted CMDB caller run required")
    require(re.fullmatch(r"[1-9][0-9]{0,17}", env.get("EXPECTED_SCHEMA_VERSION", "")), "Exact clean starting version required")
    require(TAG.fullmatch(env.get("RELEASE_TAG", "")), "Immutable Accounts release tag required")
    for key in ("source_database_id", "baseline_id", "authorized_subscription_sample_id"):
        value = config.get(key, "")
        require(isinstance(value, str) and (value == "" or IDENTIFIER.fullmatch(value)), "Unsafe provenance identifier")
    if phase == "backup":
        require(config.get("confirm_backup") is True, "Explicit encrypted backup and isolated-restore opt-in required")
        require(all(config.get(key) for key in ("source_database_id", "baseline_id", "authorized_subscription_sample_id")),
                "Approved baseline and sample references required before backup credentials")
    if phase == "migration":
        require(re.fullmatch(r"[0-9a-f]{40,64}", str(config.get("accounts_source_revision", ""))),
                "Immutable Accounts source revision required for migration")
        require(all(config.get(key) for key in ("source_database_id", "baseline_id", "authorized_subscription_sample_id")),
                "Approved baseline and nonempty subscription sample references required before migration")
        for key in ("migration_request", "baseline_manifest"):
            require(isinstance(config.get(key), dict), f"{key} is required for migration")
    return config


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


def prepare(env):
    config = validate(env)
    cmdb = json.loads(Path(env["CMDB_FILE"]).read_text())
    host = config["target_host"]
    require(cmdb.get("environment") == "uat", "CMDB environment differs")
    record = cmdb.get(host, {})
    require(isinstance(record, dict) and "web_saas" in record.get("groups", []), "Canonical CMDB host missing")
    ipaddress.ip_address(record.get("ip", ""))
    user = record.get("ansible_user", "root")
    require(isinstance(user, str) and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user), "Unsafe CMDB SSH identity")
    # Do not import arbitrary host_vars: they could override connection,
    # become, target database or secret parameters. This remains CMDB-derived.
    selected = {host: {"ip": record["ip"], "ansible_user": user, "groups": ["web_saas"]}}
    directory = Path(env["RUNNER_TEMP"]) / "selfhost-data-roles"
    write_json(directory / "cmdb.json", selected)
    accounts = ROOT / "accounts"
    result = subprocess.run(["git", "-C", str(accounts), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    source_sha = result.stdout.strip()
    if env["REQUESTED_OPERATION"] == "migration":
        require(source_sha == config["accounts_source_revision"] and re.fullmatch(r"[0-9a-f]{40}", source_sha),
                "Accounts source differs from the reviewed migration commit")
    else:
        result = subprocess.run(["git", "-C", str(accounts), "rev-parse", f"refs/tags/{env['RELEASE_TAG']}^{{commit}}"],
                                capture_output=True, text=True, check=True)
        require(result.stdout.strip() == source_sha and re.fullmatch(r"[0-9a-f]{40}", source_sha), "Accounts source differs from its tag")
    binding = dict(environment="uat", release_tag=env["RELEASE_TAG"], accounts_source_sha=source_sha,
                   expected_schema_version=int(env["EXPECTED_SCHEMA_VERSION"]), config=config)
    request_sha256 = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    variables = dict(web_saas_release_environment="uat", web_saas_release_target_host=host,
                     web_saas_data_operation=env["REQUESTED_OPERATION"],
                     web_saas_release_mode="preflight" if env["REQUESTED_OPERATION"] == "preflight" else "rehearsal",
                     web_saas_release_phase=env["REQUESTED_OPERATION"], web_saas_release_run_id=env["GITHUB_RUN_ID"],
                     web_saas_release_expected_version=env["EXPECTED_SCHEMA_VERSION"], web_saas_release_tag=env["RELEASE_TAG"],
                     web_saas_release_candidate_sha256=request_sha256,
                     web_saas_release_receipt_file=str(directory / "private-receipt.json"),
                     web_saas_release_source_database_id=config.get("source_database_id", ""),
                     web_saas_release_baseline_id=config.get("baseline_id", ""),
                     web_saas_release_authorized_subscription_sample_id=config.get("authorized_subscription_sample_id", ""),
                     web_saas_data_migration_environment="uat",
                     web_saas_data_migration_request=config.get("migration_request", {}),
                     web_saas_data_migration_baseline_manifest=config.get("baseline_manifest", {}),
                     web_saas_data_migration_backup_evidence=config.get("backup_evidence", {}),
                     web_saas_data_migration_restore_evidence=config.get("restore_evidence", {}),
                     web_saas_data_migration_binary_path=str(Path(env["RUNNER_TEMP"]) / "accounts-migratectl"),
                     web_saas_data_migration_migrations_path=str(Path(env["RUNNER_TEMP"]) / "account-migrations"),
                     web_saas_data_migration_receipt_file=str(directory / "private-receipt.json"))
    write_json(directory / "vars.json", variables)
    # A request hash is component provenance, NOT an accepted image candidate.
    write_json(directory / "binding.json", binding | {"request_sha256": request_sha256, "business_acceptance": False})


def execute(env):
    config = validate(env)
    directory = Path(env["RUNNER_TEMP"]) / "selfhost-data-roles"
    child_env = dict(env, AI_WORKSPACE_CMDB_JSON=str(directory / "cmdb.json"))
    inventory = ["-i", "inventory/terraform_cmdb.py"]
    listed = subprocess.run(["ansible-inventory", *inventory, "--list"], cwd=ROOT, env=child_env,
                            capture_output=True, text=True, check=True)
    require(set(json.loads(listed.stdout).get("_meta", {}).get("hostvars", {})) == {config["target_host"]}, "Inventory target set is not exact")
    playbook = ["ansible-playbook", *inventory, "web-saas-data-operations.yml", "--limit", config["target_host"],
                "--extra-vars", "@" + str(directory / "vars.json")]
    evidence = {"schema": 1, "phase": env["REQUESTED_OPERATION"], "environment": "uat",
                "status": "blocked", "business_acceptance": False, "reason_code": "ROLE_EXECUTION_FAILED"}
    try:
        subprocess.run([*playbook, "--syntax-check"], cwd=ROOT, env=child_env, check=True)
        subprocess.run(["ansible", config["target_host"], *inventory, "-m", "ping"], cwd=ROOT, env=child_env, check=True)
        subprocess.run(playbook, cwd=ROOT, env=child_env, check=True)
        receipt = json.loads((directory / "private-receipt.json").read_text())
        require(receipt.get("status") == "passed", "Role receipt did not pass")
        # Only an allowlisted, nonpersonal component receipt is publishable.
        for key in ("schema_version", "existing_users", "subscriptions", "clean", "encrypted", "durable",
                    "archive_sha256", "isolated_restore_verified", "restored_data_matches",
                    "before_version", "after_version", "migration_sha256", "checkpoint_id",
                    "dirty_false", "database_lock_held", "bounded_lock_wait", "bounded_execution",
                    "reviewed_additive_sql", "idempotent", "data_preserved", "old_application_compatible"):
            if key in receipt:
                evidence[key] = receipt[key]
        evidence.update(status="passed", reason_code="COMPONENT_ONLY_NOT_RELEASE_ACCEPTANCE")
    finally:
        write_json(directory / "public" / "receipt.json", evidence)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("validate", "prepare", "execute"))
    action = parser.parse_args().action
    try:
        if action == "validate":
            validate(os.environ)
        elif action == "prepare":
            prepare(os.environ)
        else:
            execute(os.environ)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        # No subprocess stderr, request values, secrets or row data propagated.
        raise SystemExit("UAT role component blocked: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)) from None


if __name__ == "__main__":
    main()
