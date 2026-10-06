#!/usr/bin/env python3
"""Read-only startup diagnosis with fixed safe fields; never prints raw logs/env."""
import json
from pathlib import Path
import subprocess


def read(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError('diagnostic command failed')
    return result.stdout


def main():
    container = json.loads(read(['docker', 'inspect', 'web-saas-postgresql']))[0]
    environment = dict(entry.split('=', 1) for entry in container['Config']['Env'] if '=' in entry)
    result = subprocess.run(['docker', 'logs', '--tail', '100', 'web-saas-postgresql'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError('diagnostic log command failed')
    logs = (result.stdout + result.stderr).lower()
    codes = []
    for code, pattern in [('missing_postgres_password', 'superuser password is not specified'),
                          ('data_directory_permission_denied', 'permission denied'),
                          ('cluster_version_mismatch', 'database files are incompatible'),
                          ('postgres_configuration_error', 'configuration file'),
                          ('incomplete_cluster', 'could not open file "global/pg_control"')]:
        if pattern in logs:
            codes.append(code)
    pgdata = Path('/data/postgresql/pgdata')
    print(json.dumps({'kind': 'postgres_startup_diagnostic', 'host': 'web-saas-prod',
        'container_state': container['State']['Status'], 'exit_code': container['State']['ExitCode'],
        'oom_killed': container['State'].get('OOMKilled') is True,
        'postgres_password_present': bool(environment.get('POSTGRES_PASSWORD')),
        'pgdata_empty': pgdata.is_dir() and not pgdata.is_symlink() and not any(pgdata.iterdir()),
        'pg_version_present': (pgdata / 'PG_VERSION').exists(),
        'error_codes': codes or ['unclassified_startup_failure']}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('PostgreSQL startup diagnosis stopped; raw output withheld.')
        raise SystemExit(1)
