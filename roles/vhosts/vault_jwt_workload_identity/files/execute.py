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
import flux_release
import remote_kubectl
import vault_contract


def execute(config, env=None, collector=cluster_identity.collect, remote_type=remote_kubectl.RemoteKubectl,
            client_type=vault_contract.Client):
    env = os.environ if env is None else env
    spec = vault_contract.IdentitySpec(**config['identity'])
    if config['mode'] not in ('configure', 'verify', 'release-root', 'release-eso', 'release-identity'):
        raise ValueError('unsupported operation')
    child_env = {k: env[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSH_AUTH_SOCK') if k in env}
    if not child_env.get('SSH_AUTH_SOCK'):
        raise ValueError('isolated SSH agent required')
    stage = 'cluster_identity'
    try:
        runner = remote_type(config['host'], config['known_hosts'], spec, config['runtime'], config['ssh_user'], env=child_env)
        if config['mode'].startswith('release-'):
            stage = 'release_cluster_identity'
            cluster = cluster_identity.verify_cluster_target(config['cluster_uid'], config['node_uid'],
                                                              config['expected_version'], config['node_name'], run=runner)
            return flux_release.release(config, runner, cluster)
        data = collector(config['cluster_uid'], config['node_uid'], config['expected_version'],
                         config['node_name'], spec, run=runner)
        if config['mode'] == 'configure':
            stage = 'administrator_scope'
        else:
            stage = 'workload_login_and_path_checks'
    except cluster_identity.ClusterError as exc:
        allowed = {'namespace_read','nodes_read','node_receipt_match','service_accounts_read',
                   'oidc_discovery','token_requests','cluster_identity_mismatch'}
        release_stages = {'release_cluster_identity','namespace_read','nodes_read','node_receipt_match'}
        release_codes = {'release_declaration','live_graph_read','live_graph_mismatch','live_gate_order',
                         'config_source_ready','root_ready','external-secrets_ready',
                         'identity_service_accounts','release_source_revision','release_root_revision',
                         'live_gate_mismatch','vault_config_source_ready','identity_ready',
                         'release_source_apply','release_root_apply','release_revision_validation',
                         'release_post_reconcile_graph'}
        if config['mode'].startswith('release-'):
            if exc.stage in release_codes:
                stage = exc.stage if exc.stage.startswith('release_') else 'release_' + exc.stage
            else:
                stage = 'release_' + exc.stage if exc.stage in release_stages else 'release_cluster_identity'
        else:
            stage = 'cluster_' + exc.stage if exc.stage in allowed else 'cluster_identity'
        error = RuntimeError('stage=' + stage)
        error.safe_diagnostic = exc.diagnostic
        raise error from None
    except (KeyError, TypeError, ValueError, OSError, vault_contract.VaultError):
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
        except vault_contract.VaultError as exc:
            stage = 'vault_' + exc.stage if exc.stage in {'policy','mount','config','role'} else 'vault_provision'
            error = RuntimeError('stage=' + stage)
            error.safe_diagnostic = exc.diagnostic
            raise error from None
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
        stages = {'stage=cluster_identity','stage=cluster_namespace_read','stage=cluster_nodes_read',
                  'stage=cluster_node_receipt_match','stage=cluster_service_accounts_read',
                  'stage=cluster_oidc_discovery','stage=cluster_token_requests','stage=cluster_identity_mismatch',
                  'stage=administrator_scope','stage=workload_login_and_path_checks','stage=vault_provision',
                  'stage=vault_policy','stage=vault_mount','stage=vault_config','stage=vault_role',
                  'stage=release_cluster_identity','stage=release_namespace_read','stage=release_nodes_read',
                  'stage=release_node_receipt_match','stage=release_declaration','stage=release_live_graph_read',
                  'stage=release_live_graph_mismatch','stage=release_live_gate_order','stage=release_config_source_ready',
                  'stage=release_root_ready','stage=release_external-secrets_ready','stage=release_identity_service_accounts',
                  'stage=release_source_revision','stage=release_root_revision','stage=release_live_gate_mismatch',
                  'stage=release_vault_config_source_ready','stage=release_identity_ready',
                  'stage=release_source_apply','stage=release_root_apply',
                  'stage=release_revision_validation','stage=release_post_reconcile_graph'}
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
                elif (record['stage'] == 'release_live_gate_order'
                      and diagnostic.get('operation') == 'live_flux_gate_state'):
                    safe_names = {
                        'GitRepository/flux-system/config-prod',
                        'GitRepository/flux-system/vault-prod-config',
                        'GitRepository/flux-system/postgresql-prod-config',
                        'GitRepository/flux-system/ingress-prod-config',
                        'GitRepository/flux-system/observability-prod-config',
                        'GitRepository/flux-system/web-saas-prod-config',
                        'Kustomization/flux-system/prod-k3s-v6',
                        'Kustomization/flux-system/external-secrets',
                        'Kustomization/flux-system/vault-tls-identity',
                        'Kustomization/flux-system/vault-tls-bootstrap',
                        'Kustomization/flux-system/vault-prod',
                        'Kustomization/flux-system/postgresql-prod',
                        'Kustomization/flux-system/ingress-prod',
                        'Kustomization/flux-system/observability-prod',
                        'Kustomization/flux-system/web-saas-prod',
                        'HelmRelease/external-secrets/external-secrets',
                        'HelmRelease/vault-prod/vault-prod',
                        'HelmRelease/postgresql-prod/postgresql-prod',
                        'HelmRelease/ingress-prod/caddy-ingress',
                        'HelmRelease/observability-prod/observability',
                        'HelmRelease/observability-prod/observability-agent',
                        'HelmRelease/observability-prod/datakit',
                        'HelmRelease/web-saas-prod/web-saas',
                    }
                    active = diagnostic.get('active')
                    if (isinstance(active, list) and all(name in safe_names for name in active)
                            and len(active) == len(set(active))):
                        record['diagnostic'] = {'operation': 'live_flux_gate_state',
                                                'active': sorted(active)}
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
