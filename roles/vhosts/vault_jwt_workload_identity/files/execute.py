#!/usr/bin/env python3
"""Run a narrowly configured Vault JWT workload identity operation.

Receipt provenance, host-key attestation and selection of an approved caller are
owned by the invoking pipeline; this role only consumes those verified inputs.
"""
import json
import os
import sys
from pathlib import Path

import cluster_identity
import remote_kubectl
import vault_contract


def execute(config, env=None, collector=cluster_identity.collect, remote_type=remote_kubectl.RemoteKubectl,
            client_type=vault_contract.Client):
    env = os.environ if env is None else env
    spec = vault_contract.IdentitySpec(**config['identity'])
    if config['mode'] not in ('configure', 'verify'):
        raise ValueError('unsupported operation')
    child_env = {k: env[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSH_AUTH_SOCK') if k in env}
    if not child_env.get('SSH_AUTH_SOCK'):
        raise ValueError('isolated SSH agent required')
    stage = 'cluster_identity'
    try:
        runner = remote_type(config['host'], config['known_hosts'], spec, config['runtime'], config['ssh_user'], env=child_env)
        data = collector(config['cluster_uid'], config['node_uid'], config['expected_version'],
                         config['node_name'], spec, run=runner)
        if config['mode'] == 'configure':
            stage = 'administrator_scope'
        else:
            stage = 'workload_login_and_path_checks'
    except (KeyError, TypeError, ValueError, OSError, cluster_identity.ClusterError, vault_contract.VaultError):
        raise RuntimeError('stage=' + stage) from None
    if config['mode'] == 'configure':
        try:
            if not env.get('VAULT_TOKEN'):
                raise ValueError('scoped administrator token required')
            admin = client_type(config['vault_address'], env['VAULT_TOKEN'])
            response = vault_contract.expect(admin.request('GET','auth/token/lookup-self'),{200})['data']
            if (set(response.get('policies') or []) != {config['admin_policy']}
                    or not 0 < response.get('ttl',0) <= 1200):
                raise ValueError('unexpected administrator token scope')
        except (KeyError,TypeError,ValueError,OSError,vault_contract.VaultError):
            raise RuntimeError('stage=administrator_scope') from None
        try:
            vault_contract.provision(admin,spec,data['issuer'],data['public_keys'])
        except vault_contract.VaultError:
            raise RuntimeError('stage=vault_provision') from None
        result={'result':'VAULT_WORKLOAD_IDENTITY_CONFIGURED'}
    else:
        try:
            result=vault_contract.verify(lambda token:client_type(config['vault_address'],token),spec,data['issuer'],
                                         data['good_jwt'],data['wrong_audience_jwt'],data['wrong_service_account_jwt'])
        except vault_contract.VaultError:
            raise RuntimeError('stage=workload_login_and_path_checks') from None
    result.update({'cluster_uid':config['cluster_uid'],'node_uid':config['node_uid'],'node_name':config['node_name']})
    return result


def main():
    try:
        result=execute(json.load(sys.stdin))
        print(json.dumps(result,sort_keys=True))
    except RuntimeError as exc:
        stage = str(exc)
        if stage not in ('stage=cluster_identity', 'stage=administrator_scope', 'stage=workload_login_and_path_checks', 'stage=vault_provision'):
            stage = 'stage=unexpected'
        failure_path = os.environ.get('WORKLOAD_IDENTITY_FAILURE')
        if failure_path:
            Path(failure_path).write_text(json.dumps({'result':'FAILED','stage':stage.removeprefix('stage=')})+'\n')
        print('workload_identity_failure ' + stage, file=sys.stderr)
        raise SystemExit('Vault workload identity operation failed') from None
    except (KeyError,TypeError,ValueError,OSError,cluster_identity.ClusterError,vault_contract.VaultError):
        failure_path = os.environ.get('WORKLOAD_IDENTITY_FAILURE')
        if failure_path:
            Path(failure_path).write_text(json.dumps({'result':'FAILED','stage':'input_validation'})+'\n')
        print('workload_identity_failure stage=input_validation', file=sys.stderr)
        raise SystemExit('Vault workload identity operation failed') from None


if __name__=='__main__':main()
