"""Release one reviewed V6 Flux gate through the pinned cluster root."""
import json
from pathlib import Path
import re
import time

import yaml

from cluster_identity import ClusterError

API = {
    'GitRepository': 'source.toolkit.fluxcd.io/v1',
    'Kustomization': 'kustomize.toolkit.fluxcd.io/v1',
    'HelmRelease': 'helm.toolkit.fluxcd.io/v2',
}
STAGES = ('root', 'eso', 'identity')
BASE_ACTIVE = {('GitRepository', 'flux-system', 'config-prod'),
               ('Kustomization', 'flux-system', 'prod-k3s-v6')}
STAGE_ACTIVE = {
    'root': BASE_ACTIVE,
    'eso': BASE_ACTIVE | {('Kustomization', 'flux-system', 'external-secrets'),
                          ('HelmRelease', 'external-secrets', 'external-secrets')},
    'identity': BASE_ACTIVE | {('Kustomization', 'flux-system', 'external-secrets'),
                               ('HelmRelease', 'external-secrets', 'external-secrets'),
                               ('GitRepository', 'flux-system', 'vault-prod-config'),
                               ('Kustomization', 'flux-system', 'vault-tls-identity')},
}


def ident(doc):
    meta = doc['metadata']
    return doc['kind'], meta.get('namespace', 'default'), meta['name']


def documents(path):
    if not path.is_file():
        raise ClusterError('reviewed GitOps release file is missing', 'release_declaration')
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except (OSError, yaml.YAMLError):
        raise ClusterError('reviewed GitOps release file is invalid', 'release_declaration') from None


def graph(checkout, tag, stage):
    if stage not in STAGES or not re.fullmatch(r'v[0-9][A-Za-z0-9.+-]*', tag):
        raise ClusterError('unsupported V6 release gate', 'release_declaration')
    root_dir = (checkout / 'environments/clusters/prod-k3s-v6').resolve()
    if not root_dir.is_relative_to(checkout) or not root_dir.is_dir():
        raise ClusterError('V6 root path is invalid', 'release_declaration')
    source_path, root_path = root_dir / 'source.yaml', root_dir / 'root.yaml'
    source_docs, root_docs = documents(source_path), documents(root_path)
    if len(source_docs) != 1 or len(root_docs) != 1:
        raise ClusterError('V6 source/root declarations must be singular', 'release_declaration')
    source, root = source_docs[0], root_docs[0]
    if (source.get('apiVersion') != API['GitRepository'] or source.get('kind') != 'GitRepository'
            or ident(source) != ('GitRepository', 'flux-system', 'config-prod')
            or source.get('spec', {}).get('url') != 'https://github.com/ai-workspace-infra/gitops.git'
            or source.get('spec', {}).get('ref') != {'tag': tag}
            or source.get('spec', {}).get('suspend') is not False):
        raise ClusterError('V6 management source differs from the reviewed tag', 'release_declaration')
    if (root.get('apiVersion') != API['Kustomization'] or root.get('kind') != 'Kustomization'
            or ident(root) != ('Kustomization', 'flux-system', 'prod-k3s-v6')
            or root.get('spec', {}).get('sourceRef') != {'kind': 'GitRepository', 'name': 'config-prod'}
            or root.get('spec', {}).get('path') != './environments/clusters/prod-k3s-v6'
            or root.get('spec', {}).get('suspend') is not False):
        raise ClusterError('V6 management root differs from the reviewed tag', 'release_declaration')

    all_docs = source_docs + root_docs
    for file_path in root_dir.glob('*.yaml'):
        if file_path in {source_path, root_path}:
            continue
        all_docs.extend(documents(file_path))
    children = [d for d in all_docs if d.get('apiVersion') == API['Kustomization']
                and ident(d) != ident(root)]
    for child in children:
        child_path = child.get('spec', {}).get('path', '')
        path = (checkout / child_path.removeprefix('./')).resolve()
        if not path.is_relative_to(checkout) or not path.is_dir():
            raise ClusterError('V6 child path escapes the reviewed checkout', 'release_declaration')
        for leaf in path.rglob('*.yaml'):
            all_docs.extend(documents(leaf))

    flux = [d for d in all_docs if d.get('apiVersion') == API.get(d.get('kind'))]
    keyed = {ident(d): d for d in flux}
    if len(keyed) != len(flux) or (sum(k[0] == 'GitRepository' for k in keyed) != 6
                                    or sum(k[0] == 'Kustomization' for k in keyed) != 9
                                    or sum(k[0] == 'HelmRelease' for k in keyed) != 8):
        raise ClusterError('V6 Flux graph shape differs from the reviewed contract', 'release_declaration')
    active = {key for key, doc in keyed.items() if doc.get('spec', {}).get('suspend') is False}
    if active != STAGE_ACTIVE[stage]:
        raise ClusterError('V6 active gate set differs from the requested stage', 'release_declaration')
    for key, doc in keyed.items():
        if key not in active and doc.get('spec', {}).get('suspend') is not True:
            raise ClusterError('every unreleased V6 Flux object must stay suspended', 'release_declaration')
    identity = keyed.get(('Kustomization', 'flux-system', 'vault-tls-identity'))
    if identity and not any(d.get('name') == 'external-secrets'
                            for d in identity.get('spec', {}).get('dependsOn', [])):
        raise ClusterError('TLS identity gate must depend on ESO', 'release_declaration')
    return source, root, keyed, active


