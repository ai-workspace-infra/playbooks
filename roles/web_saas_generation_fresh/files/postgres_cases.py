#!/usr/bin/env python3
"""Run pinned migration integration tests on new network-isolated PG17 fixtures."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

os.umask(0o077)


def run(args, check=True, **kw):
    value = subprocess.run(args, capture_output=True, text=True, timeout=240, **kw)
    if check and value.returncode:
        raise RuntimeError('isolated fixture command failed; fixture retained')
    return value


def main(spec):
    generation = spec['target_generation']
    assert re.fullmatch(r'prod-v[1-9][0-9]*', generation)
    assert run(['hostname', '-s']).stdout.strip() == 'web-saas-' + generation
    image = spec['postgres_image']
    assert re.fullmatch(r'ghcr.io/[a-zA-Z0-9_./:-]+@sha256:[0-9a-f]{64}', image)
    binary = Path(spec['integration_binary'])
    assert str(binary).startswith('/data/backups/web-saas/' + generation + '/')
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == spec['integration_binary_sha256']
    root = Path(spec['checkpoint'])
    assert str(root).startswith('/data/backups/web-saas/' + generation + '/fresh-native-')
    started = datetime.datetime.now(datetime.timezone.utc)
    suffix = started.strftime('%m%d%H%M%S%f')
    cases = [
        ('native-initialization', 'TestNativeInitPostgres17', 'MIGRATECTL_NATIVE_TEST_DSN', 'account', None),
        ('migration-concurrency-retry', 'TestControlledMigratePostgresIntegration', 'ACCOUNTS_TEST_POSTGRES_DSN', 'migration_test', None),
        ('lifecycle-migration-rollback', 'TestSubscriptionLifecycleUpgradePostgres17', 'MIGRATECTL_LIFECYCLE_UPGRADE_TEST_DSN', 'lifecycle_upgrade_test', None),
        ('business-conflict-rollback', 'TestFullBusinessPostgres17', 'MIGRATECTL_FULL_BUSINESS_TEST_DSN', 'account', 'full_business_source'),
        ('usage-merge-conflicts', 'TestFullBusinessUsageMergePostgres17', 'MIGRATECTL_USAGE_MERGE_TEST_DSN', 'account', 'usage_merge_source'),
        ('late-users-single-backfill', 'TestGenerationUsersPostgres17', 'MIGRATECTL_GENERATION_USERS_SOURCE_TEST_DSN', 'account', 'second-server'),
    ]
    results = []
    for index, (case_id, test, variable, database, extra) in enumerate(cases):
        if spec.get('selected_cases') and case_id not in spec['selected_cases']:
            continue
        name = 'upgrade-fixture-' + suffix + '-' + str(index)
        containers = [name]
        row = {'case_id': case_id, 'test': test, 'containers': containers,
               'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            def start(container, network, port=5432):
                run(['docker', 'run', '-d', '--name', container, '--network', network, '--memory=384m',
                     '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=' + database,
                     '--entrypoint', 'docker-entrypoint.sh', image, 'postgres', '-p', str(port)])
                for _ in range(60):
                    if run(['docker', 'exec', container, 'pg_isready', '-U', 'postgres', '-p', str(port)], check=False).returncode == 0:
                        return
                    time.sleep(1)
                raise RuntimeError('isolated PG17 startup timeout')
            start(name, 'none')
            if extra == 'second-server':
                other = name + '-target'
                containers.append(other)
                start(other, 'container:' + name, 5544)
            elif extra:
                run(['docker', 'exec', name, 'createdb', '-U', 'postgres', extra])
            run(['docker', 'exec', name, 'mkdir', '-p', '/work/internal/migrate/testdata', '/work/sql/migrations'])
            run(['docker', 'cp', str(binary), name + ':/work/integration-tests'])
            run(['docker', 'cp', spec['billing_sql'], name + ':/work/internal/migrate/testdata/cloud_vendor_costs_native.sql'])
            run(['docker', 'cp', spec['lifecycle_sql'], name + ':/work/sql/migrations/2026102001_subscription_lifecycle.up.sql'])
            args = ['docker', 'exec', '-w', '/work/internal/migrate', '-e', variable + '=postgres://postgres@127.0.0.1:5432/' + database + '?sslmode=disable']
            if extra == 'second-server':
                args += ['-e', 'MIGRATECTL_GENERATION_USERS_TARGET_TEST_DSN=postgres://postgres@127.0.0.1:5544/account?sslmode=disable']
            executed = run(args + [name, '/work/integration-tests', '-test.run=^' + test + '$', '-test.v', '-test.timeout=180s'], check=False)
            # Preserve only aggregate metadata: test failures can contain fixture identities.
            row.update(exit_code=executed.returncode, output_sha256=hashlib.sha256((executed.stdout + executed.stderr).encode()).hexdigest(),
                       skipped='--- SKIP:' in executed.stdout,
                       status='PASS' if executed.returncode == 0 and '--- SKIP:' not in executed.stdout and '--- PASS:' in executed.stdout else 'FAIL')
            safe_log = re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}', '<fixture-uuid>', executed.stdout + executed.stderr)
            safe_log = re.sub(r'[\w.+-]+@[\w.-]+', '<fixture-address>', safe_log)
            log_path = root / (case_id + '-' + suffix + '.log')
            log_path.write_text(safe_log)
            row['redacted_log'] = str(log_path)
            if row['status'] == 'PASS':
                # Reboot only this isolated server, retaining its data volume and checking
                # representative row fingerprints before/after the restart.
                def fingerprint():
                    base = ['docker', 'exec', name, 'psql', '-U', 'postgres', '-d', database, '-XAtq', '-c']
                    tables = run(base + ["SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).stdout.splitlines()
                    values = []
                    for table in tables:
                        assert re.fullmatch('[a-z][a-z0-9_]*', table)
                        query = 'SELECT md5(COALESCE(string_agg(h,\'\' ORDER BY h),\'\')) FROM (SELECT md5(row_to_json(t)::text) h FROM public."' + table + '" t) q'
                        values.append((table, run(base + [query]).stdout.strip()))
                    return hashlib.sha256(json.dumps(values).encode()).hexdigest()
                before = fingerprint()
                run(['docker', 'restart', name])
                for _ in range(60):
                    ready = run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres'], check=False)
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                after = fingerprint()
                row['restart_rows_unchanged'] = before == after
                if before != after:
                    row['status'] = 'FAIL'
            for container in reversed(containers):
                run(['docker', 'stop', container], check=False)
        except Exception as error:
            row.update(status='FAIL', exit_code=1, error=type(error).__name__)
        row['ended_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        results.append(row)
    result = {'run_id': spec['run_id'], 'candidate_commit': spec['candidate_commit'], 'source_sha': spec['source_sha'],
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'binary_sha256': spec['integration_binary_sha256'],
              'postgres_image': image, 'production_connections': False, 'results': results,
              'status': 'PASS' if all(r['status'] == 'PASS' for r in results) else 'FAIL'}
    path = root / ('postgres-cases-' + started.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(dict(result, evidence=str(path))))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main(json.load(sys.stdin)))
