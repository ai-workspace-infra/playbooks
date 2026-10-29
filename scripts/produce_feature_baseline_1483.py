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
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from dev_feature_baseline_inputs import inspect

ACCOUNTS = {'repo': 'ai-workspace-services/accounts', 'sha': 'a88195e5532e7be60b1ce8a6f158a27d682d7914',
            'run': 40709054881, 'artifact': 12825497078,
            'image': 'ghcr.io/ai-workspace-services/accounts@sha256:2938c97dc975c82cfdf2b9874b574e2aaa6357b4bb83a5a97f5b703448041434',
            'prefix': 'accounts-main-image'}
PR_ACCOUNTS = {'repo': 'ai-workspace-services/accounts', 'sha': '0bf29d88b3e8fc3421ef52fc35b0e26df061899e',
               'base': '38c3a3c2e19b7d177e6cb5e019a49c1b35027e95', 'pr': 280,
               'run': 40836607277, 'artifact': 12880199249, 'build_run': 40835969395,
               'image': 'ghcr.io/ai-workspace-services/accounts@sha256:cf8e14f91c42aa4741013876de7e1a36d5f5c7c65323dbf3db39f0cb58f22a12',
               'prefix': 'accounts-pr-image-evidence', 'filename': 'accounts-pr-image-evidence.json',
               'workflow': '.github/workflows/accounts-pr-image-evidence.yml'}
PHASE = 'context'

BILLING = {'repo': 'ai-workspace-services/billing-service', 'sha': '86b0560e616fa85eb9abcef49c0ca52f6bb99a9b',
           'run': 40729119885, 'artifact': 12835035241,
           'image': 'ghcr.io/ai-workspace-services/billing-service@sha256:66a7e12274a42c929277b68fbee58ff5e4277b21e37d4f8e937ed38b9ac474e4',
           'prefix': 'billing-main-image'}

PR_BILLING = {'repo': 'ai-workspace-services/billing-service', 'sha': '67abf1e64276904374b63ecca837eef07ddb1f9d',
              'base': '4d34c7f36a1d71f29ca31389b250d821b36857ce', 'pr': 57,
              'run': 40844072417, 'artifact': 12883574130, 'build_run': 40843516350,
              'image': 'ghcr.io/ai-workspace-services/billing-service@sha256:5905930286f384acf5b9c4a5e458d81c06646fe833bb840e009c4fb9198a9453',
              'prefix': 'billing-pr-image-evidence', 'filename': 'billing-pr-image-evidence.json',
              'workflow': '.github/workflows/billing-pr-image-evidence.yml'}


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


def check_pr(row):
    pull = api(f"repos/{row['repo']}/pulls/{row['pr']}")
    if (pull.get('state') != 'open' or pull.get('merged') is not False or
        pull.get('head', {}).get('sha') != row['sha'] or
        pull.get('head', {}).get('repo', {}).get('full_name') != row['repo'] or
        pull.get('base', {}).get('ref') != 'main' or pull.get('base', {}).get('sha') != row['base'] or
        pull.get('base', {}).get('repo', {}).get('full_name') != row['repo']):
        raise ValueError('fixed Accounts PR moved')
    if api(f"repos/{row['repo']}/git/ref/heads/main").get('object', {}).get('sha') != row['base']:
        raise ValueError('Accounts Main moved')