def live_graph(run, expected, require_full=True):
    try:
        actual = json.loads(run('get', 'gitrepositories,kustomizations,helmreleases', '-A', '-o', 'json'))['items']
        observed = {ident(d): d for d in actual if ident(d) in expected}
    except ClusterError:
        raise ClusterError('live V6 Flux graph could not be read', 'live_graph_read') from None
    except (KeyError, TypeError, ValueError):
        raise ClusterError('live V6 Flux graph could not be read', 'live_graph_read') from None
    required = set(expected) if require_full else BASE_ACTIVE
    if not required.issubset(observed.keys()):
        raise ClusterError('live V6 Flux object identities differ from the reviewed graph', 'live_graph_mismatch')
    active = {key for key, doc in observed.items() if doc.get('spec', {}).get('suspend') is False}
    return observed, active


def ready(obj):
    return any(c.get('type') == 'Ready' and c.get('status') == 'True'
               for c in obj.get('status', {}).get('conditions', []))


def wait_ready(run, key, stage, deadline):
    kind, namespace, name = key
    command = {
        'GitRepository': ('get', 'gitrepository', name, '-n', namespace, '-o', 'json'),
        'Kustomization': ('get', 'kustomization', name, '-n', namespace, '-o', 'json'),
        'HelmRelease': ('get', 'helmrelease', name, '-n', namespace, '-o', 'json'),
    }[kind]
    while time.monotonic() < deadline:
        try:
            obj = json.loads(run(*command))
        except ClusterError:
            raise ClusterError(stage, stage) from None
        except (ValueError, TypeError):
            raise ClusterError(stage, stage) from None
        if ready(obj):
            return
        time.sleep(10)
    raise ClusterError(stage, stage)


