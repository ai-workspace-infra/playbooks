#!/usr/bin/env python3
"""Read-only Selfhost metadata diagnostic; no source credential or business rows."""
import json
import subprocess


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('private command failed')
    return result.stdout.strip()


def sql(query, database='postgres'):
    return command(['docker', 'exec', 'web-saas-postgresql', 'psql', '-U', 'postgres',
        '-d', database, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-c',
        'BEGIN READ ONLY; ' + query + '; COMMIT;'])


def main():
    stage = 'runtime'
    try:
        # No docker inspect/config output: environment values may contain secrets.
        containers = command(['docker', 'ps', '--format', '{{.Names}}']).splitlines()
        writers = sorted(name for name in containers if any(part in name.lower()
            for part in ('account', 'billing', 'doco', 'watchtower')))
        stage = 'postgres'
        version = sql('SHOW server_version_num')
        present = sql("SELECT count(*) FROM pg_database WHERE datname='account'") == '1'
        result = dict(format=1, stage='target_metadata', postgres_version=version,
            database_present=present, active_writer_containers=writers,
            target_writes=False, source_accessed=False, database_cutover_approved=False)
        if present:
            stage = 'schema'
            result['checkpoint'] = sql('SELECT version::text || \':\' || dirty::text '
                'FROM public.schema_migrations', 'account')
            result['business_table_count'] = int(sql("SELECT count(*) FROM pg_tables "
                "WHERE schemaname='public' AND tablename NOT IN "
                "('schema_migrations','system_release_checkpoints')", 'account'))
        print(json.dumps(result, sort_keys=True))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print(json.dumps(dict(format=1,result='failed',failure_stage=stage,
            target_writes=False, source_accessed=False, database_cutover_approved=False)))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