def check_pr_build(row):
    repo, rid, aid = row['repo'], row['run'], row['artifact']
    run = api(f'repos/{repo}/actions/runs/{rid}')
    if (run.get('id') != rid or run.get('path', '').split('@')[0] != row['workflow'] or
        run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main' or run.get('head_sha') != row['base'] or
        run.get('run_attempt') != 1 or run.get('status') != 'completed' or run.get('conclusion') != 'success' or
        run.get('repository', {}).get('full_name') != repo or run.get('head_repository', {}).get('full_name') != repo):
        raise ValueError('PR evidence run identity mismatch')
    meta = api(f'repos/{repo}/actions/artifacts/{aid}')
    if (meta.get('id') != aid or meta.get('expired') is not False or meta.get('name') != f"{row['prefix']}-{rid}-1" or
        meta.get('workflow_run', {}).get('id') != rid or meta.get('workflow_run', {}).get('head_sha') != row['base'] or
        type(meta.get('size_in_bytes')) is not int or not 0 < meta['size_in_bytes'] <= 2_000_000):
        raise ValueError('PR evidence artifact identity mismatch')
    archive = api(f'repos/{repo}/actions/artifacts/{aid}/zip', binary=True)
    if meta.get('digest') != 'sha256:' + hashlib.sha256(archive).hexdigest():
        raise ValueError('PR evidence artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if len(entries) != 1 or entries[0].filename != row['filename'] or entries[0].file_size > 100_000:
            raise ValueError('unexpected PR evidence archive')
        proof = json.loads(zipped.read(entries[0]))
    if (proof.get('schema') != 1 or proof.get('evidence_kind') != 'github-pr-run-and-registry' or
        proof.get('repository') != repo or proof.get('source_sha') != row['sha'] or proof.get('image') != row['image'] or
        proof.get('pr') != row['pr'] or proof.get('base_sha') != row['base'] or
        proof.get('producer_run_id') != row['build_run'] or proof.get('producer_attempt') != 1):
        raise ValueError('PR evidence does not match fixed source')
    original = api(f"repos/{repo}/actions/runs/{row['build_run']}")
    if (original.get('id') != row['build_run'] or original.get('head_sha') != row['sha'] or
        original.get('path', '').split('@')[0] != '.github/workflows/ci-pipeline.yml' or
        original.get('event') != 'workflow_dispatch' or original.get('run_attempt') != 1 or
        original.get('status') != 'completed' or original.get('conclusion') != 'success' or
        original.get('head_repository', {}).get('full_name') != repo):
        raise ValueError('PR producer run identity mismatch')


def produce(image_manifest_bytes, profile='main'):
    if profile not in ('main', 'pr-1483', 'pr-both-1483'):
        raise ValueError('unknown baseline profile')
    accounts = PR_ACCOUNTS if profile in ('pr-1483', 'pr-both-1483') else ACCOUNTS
    if profile in ('pr-1483', 'pr-both-1483'):
        check_pr(accounts); check_pr_build(accounts)
    else:
        check_main(accounts); check_build(accounts)
    billing = PR_BILLING if profile == 'pr-both-1483' else BILLING
    if profile == 'pr-both-1483':
        check_pr(billing); check_pr_build(billing)
    else:
        check_main(billing); check_build(billing)
    manifest = source(accounts, 'sql/init/accounts-native.manifest.json')
    native = source(accounts, 'sql/init/accounts-native.sql')
    billing_schema = source(billing, 'sql/billing-service-schema.sql')
    result = inspect(manifest, native, billing_schema, image_manifest_bytes, accounts_sha=accounts['sha'],
                     billing_sha=billing['sha'], accounts_image=accounts['image'])
    if profile in ('pr-1483', 'pr-both-1483'):
        check_pr(accounts)
    else:
        check_main(accounts)
    if profile == 'pr-both-1483':
        check_pr(billing)
    else:
        check_main(billing)
    return {'schema': 'sit-data-baseline/v1', 'result': 'verified', 'feature_id': 'sit-test-1483',
            'namespace': 'feature-sit-test-1483-sit', 'data_baseline_sha256': result['data_baseline_sha256'],
            'inputs': result['inputs'], 'database_status': 'NOT_RUN',
            'source_builds': [{'repository': row['repo'], 'sha': row['sha'], 'run_id': row['run'],
                               'artifact_id': row['artifact']} for row in (accounts, billing)]}


def validate_registry_credential(username, token):
    """Require a classic GitHub token scoped only to read:packages."""
    if not username or not token or any(c in username + token for c in '\r\n'):
        raise ValueError('dedicated GHCR pull identity required')
    request = urllib.request.Request('https://api.github.com/user', headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        scopes = {part.strip() for part in response.headers.get('X-OAuth-Scopes', '').split(',') if part.strip()}
        profile = json.load(response)
    if scopes != {'read:packages'} or profile.get('login', '').lower() != username.lower():
        raise ValueError('GHCR credential must have only read:packages scope')


def main():
    global PHASE
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REPOSITORY') != 'ai-workspace-infra/playbooks' or
        os.environ.get('GITHUB_REF') != 'refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or
        os.environ.get('GITHUB_WORKFLOW_REF') != 'ai-workspace-infra/playbooks/.github/workflows/feature-baseline-1483.yml@refs/heads/main'):
        raise ValueError('protected Main workflow required')
    profile = os.environ.get('BASELINE_PROFILE', 'main')
    if profile not in ('main', 'pr-1483', 'pr-both-1483'):
        raise ValueError('unknown baseline profile')
    accounts = PR_ACCOUNTS if profile in ('pr-1483', 'pr-both-1483') else ACCOUNTS
    PHASE = 'isolated-image'
    validate_registry_credential(os.environ.get('FEATURE_GHCR_USERNAME', ''), os.environ.get('FEATURE_GHCR_TOKEN', ''))
    with tempfile.TemporaryDirectory(prefix='baseline-docker-') as directory:
        config = Path(directory) / 'config.json'
        # The helper receives credentials only through its process environment;
        # the Docker configuration file contains no secret material.
        config.write_text(json.dumps({'credHelpers': {'ghcr.io': 'feature-baseline'}}))
        config.chmod(0o600)
        env = dict(os.environ, DOCKER_CONFIG=directory,
                   PATH=str(Path(__file__).resolve().parent) + os.pathsep + os.environ.get('PATH', ''))
        image = subprocess.run(['docker', 'run', '--rm', '--pull=always', '--platform', 'linux/amd64',
                                '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
                                '--security-opt', 'no-new-privileges', '--entrypoint', '/usr/local/bin/migratectl',
                                accounts['image'], 'native-schema'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, timeout=300, check=True).stdout
    PHASE = 'source-and-build-evidence'
    result = produce(image, profile)
    PHASE = 'artifact-write'
    destination = Path(os.environ['RUNNER_TEMP']) / 'feature-baseline-1483.json'
    destination.write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
    print('Verified fixed baseline inputs; database status: NOT_RUN')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'::error::Feature baseline verification failed (phase={PHASE}, category={type(error).__name__})', file=sys.stderr)
        sys.exit(1)
