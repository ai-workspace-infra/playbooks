#!/usr/bin/env python3
"""CI adapter: authenticated shared Dev transport + fixed database seed role."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import dev_feature_candidate as candidate
from dev_k3s_bootstrap import validate_cmdb
from dev_ssh_binding import binding, run
from run_dev_feature_flux import validate_candidate_receipt, validate_prepare_receipt
from tagged_seed_baseline import validate_receipt_inputs

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def read_bound(path, digest):
    require(re.fullmatch('[0-9a-f]{64}', digest or ''), 'artifact checksum required')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, 'artifact checksum mismatch')
    return json.loads(data)


def require_same_run_prepare(prior, env):
    source = prior.get('registry', {}).get('oci_mapping', {}).get('binding', {}).get('source', {})
    repo, workflow_sha = env.get('GITHUB_REPOSITORY'), env.get('GITHUB_SHA')
    run_id, attempt = env.get('GITHUB_RUN_ID'), env.get('GITHUB_RUN_ATTEMPT')
    require(repo in ('ai-workspace-infra/platform-ops-toolkit', 'ai-workspace-infra/Platform-Orchestrator') and
            re.fullmatch(r'[0-9a-f]{40}', workflow_sha or '') and
            re.fullmatch(r'[1-9][0-9]*', run_id or '') and re.fullmatch(r'[1-9][0-9]*', attempt or '') and
            source.get('repository') == repo and source.get('workflow_ref') == repo + '/.github/workflows/feature-lane-deploy.yml@refs/heads/main' and
            source.get('workflow_sha') == workflow_sha and str(source.get('run_id')) == run_id and
            str(source.get('run_attempt')) == attempt and str(prior.get('run_id')) == run_id and
            str(prior.get('run_attempt')) == attempt, 'same-run tagged prepare producer required')


def execute(a):
    require(a.phase in ('seed-admin', 'initialize'), 'explicit seed phase required')
    cmdb = validate_cmdb(read_bound(a.cmdb, a.cmdb_sha256), a.instance_id)
    control = validate_candidate_receipt(read_bound(a.candidate_receipt, a.candidate_sha256),
        a.gitops_sha, a.stage, os.environ.get('GITHUB_RUN_ID'))
    tagged = control.get('schema_version') == 'dev-feature-candidate/v2'
    if tagged:
        # Candidate provenance has already been checked above. Bind the exact
        # projected baseline and namespace incarnation before considering a
        # future tagged execution path. This check alone grants no authority.
        validate_receipt_inputs(control, a.namespace_uid)
        require(a.prepare_receipt is not None, 'authenticated prepare receipt required')
        prior = read_bound(a.prepare_receipt, a.prepare_sha256)
        require_same_run_prepare(prior, os.environ)
        projection = control['projection']
        require(projection['tagged_lane'].get('feature_id') == 'sit-test-1483',
                'tagged seed is authorized only for the reviewed rehearsal')
        observation = candidate.validate_tree(a.gitops_root, a.gitops_sha, a.stage, False, projection)
        owner_sha = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip()
        config = {'instance_id': a.instance_id, 'namespace': observation['namespace'],
                  'node_uid': a.node_uid, 'cluster_uid': a.cluster_uid, 'namespace_uid': a.namespace_uid,
                  'candidate_sha': a.gitops_sha, 'platform_sha': a.platform_sha,
                  'owner_sha': owner_sha, 'manifest_sha256': observation['manifest_sha256'],
                  'tagged_lane': projection['tagged_lane']}
        runtime_mapping = validate_prepare_receipt(prior, config, observation)
    else:
        require(control.get('schema_version') == 'dev-feature-candidate/v1', 'unknown candidate receipt schema')
        observation = candidate.validate_tree(a.gitops_root, a.gitops_sha, a.stage)
        owner_sha = run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()
    require(candidate.SCOPE.SHA.fullmatch(a.platform_sha), 'immutable platform SHA required')
    config = {'phase':a.phase,'stage':a.stage,'namespace':observation['namespace'],
        'candidate_sha':a.gitops_sha,'platform_sha':a.platform_sha,'owner_sha':owner_sha,
        'manifest_sha256':observation['manifest_sha256'],'instance_id':a.instance_id,
        'node_uid':a.node_uid,'cluster_uid':a.cluster_uid,'namespace_uid':a.namespace_uid}
    if tagged:
        config.update(tagged_lane=projection['tagged_lane'], runtime_mapping=runtime_mapping,
                      data_baseline_inputs=control['data_baseline_inputs'],
                      data_baseline_sha256=control['data_baseline_sha256'])
    # Reuse A's validator for UID/SHA/namespace constraints without changing A's
    # approval or namespace ownership behavior. Actual live scope is rechecked.
    candidate.SCOPE.validate({**config,'mode':'prepare','environment':a.stage,'node_name':'dev-k3s-v5'})
    a.receipt.unlink(missing_ok=True)
    with binding(cmdb,a.instance_id,tagged=tagged) as transport:
        report = transport.directory/'seed-receipt.json'
        variables = transport.directory/'seed-vars.json'
        variables.write_text(json.dumps({'dev_web_saas_seed_target':'dev-k3s-v5',
            'dev_web_saas_seed_request':config,'dev_web_saas_seed_report':str(report)}))
        variables.chmod(0o600)
        env = {**transport.env,'ANSIBLE_ROLES_PATH':str(ROOT/'roles')}
        command = ['ansible-playbook','-i',str(ROOT/'inventory/terraform_cmdb.py'),
            str(ROOT/'dev_web_saas_seed.yml'),'--limit','dev-k3s-v5','-e','@'+str(variables)]
        run([*command,'--syntax-check'],env)
        run(command,env)
        require(report.is_file(), 'no actual seed owner receipt')
        result = json.loads(report.read_text())
        expected = 'verified' if a.phase == 'initialize' else 'admin_secret_prepared'
        require(result.get('result') == expected and result.get('namespace') == config['namespace'] and result.get('namespace_uid') == a.namespace_uid,
                'actual seed phase did not verify')
        if a.phase == 'initialize':
            require(result.get('schema_version') == 'dev-lane-seed/v1' and
                    all(result.get(k) == config[k] for k in ('candidate_sha','instance_id','node_uid','cluster_uid')),
                    'seed receipt target mismatch')
        result.update(owner_sha=owner_sha, platform_sha=a.platform_sha,
            cmdb_sha256=a.cmdb_sha256,candidate_receipt_sha256=a.candidate_sha256,
            candidate_control_plane=control,phase=a.phase,
            run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
            business_acceptance='NOT_RUN')
        a.receipt.parent.mkdir(parents=True,exist_ok=True)
        a.receipt.write_text(json.dumps(result,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser()
    for key in ('phase','stage','instance-id','gitops-sha','platform-sha','cmdb-sha256',
                'candidate-sha256','node-uid','cluster-uid','namespace-uid'):
        p.add_argument('--'+key,required=True)
    for key in ('cmdb','candidate-receipt','gitops-root','receipt'):
        p.add_argument('--'+key,required=True,type=Path)
    p.add_argument('--prepare-receipt', type=Path)
    p.add_argument('--prepare-sha256')
    execute(p.parse_args())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Dev seed rejected scope or execution ('+type(error).__name__+'); raw output suppressed') from None
