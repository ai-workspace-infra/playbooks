#!/usr/bin/env python3
"""Owner-operated rollback-only schema preflight; never writes business data."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys

import yaml
import business_schema_guard as guard


def run(args, body=None):
    result = subprocess.run(args, input=body, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('preflight command failed: ' + args[0] + ' (details suppressed)')
    return result.stdout.strip()


def pinned(root, sha):
    root = pathlib.Path(root).resolve(strict=True)
    if not guard.SHA256.fullmatch(sha + '0'*24) or run(['git', '-C', str(root), 'rev-parse', 'HEAD']) != sha:
        raise RuntimeError('repository revision differs')
    if run(['git', '-C', str(root), 'status', '--porcelain']):
        raise RuntimeError('repository worktree is not clean')
    return root


def private_socket(path):
    p = pathlib.Path(path)
    if not p.is_absolute() or p.is_symlink():
        raise RuntimeError('restore socket directory must be absolute and non-symlink')
    st = p.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise RuntimeError('restore socket directory is not private')
    return str(p)


def fingerprints(command):
    settings = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL TIME ZONE 'UTC'; SET LOCAL DateStyle TO 'ISO, YMD'; SET LOCAL extra_float_digits TO 3;\n"
    names = run(command, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename COLLATE \"C\";\n").splitlines()
    if not names or len(names) > 1000 or len(set(names)) != len(names):
        raise RuntimeError('restore table scope invalid')
    for name in names:
        guard._identifier(name)
    # Capture all row digests in one read-only snapshot; never return rows.
    sql = settings
    for name in names:
        sql += guard.fingerprint_query(name).replace('SELECT count(*)::bigint,', "SELECT '" + name + "|'||count(*)::text||'|'||", 1) + ';\n'
    sql += 'ROLLBACK;\n'
    lines = run(command, sql).splitlines()
    if len(lines) != len(names):
        raise RuntimeError('restore fingerprint scope differs')
    out = {}
    for name, line in zip(names, lines):
        parts = line.split('|')
        if len(parts) != 3 or parts[0] != name or not parts[1].isdigit() or not guard.SHA256.fullmatch(parts[2]):
            raise RuntimeError('restore fingerprint invalid')
        out[name] = (int(parts[1]), parts[2])
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('gitops', 'gitops-sha', 'operation', 'accounts', 'accounts-sha', 'sql-sha256', 'backup', 'backup-sha256', 'restore-socket', 'restore-db', 'restore-user', 'host', 'target-system-id', 'target-db-oid', 'receipt'):
        parser.add_argument('--' + key, required=True)
    a = parser.parse_args()
    root = pinned(a.gitops, a.gitops_sha)
    accounts = pinned(a.accounts, a.accounts_sha)
    operation = (root / a.operation).resolve(strict=True)
    if not operation.is_relative_to(root):
        raise RuntimeError('operation path escapes GitOps')
    c = yaml.safe_load(operation.read_text())
    if c.get('kind') != 'accounts-migration-bootstrap' or c.get('role') != 'target' or c.get('target_mode') != 'business':
        raise RuntimeError('explicit business target declaration required')
    for key in ('source_namespace', 'postgres_pod', 'postgres_admin', 'database_name'):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', c.get(key, '')):
            raise RuntimeError('invalid target identifier')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+', a.host):
        raise RuntimeError('invalid SSH target')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', a.restore_db) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', a.restore_user) or a.restore_db == c['database_name']:
        raise RuntimeError('invalid independent restore identity')
    socket = private_socket(a.restore_socket)
    archive = pathlib.Path(a.backup)
    if archive.is_symlink() or not archive.is_file() or not guard.SHA256.fullmatch(a.backup_sha256):
        raise RuntimeError('invalid encrypted backup')
    with archive.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    if actual != a.backup_sha256:
        raise RuntimeError('encrypted backup checksum differs')
    sql = (accounts / 'sql/compat/target-scope-upgrade.sql').read_bytes()
    local = ['psql', '-X', '-q', '-h', socket, '-U', a.restore_user, '-d', a.restore_db, '-At', '-v', 'ON_ERROR_STOP=1']
    if run(local, "SELECT inet_server_addr() IS NULL AND current_database()=" + "'" + a.restore_db + "';") != 't':
        raise RuntimeError('restore connection is not a local socket')
    rows = fingerprints(local)
    remote_cmd = ['kubectl', '-n', c['source_namespace'], 'exec', '-i', c['postgres_pod'], '--', 'psql', '-X', '-q', '-U', c['postgres_admin'], '-d', c['database_name'], '-At', '-v', 'ON_ERROR_STOP=1']
    remote = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', a.host, shlex.join(remote_cmd)]
    catalog = run(remote, guard.catalog_fingerprint_query() + ';')
    statement = guard.compose_transaction(sql, a.sql_sha256, rows, a.target_system_id, a.target_db_oid, catalog, dry_run=True)
    output = run(remote, statement + "SELECT 'guard_rollback_confirmed';\n")
    if not output.endswith('guard_rollback_confirmed'):
        raise RuntimeError('rollback acknowledgement missing')
    receipt = {'result': 'rollback_preflight_passed', 'gitops_sha': a.gitops_sha, 'accounts_sha': a.accounts_sha, 'sql_sha256': a.sql_sha256, 'encrypted_backup_sha256': actual, 'target_catalog_sha256': catalog, 'table_count': len(rows), 'limitations': ['restore provenance not independently authenticated', 'no role or ACL recovery proof', 'no migration preview or write authorization']}
    fd = os.open(a.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(receipt, f, sort_keys=True)
        f.write('\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
