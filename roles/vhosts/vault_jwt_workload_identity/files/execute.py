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
    if config['mode'] not in ('configure', 'refresh', 'verify'):
        raise ValueError('unsupported operation')
    child_env = {k: env[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSH_AUTH_SOCK') if k in env}
    if not child_env.get('SSH_AUTH_SOCK'):
        raise ValueError('isolated SSH agent required')
    stage = 'cluster_identity'
    try:
        runner = remote_type(config['host'], config['known_hosts'], spec, config['runtime'], config['ssh_user'], env=child_env)
        data = collector(config['cluster_uid'], config['node_uid'], config['expected_version'],
                         config['node_name'], spec, run=runner)
        if config['mode'] in ('configure', 'refresh'):
            stage = 'administrator_scope'
        else:
            stage = 'workload_login_and_path_checks'
    except cluster_identity.ClusterError as exc:
        allowed = {'namespace_read','nodes_read','node_receipt_match','service_accounts_read',
                   'oidc_discovery','token_requests','cluster_identity_mismatch'}
        stage = 'cluster_' + exc.stage if exc.stage in allowed else 'cluster_identity'
        error = RuntimeError('stage=' + stage)
        error.safe_diagnostic = exc.diagnostic
        raise error from None
    except (KeyError, TypeError, ValueError, OSError, vault_contract.VaultError):
        raise RuntimeError('stage=' + stage) from None
    if config['mode'] in ('configure', 'refresh'):
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
            if config['mode'] == 'refresh':
                vault_contract.refresh_signing_keys(admin, spec, data['issuer'], data['public_keys'], config['expected_previous_key_sha256'])
                vault_contract.verify(lambda token:client_type(config['vault_address'],token),spec,data['issuer'],
                                      data['good_jwt'],data['wrong_audience_jwt'],data['wrong_service_account_jwt'])
            else:
                vault_contract.provision(admin,spec,data['issuer'],data['public_keys'])
        except vault_contract.VaultError as exc:
            stage = 'vault_' + exc.stage if exc.stage in {'policy','mount','config','role'} else 'vault_provision'
            error = RuntimeError('stage=' + stage)
            error.safe_diagnostic = exc.diagnostic
            raise error from None
        result={'result': 'VAULT_WORKLOAD_IDENTITY_REFRESHED_AND_VERIFIED' if config['mode'] == 'refresh' else 'VAULT_WORKLOAD_IDENTITY_CONFIGURED'}
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
        stages = {'stage=cluster_identity','stage=cluster_namespace_read','stage=cluster_nodes_read',
                  'stage=cluster_node_receipt_match','stage=cluster_service_accounts_read',
                  'stage=cluster_oidc_discovery','stage=cluster_token_requests','stage=cluster_identity_mismatch',
                  'stage=administrator_scope','stage=workload_login_and_path_checks','stage=vault_provision',
                  'stage=vault_policy','stage=vault_mount','stage=vault_config','stage=vault_role',}
        if stage not in stages:
            stage = 'stage=unexpected'
        failure_path = os.environ.get('WORKLOAD_IDENTITY_FAILURE')
        if failure_path:
            record = {'result':'FAILED','stage':stage.removeprefix('stage=')}
            diagnostic = getattr(exc, 'safe_diagnostic', None)
            if isinstance(diagnostic, dict):
                allowed = {'auth_mount_list', 'auth_mount_inspect', 'auth_mount_create',
                           'auth_mount_capabilities'}
                operation = diagnostic.get('operation')
                if operation in allowed:
                    safe = {'operation': operation}
                    if type(diagnostic.get('http_status')) is int and 100 <= diagnostic['http_status'] <= 599:
                        safe['http_status'] = diagnostic['http_status']
                    if diagnostic.get('reason') == 'unexpected_mount_type':
                        safe['reason'] = 'unexpected_mount_type'
                    if diagnostic.get('reason') == 'unexpected_capability_response':
                        safe['reason'] = 'unexpected_capability_response'
                    missing = diagnostic.get('missing')
                    if (isinstance(missing, list) and all(cap in {'create', 'update', 'sudo'} for cap in missing)
                            and len(missing) == len(set(missing))):
                        safe['missing'] = sorted(missing)
                    if len(safe) > 1:
                        record['diagnostic'] = safe
            Path(failure_path).write_text(json.dumps(record,sort_keys=True)+'\n')
        print('workload_identity_failure ' + stage, file=sys.stderr)
        raise SystemExit('Vault workload identity operation failed') from None
    except (KeyError,TypeError,ValueError,OSError,cluster_identity.ClusterError,vault_contract.VaultError):
        failure_path = os.environ.get('WORKLOAD_IDENTITY_FAILURE')
        if failure_path:
            Path(failure_path).write_text(json.dumps({'result':'FAILED','stage':'input_validation'})+'\n')
        print('workload_identity_failure stage=input_validation', file=sys.stderr)
        raise SystemExit('Vault workload identity operation failed') from None


if __name__=='__main__':main()