def release(config, runner, cluster):
    stage = config['release_stage']
    phase = 'release_declaration'
    try:
        checkout = Path(config['gitops_checkout']).resolve()
        tag = config['gitops_tag']
        source, root, expected, active = graph(checkout, tag, stage)
        phase = 'live_graph_read'
        observed, current = live_graph(runner, expected, require_full=stage != 'root')
        prior = {'root': set(), 'eso': STAGE_ACTIVE['root'],
                 'identity': STAGE_ACTIVE['eso']}[stage]
        # A failed run may have applied only part or all of this exact stage.
        # Continue only when every active gate belongs to the requested stage.
        if not prior.issubset(active) or not current.issubset(active):
            raise ClusterError('live V6 Flux gates are outside the requested stage', 'live_gate_order')
        if stage in ('eso', 'identity'):
            if not ready(observed[('Kustomization', 'flux-system', 'prod-k3s-v6')]):
                raise ClusterError('V6 root is not Ready before the next gate', 'live_gate_order')
        if stage == 'identity':
            if not ready(observed[('Kustomization', 'flux-system', 'external-secrets')]) or not ready(
                    observed[('HelmRelease', 'external-secrets', 'external-secrets')]):
                raise ClusterError('ESO is not Ready before the identity gate', 'live_gate_order')
        phase = 'release_source_apply'
        runner.apply_manifest((checkout / 'environments/clusters/prod-k3s-v6/source.yaml').read_text())
        phase = 'release_root_apply'
        runner.apply_manifest((checkout / 'environments/clusters/prod-k3s-v6/root.yaml').read_text())
        deadline = time.monotonic() + 900
        phase = 'config_source_ready'
        wait_ready(runner, ('GitRepository', 'flux-system', 'config-prod'), 'config_source_ready', deadline)
        phase = 'root_ready'
        wait_ready(runner, ('Kustomization', 'flux-system', 'prod-k3s-v6'), 'root_ready', deadline)
    except ClusterError:
        raise
    except Exception:
        raise ClusterError('V6 Flux stage execution failed', phase) from None
    phase = 'release_revision_validation'
    try:
        source_live = json.loads(runner('get', 'gitrepository', 'config-prod', '-n', 'flux-system', '-o', 'json'))
        root_live = json.loads(runner('get', 'kustomization', 'prod-k3s-v6', '-n', 'flux-system', '-o', 'json'))
    except ClusterError:
        raise ClusterError('Flux revision evidence could not be read', 'release_source_revision') from None
    if config['gitops_sha'] not in source_live.get('status', {}).get('artifact', {}).get('revision', ''):
        raise ClusterError('config-prod artifact revision differs from the immutable release', 'release_source_revision')
    if config['gitops_sha'] not in root_live.get('status', {}).get('lastAppliedRevision', ''):
        raise ClusterError('V6 root revision differs from the immutable release', 'release_root_revision')
    readiness = [('GitRepository', 'flux-system', 'config-prod'),
                 ('Kustomization', 'flux-system', 'prod-k3s-v6')]
    if stage in ('eso', 'identity'):
        readiness.extend([('Kustomization', 'flux-system', 'external-secrets'),
                          ('HelmRelease', 'external-secrets', 'external-secrets')])
    if stage == 'identity':
        readiness.extend([('GitRepository', 'flux-system', 'vault-prod-config'),
                          ('Kustomization', 'flux-system', 'vault-tls-identity')])
    try:
        for key in readiness[2:]:
            marker = {
                ('Kustomization', 'flux-system', 'external-secrets'): 'external-secrets_ready',
                ('HelmRelease', 'external-secrets', 'external-secrets'): 'external-secrets_ready',
                ('GitRepository', 'flux-system', 'vault-prod-config'): 'vault_config_source_ready',
                ('Kustomization', 'flux-system', 'vault-tls-identity'): 'identity_ready',
            }[key]
            wait_ready(runner, key, marker, deadline)
        phase = 'release_post_reconcile_graph'
        actual, actual_active = live_graph(runner, expected)
        if actual_active != active:
            raise ClusterError('live V6 Flux gate state differs after reconciliation', 'live_gate_mismatch')
        if stage == 'identity':
            phase = 'identity_service_accounts'
            try:
                sa = json.loads(runner('get', 'serviceaccount', 'vault-tls-reader', '-n', 'vault-prod', '-o', 'json'))
                probe = json.loads(runner('get', 'serviceaccount', 'default', '-n', 'vault-prod', '-o', 'json'))
                if sa['metadata']['name'] != 'vault-tls-reader' or probe['metadata']['name'] != 'default':
                    raise ValueError()
            except Exception:
                raise ClusterError('TLS reader service accounts are not present', 'identity_service_accounts') from None
    except ClusterError:
        raise
    except Exception:
        raise ClusterError('V6 Flux stage verification failed', phase) from None
    return {'result': 'V6_GITOPS_STAGE_READY', 'stage': stage, 'gitops_tag': tag,
            'gitops_sha': config['gitops_sha'], 'cluster_uid': cluster['cluster_uid'],
            'node_uid': cluster['node_uid'],
            'active_flux_objects': [f'{k[0]}/{k[1]}/{k[2]}' for k in sorted(active)],
            'ready_flux_objects': [f'{k[0]}/{k[1]}/{k[2]}' for k in readiness]}
