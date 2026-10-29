#!/usr/bin/env python3
"""Build a read-only fixed-main SIT baseline artifact for issue #1483.

No database connection, deployment credential, or target environment is used.
Every source and build locator is fixed by reviewed Main, and drift blocks output.
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from dev_feature_baseline_inputs import inspect

ACCOUNTS = {'repo': 'ai-workspace-services/accounts', 'sha': 'a88195e5532e7be60b1ce8a6f158a27d682d7914',
            'run': 40709054881, 'artifact': 12825497078,
            'image': 'ghcr.io/ai-workspace-services/accounts@sha256:2938c97dc975c82cfdf2b9874b574e2aaa6357b4bb83a5a97f5b703448041434',
            'prefix': 'accounts-main-image'}
BILLING = {'repo': 'ai-workspace-services/billing-service', 'sha': '86b0560e616fa85eb9abcef49c0ca52f6bb99a9b',
           'run': 40729119885, 'artifact': 12835035241,
           'image': 'ghcr.io/ai-workspace-services/billing-service@sha256:66a7e12274a42c929277b68fbee58ff5e4277b21e37d4f8e937ed38b9ac474e4',
           'prefix': 'billing-main-image'}


def api(path, *, binary=False):
    if not path.startswith('repos/') or '..' in path or '://' in path:
        raise ValueError('invalid API path')
    result = subprocess.run(['gh', 'api', path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=90, check=True).stdout
    if len(result) > 2_000_000:
        raise ValueError('API response too large')
    return result if binary else json.loads(result)


def check_main(row):
    head = api('repos/' + row['repo'] + '/git/ref/heads/main')
    if head.get('object', {}).get('sha') != row['sha']:
        raise ValueError('fixed Main source moved')


def source(row, path):
    data = api('repos/' + row['repo'] + '/contents/' + path + '?ref=' + row['sha'])
    if data.get('type') != 'file' or data.get('path') != path or data.get('encoding') != 'base64':
        raise ValueError('fixed source file unavailable')
    result = base64.b64decode(data['content'], validate=False)
    if not result or len(result) > 2_000_000:
        raise ValueError('invalid source content')
    return result


def check_build(row):
    repo, rid, aid = row['repo'], row['run'], row['artifact']
    run = api(f'repos/{repo}/actions/runs/{rid}')
    if (run.get('id') != rid or run.get('path', '').split('@')[0] != '.github/workflows/ci-pipeline.yml' or
        run.get('event') != 'push' or run.get('head_branch') != 'main' or run.get('head_sha') != row['sha'] or
        run.get('run_attempt') != 1 or run.get('status') != 'completed' or run.get('conclusion') != 'success' or
        run.get('repository', {}).get('full_name') != repo or run.get('head_repository', {}).get('full_name') != repo):
        raise ValueError('build run identity mismatch')
    metadata = api(f'repos/{repo}/actions/artifacts/{aid}')
    if (metadata.get('id') != aid or metadata.get('expired') is not False or
        metadata.get('name') != f"{row['prefix']}-{rid}-1" or
        metadata.get('workflow_run', {}).get('id') != rid or
        metadata.get('workflow_run', {}).get('head_sha') != row['sha'] or
        type(metadata.get('size_in_bytes')) is not int or not 0 < metadata['size_in_bytes'] <= 2_000_000):
        raise ValueError('build artifact identity mismatch')
    archive = api(f'repos/{repo}/actions/artifacts/{aid}/zip', binary=True)
    if metadata.get('digest') != 'sha256:' + hashlib.sha256(archive).hexdigest():
        raise ValueError('build artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if len(entries) != 1 or entries[0].filename != 'main-image-build.json' or entries[0].file_size > 100_000:
            raise ValueError('unexpected build manifest archive')
        proof = json.loads(zipped.read(entries[0]))
    if (proof.get('schema') != 'main-image-build/v1' or proof.get('source_repository') != repo or
        proof.get('source_sha') != row['sha'] or proof.get('image') != row['image'] or
        proof.get('event') != 'push' or proof.get('ref') != 'refs/heads/main' or
        proof.get('run_id') != rid or proof.get('attempt') != 1):
        raise ValueError('build manifest does not match fixed source')


def produce(image_manifest_bytes):
    for row in (ACCOUNTS, BILLING):
        check_main(row)
        check_build(row)
    manifest = source(ACCOUNTS, 'sql/init/accounts-native.manifest.json')
    native = source(ACCOUNTS, 'sql/init/accounts-native.sql')
    billing = source(BILLING, 'sql/billing-service-schema.sql')
    result = inspect(manifest, native, billing, image_manifest_bytes, accounts_sha=ACCOUNTS['sha'],
                     billing_sha=BILLING['sha'], accounts_image=ACCOUNTS['image'])
    for row in (ACCOUNTS, BILLING):
        check_main(row)
    return {'schema': 'sit-data-baseline/v1', 'result': 'verified', 'feature_id': 'sit-test-1483',
            'namespace': 'feature-sit-test-1483-sit', 'data_baseline_sha256': result['data_baseline_sha256'],
            'inputs': result['inputs'], 'database_status': 'NOT_RUN',
            'source_builds': [{'repository': row['repo'], 'sha': row['sha'], 'run_id': row['run'],
                               'artifact_id': row['artifact']} for row in (ACCOUNTS, BILLING)]}


def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REPOSITORY') != 'ai-workspace-infra/playbooks' or
        os.environ.get('GITHUB_REF') != 'refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or
        os.environ.get('GITHUB_WORKFLOW_REF') != 'ai-workspace-infra/playbooks/.github/workflows/feature-baseline-1483.yml@refs/heads/main'):
        raise ValueError('protected Main workflow required')
    image = subprocess.run(['docker', 'run', '--rm', '--pull=always', '--platform', 'linux/amd64',
                            '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
                            '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/migratectl',
                            ACCOUNTS['image'], 'native-schema'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=300, check=True).stdout
    result = produce(image)
    destination = Path(os.environ['RUNNER_TEMP']) / 'feature-baseline-1483.json'
    destination.write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
    print('Verified fixed baseline inputs; database status: NOT_RUN')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('::error::Feature baseline source verification failed', file=sys.stderr)
        sys.exit(1)
