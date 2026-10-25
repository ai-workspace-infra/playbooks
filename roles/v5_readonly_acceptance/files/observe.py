#!/usr/bin/env python3
"""Only kubectl get and PostgreSQL read-only transactions. Never print raw output."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess


def command(args, stdin=None):
    result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError('observation command failed')
    return json.loads(result.stdout)


def database(config):
    sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT json_build_object(
'schema', (SELECT version::text FROM schema_migrations LIMIT 1),
'dirty', (SELECT dirty FROM schema_migrations LIMIT 1),
'users', (SELECT count(*) FROM users),
'plans', (SELECT count(*) FROM billing_plans),
'subscriptions', (SELECT count(*) FROM subscriptions),
'quota', (SELECT count(*) FROM account_quota_states),
'orphan_subscriptions', (SELECT count(*) FROM subscriptions s LEFT JOIN users u ON s.user_uuid=u.uuid WHERE u.uuid IS NULL),
'agent_count', (SELECT count(*) FROM agents),
'fresh_agents', (SELECT count(*) FROM agents WHERE last_heartbeat > now()-interval '5 minutes' AND last_heartbeat <= now()),
'positive_buckets_15m', (SELECT count(*) FROM traffic_minute_buckets WHERE total_bytes>0 AND bucket_start>now()-interval '15 minutes' AND bucket_start<=now()),
'bytes_15m', (SELECT coalesce(sum(total_bytes),0) FROM traffic_minute_buckets WHERE bucket_start>now()-interval '15 minutes' AND bucket_start<=now()));
ROLLBACK;
"""
    return command(['kubectl', '-n', config['namespace'], 'exec', '-i', config['pod'], '--',
                    'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', config['db_user'],
                    '-d', config['db_name']], sql)


def ready(resource):
    status = resource.get('status', {})
    if resource.get('kind') == 'ExternalSecret':
        # ESO does not expose observedGeneration in this installed API.
        # Ready+refreshTime is only synchronization evidence, never drift proof.
        return bool(status.get('refreshTime')) and any(
            c.get('type') == 'Ready' and c.get('status') == 'True'
            for c in status.get('conditions', []))
    return (status.get('observedGeneration') == resource.get('metadata', {}).get('generation')
            and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in status.get('conditions', [])))


def assess_db(data, config):
    return {
        'V5-06': ('PASS' if data['schema'] == config['schema'] and data['dirty'] is False else 'FAIL'),
        # Counts alone establish presence, never migration equality.
        'V5-07': 'PARTIAL',
        'V5-08': 'PASS' if data['orphan_subscriptions'] == 0 else 'FAIL',
        'V5-10': 'PARTIAL' if data['positive_buckets_15m'] > 0 and data['bytes_15m'] > 0 else 'FAIL',
        # Population counts cannot establish exact declared Agent membership.
        'V5-11': 'PARTIAL' if data['fresh_agents'] == data['agent_count'] == config['expected_agents'] else 'FAIL',
    }


def observe(config):
    checks = {f'V5-{i:02}': {'status': 'NOT_RUN', 'reason': 'owner execution or independent evidence required'} for i in range(1, 24)}
    if Path('/etc/machine-id').read_text().strip() != config['machine_id']:
        raise ValueError('machine identity mismatch')
    try:
        data = database(config)
        for key, status in assess_db(data, config).items():
            checks[key] = {'status': status, 'observation': data}
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
        for key in ('V5-06', 'V5-07', 'V5-08', 'V5-10', 'V5-11'):
            checks[key] = {'status': 'FAIL', 'reason': 'database observation unavailable'}
    try:
        source = command(['kubectl', '-n', config['source_namespace'], 'get', 'gitrepository', config['source_name'], '-o', 'json'])
        resources = command(['kubectl', 'get', 'kustomizations,helmreleases,externalsecrets', '-A', '-o', 'json'])['items']
        revision = source.get('status', {}).get('artifact', {}).get('revision', '')
        converged = bool(resources) and ready(source) and all(ready(item) for item in resources)
        # Ready alone cannot prove no out-of-band patch: retain PARTIAL pending semantic diff.
        checks['V5-13'] = {'status': 'PARTIAL' if converged and revision.endswith(':'+config['gitops_sha']) else 'FAIL',
                           'observation': {'source_revision': revision, 'resource_count': len(resources), 'all_ready': converged},
                           'reason': 'semantic live-versus-GitOps diff still required'}
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
        checks['V5-13'] = {'status': 'FAIL', 'reason': 'Flux/ESO observation unavailable'}
    return {'schema_version': 'v5-readonly-observation/v1',
            'sampled_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'environment': config['environment'], 'machine_id': config['machine_id'], 'gitops_sha': config['gitops_sha'],
            'checks': checks, 'result': 'FAIL'}


def main():
    try:
        config = json.loads(os.environ['V5_ACCEPTANCE_CONFIG'])
        for key in ('namespace', 'pod', 'db_user', 'db_name', 'source_namespace', 'source_name'):
            if not re.fullmatch('[a-zA-Z0-9_.-]+', config[key]):
                raise ValueError('invalid identifier')
        result = observe(config)
    except (ValueError, KeyError, OSError):
        result = {'schema_version': 'v5-readonly-observation/v1', 'result': 'FAIL', 'reason': 'invalid configuration or machine identity', 'checks': {}}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
