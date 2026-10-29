#!/usr/bin/env python3
"""Run a narrowly configured Vault JWT workload identity operation.

Receipt provenance, host-key attestation and selection of an approved caller are
owned by the invoking pipeline; this role only consumes those verified inputs.
"""
import json
import os
import sys

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
    runner = remote_type(config['host'], config['known_hosts'], spec, config['runtime'], config['ssh_user'], env=child_env)
    data = collector(config['cluster_uid'], config['node_uid'], config['expected_version'],
                     config['node_name'], spec, run=runner)
    if config['mode'] == 'configure':
        if not env.get('VAULT_TOKEN'):
            raise ValueError('scoped administrator token required')
        admin = client_type(config['vault_address'], env['VAULT_TOKEN'])
        response = vault_contract.expect(admin.request('GET','auth/token/lookup-self'),{200})['data']
        if (set(response.get('policies') or []) != {config['admin_policy']}
                or not 0 < response.get('ttl',0) <= 1200):
            raise ValueError('unexpected administrator token scope')
        vault_contract.provision(admin,spec,data['issuer'],data['public_keys'])
        result={'result':'VAULT_WORKLOAD_IDENTITY_CONFIGURED'}
    else:
        result=vault_contract.verify(lambda token:client_type(config['vault_address'],token),spec,data['issuer'],
                                     data['good_jwt'],data['wrong_audience_jwt'],data['wrong_service_account_jwt'])
    result.update({'cluster_uid':config['cluster_uid'],'node_uid':config['node_uid'],'node_name':config['node_name']})
    return result


def main():
    try:
        result=execute(json.load(sys.stdin))
        print(json.dumps(result,sort_keys=True))
    except (KeyError,TypeError,ValueError,OSError,cluster_identity.ClusterError,vault_contract.VaultError):
        raise SystemExit('Vault workload identity operation failed') from None


if __name__=='__main__':main()
