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
from run_dev_feature_flux import validate_candidate_receipt

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def read_bound(path, digest):
    require(re.fullmatch('[0-9a-f]{64}', digest or ''), 'artifact checksum required')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, 'artifact checksum mismatch')
    return json.loads(data)


def execute(a):
    require(a.phase in ('seed-admin', 'initialize'), 'explicit seed phase required')
    cmdb = validate_cmdb(read_bound(a.cmdb, a.cmdb_sha256), a.instance_id)
    control = validate_candidate_receipt(read_bound(a.candidate_receipt, a.candidate_sha256),
        a.gitops_sha, a.stage, os.environ.get('GITHUB_RUN_ID'))
    # This owner still binds its database, images and schema to the referral
    # lane. Never let a tagged receipt fall through to that fixed target.
    require(control.get('schema_version') == 'dev-feature-candidate/v1',
            'tagged lane database seed owner is not implemented')
    observation = candidate.validate_tree(a.gitops_root, a.gitops_sha, a.stage)
    owner_sha = run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()
    require(candidate.SCOPE.SHA.fullmatch(a.platform_sha), 'immutable platform SHA required')
    config = {'phase':a.phase,'stage':a.stage,'namespace':observation['namespace'],
        'candidate_sha':a.gitops_sha,'platform_sha':a.platform_sha,'owner_sha':owner_sha,
        'manifest_sha256':observation['manifest_sha256'],'instance_id':a.instance_id,
        'node_uid':a.node_uid,'cluster_uid':a.cluster_uid,'namespace_uid':a.namespace_uid}
    # Reuse A's validator for UID/SHA/namespace constraints without changing A's
    # approval or namespace ownership behavior. Actual live scope is rechecked.
    candidate.SCOPE.validate({**config,'mode':'prepare','environment':a.stage,'node_name':'dev-k3s-v5'})
    a.receipt.unlink(missing_ok=True)
    with binding(cmdb,a.instance_id) as transport:
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
    execute(p.parse_args())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Dev seed rejected scope or execution ('+type(error).__name__+'); raw output suppressed') from None
