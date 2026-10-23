#!/usr/bin/env python3
"""Target-only adapter, called immediately after the native backup/restore roles."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def require(value):
    if not value:
        raise ValueError('Native backup/restore or API baseline binding differs; no gate receipt issued')


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build(request):
    backup, restore = request['backup'], request['restore']
    plan, fresh = request['plan'], request['fresh_plan']
    require(backup['status'] == restore['status'] == 'passed' and backup.get('encrypted') is True
            and backup.get('durable') is True and backup.get('full_database') is True and restore.get('full_database') is True)
    for key in ('environment', 'source_database_id', 'baseline_id', 'schema_version', 'migration_rows', 'migration_dirty',
                'schema_sha256', 'data_sha256', 'archive_sha256', 'database_system_identifier', 'existing_users', 'subscriptions'):
        require(backup[key] == restore[key])
    require(restore.get('isolated_restore_verified') is True and restore.get('restored_data_matches') is True
            and restore.get('source_restore_schema_matches') is True and backup['migration_dirty'] == 'false')
    # The native hashes and API hashes deliberately have different algorithms.
    # The owner pauses target writers across backup+restore+fresh preview, then
    # binds the unchanged API baseline; it never renames a native data checksum.
    for key in ('target_identity_sha256', 'target_schema_sha256', 'target_baseline_sha256', 'tool_sha256'):
        require(plan[key] == fresh[key])
    require(plan['snapshot']['snapshot_sha256'] == fresh['snapshot']['snapshot_sha256']
            and plan['state'] == fresh['state'] == 'preview' and plan['conflict_rows'] == fresh['conflict_rows'] == 0)
    require(plan['plan_id'] == request['plan_id'] and plan['plan_sha256'] == request['plan_sha256'])
    archive = Path(backup['archive_path'])
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_mode & 0o077 == 0)
    digest = hashlib.sha256()
    with archive.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    require(digest.hexdigest() == backup['archive_sha256'])
    # Native record + actual database OID must resolve to the API's physical identity.
    result = subprocess.run(['docker', 'exec', 'web-saas-postgresql', 'psql', '-U', 'postgres', '-d', 'account',
        '-XAtq', '-v', 'ON_ERROR_STOP=1', '-c', "SELECT system_identifier::text||':'||(SELECT oid::text FROM pg_database WHERE datname=current_database()) FROM pg_control_system()"],
        capture_output=True, text=True, timeout=30)
    require(result.returncode == 0)
    cluster, oid = result.stdout.strip().split(':')
    require(cluster == backup['database_system_identifier'] and hashed([cluster, oid]) == plan['target_identity_sha256'])
    now = datetime.now(timezone.utc)
    expires = min(now + timedelta(hours=1), datetime.fromisoformat(plan['expires_at'].replace('Z', '+00:00')))
    require(expires > now)
    receipt = {'format': 1, 'backup_id': backup['checkpoint_id'], 'backup_sha256': backup['archive_sha256'],
               'target_identity_sha256': plan['target_identity_sha256'], 'target_schema_sha256': plan['target_schema_sha256'],
               'target_baseline_sha256': plan['target_baseline_sha256'], 'restored_baseline_sha256': plan['target_baseline_sha256'],
               'restore_verified': True, 'verified_at': now.isoformat().replace('+00:00', 'Z'),
               'expires_at': expires.isoformat().replace('+00:00', 'Z')}
    proof = {'kind': 'playbooks-api-backup-proof', 'owner_sha': request['owner_sha'], 'plan_id': plan['plan_id'],
             'plan_sha256': plan['plan_sha256'], 'receipt': receipt, 'backup': backup, 'restore': restore,
             'fresh_plan': fresh, 'target_writers_paused': True, 'business_acceptance': False,
             'populated_restore': backup['existing_users'] > 0}
    return receipt, proof


def write_private(path, value):
    path = Path(path)
    require(path.is_absolute() and path.parent.is_dir() and path.parent.resolve() == path.parent
            and not path.is_symlink() and not path.exists())
    with path.open('x', encoding='utf-8') as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        receipt, proof = build(request)
        # The API listener consumes the first file. The controller/caller checks
        # the owner proof and its trusted run checksum before submitting apply.
        write_private(request['receipt_file'], receipt)
        write_private(request['proof_file'], proof)
        print(json.dumps({'backup_gate_issued': True, 'receipt': receipt, 'proof': proof, 'business_acceptance': False}))
    except Exception:
        print('Backup gate not issued; restore and baseline verification required.', file=sys.stderr)
        sys.exit(2)
