#!/usr/bin/env python3
"""Configuration-only Grafana export/render/verification; source APIs are GET-only."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import yaml

ALLOWED_DS = {'name', 'uid', 'type', 'access', 'url', 'isDefault', 'basicAuth', 'basicAuthUser', 'user', 'database', 'jsonData'}
ENV_REF = re.compile(r'^\$\{[A-Z][A-Z0-9_]*\}$')

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

def api_get(base, path, auth):
    if urllib.parse.urlsplit(base).scheme != 'https':
        raise ValueError('Grafana API access requires HTTPS')
    if not path.startswith('/api/') or '..' in path:
        raise ValueError('Only bounded Grafana configuration GET paths are supported')
    headers = {}
    if auth.get('token'):
        headers['Authorization'] = 'Bearer ' + auth['token']
    else:
        headers['Authorization'] = 'Basic ' + base64.b64encode((auth['username'] + ':' + auth['password']).encode()).decode()
    with urllib.request.urlopen(urllib.request.Request(base.rstrip('/') + path, headers=headers, method='GET'), timeout=30) as response:
        return json.load(response)

def snapshot(base, auth, app_ids):
    result = {'source': base.rstrip('/'), 'health': api_get(base, '/api/health', auth),
              'folders': api_get(base, '/api/folders?limit=1000', auth),
              'datasources': api_get(base, '/api/datasources', auth),
              'plugins': api_get(base, '/api/plugins', auth)}
    found = []
    for page in range(1, 101):
        batch = api_get(base, f'/api/search?type=dash-db&limit=1000&page={page}', auth)
        found.extend(batch)
        if len(batch) < 1000:
            break
    else:
        raise ValueError('Source dashboard pagination exceeded its bound')
    if len({x['uid'] for x in found}) != len(found):
        raise ValueError('Duplicate source dashboard UIDs')
    result['dashboards'] = [api_get(base, '/api/dashboards/uid/' + urllib.parse.quote(x['uid'], safe=''), auth) for x in found]
    result['app_settings'] = [api_get(base, '/api/plugins/' + urllib.parse.quote(x, safe='') + '/settings', auth) for x in app_ids]
    return result

def rewrite(value, source, target):
    if isinstance(value, str):
        value = value.replace(source.rstrip('/') + '/', target.rstrip('/') + '/')
        if value.startswith('/grafana/'):
            value = value[len('/grafana'):]
        value = value.replace('](/grafana/', '](/').replace('href="/grafana/', 'href="/')
        return value
    if isinstance(value, list):
        return [rewrite(x, source, target) for x in value]
    if isinstance(value, dict):
        return {k: rewrite(v, source, target) for k, v in value.items()}
    return value

def reject_credentials(value):
    raw = json.dumps(value)
    if re.search(r'https?://[^\s/"<>]+:[^\s/@"<>]+@', raw):
        raise ValueError('Credential-bearing URLs cannot enter a configuration bundle')
    if re.search(r'(?i)(?:Bearer|Basic)\s+[A-Za-z0-9/+_.=-]{24,}', raw):
        raise ValueError('Literal authorization credentials cannot enter a configuration bundle')

def cm(name, data, namespace):
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': namespace}, 'data': data}

def semantic(value):
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if k != 'pluginVersion'}
    if isinstance(value, list):
        return [semantic(x) for x in value]
    return value

def variable_definition(value):
    value = json.loads(json.dumps(value))
    for variable in value.get('list', []):
        if variable.get('type') == 'query':
            variable.pop('options', None)
            variable.pop('current', None)
    return semantic(value)

def build(source, options):
    prefix, namespace = options['prefix'], options['namespace']
    if not re.fullmatch('[a-z][a-z0-9-]{0,40}', prefix):
        raise ValueError('Invalid bundle prefix')
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', namespace):
        raise ValueError('Invalid target namespace')
    source_url, target_url = source['source'].rstrip('/'), options['target_url'].rstrip('/')
    if source_url == target_url or not target_url.startswith('https://'):
        raise ValueError('Distinct HTTPS source and target identities are required')
    resources, providers, models = {}, [], {}
    folders = {f['uid']: f['title'] for f in sorted(source['folders'], key=lambda x: x['uid'])}
    if any(f.get('parentUid') for f in source['folders']):
        raise ValueError('Nested source folders need an explicitly reviewed folder mapping')
    grouped = {'': {}}
    for folder_uid in folders:
        grouped[folder_uid] = {}
    reserved = set(options.get('reserved_uids', []))
    for item in sorted(source['dashboards'], key=lambda x: x['dashboard']['uid']):
        d = rewrite(json.loads(json.dumps(item['dashboard'])), source_url, target_url)
        uid = d['uid']
        if not re.fullmatch('[A-Za-z0-9_-]{1,80}', uid) or uid in reserved or uid in models:
            raise ValueError('Conflicting or unsafe dashboard UID')
        for key in ['id', 'version']:
            d.pop(key, None)
        d['editable'] = False
        for variable in d.get('templating', {}).get('list', []):
            if variable.get('type') == 'query':
                variable['options'] = []
                variable['current'] = {}
                variable['refresh'] = 1
        if 'libraryPanel' in json.dumps(d):
            raise ValueError('Library panels require an explicitly exported library model')
        reject_credentials(d)
        folder_uid = item.get('meta', {}).get('folderUid', '')
        if folder_uid not in grouped:
            raise ValueError('Dashboard refers to an unexported folder')
        grouped[folder_uid][uid + '.json'] = json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + '\n'
        models[uid] = {'title': d['title'], 'folder_uid': folder_uid, 'sha256': digest(d), 'panels_sha256': digest(semantic(d.get('panels', []))), 'variables_sha256': digest(variable_definition(d.get('templating', {}))), 'links_sha256': digest(d.get('links', []))}
    for folder_uid, files in sorted(grouped.items()):
        key = 'general' if not folder_uid else hashlib.sha256(folder_uid.encode()).hexdigest()[:12]
        chunks, chunk, size = [], {}, 0
        for filename, data in sorted(files.items()):
            length = len(data.encode())
            if length > 700_000:
                raise ValueError('One dashboard exceeds the supported ConfigMap size')
            if size + length > 700_000 and chunk:
                chunks.append(chunk); chunk, size = {}, 0
            chunk[filename] = data; size += length
        if chunk:
            chunks.append(chunk)
        if not chunks:
            chunks = [{'README.txt': 'Empty source folder. No alerts or notification channels are imported.\n'}]
        names = []
        for i, data in enumerate(chunks):
            name = f'{prefix}-{key}-{i}'
            resources[name + '.yaml'] = cm(name, data, namespace); names.append(name)
        providers.append({'name': 'import-' + key, 'folder': folders.get(folder_uid, ''), 'folderUid': folder_uid, 'path': key, 'configMaps': names})
    datasources = []
    for original in sorted(source['datasources'], key=lambda x: x['uid']):
        d = {k: original[k] for k in sorted(ALLOWED_DS) if k in original}
        uid = d['uid']
        override = options['datasource_overrides'].get(uid)
        if override is None or not override.get('url'):
            raise ValueError('Every source datasource requires an explicit target endpoint mapping')
        d.update(override)
        required = {k for k, v in original.get('secureJsonFields', {}).items() if v}
        if not required.issubset(d.get('secureJsonData', {})):
            raise ValueError('Datasource secure fields require explicit target Secret environment references')
        if any(not ENV_REF.fullmatch(v) for v in d.get('secureJsonData', {}).values()):
            raise ValueError('Datasource secure values must be target environment references')
        reject_credentials(d)
        # Preserve Grafana runtime macros through provisioning's environment expansion.
        d = json.loads(re.sub(r'(?<!\$)\$(?=\{?__)', '$$', json.dumps(d)))
        datasources.append(d)
    ds_name = prefix + '-datasources'
    deleted_ds = [{'name': d['name'], 'orgId': 1} for d in datasources]
    resources[ds_name + '.yaml'] = cm(ds_name, {'datasources.yaml': yaml.safe_dump({'apiVersion': 1, 'deleteDatasources': deleted_ds, 'datasources': datasources}, sort_keys=False)}, namespace)
    apps = []
    for original in source.get('app_settings', []):
        app_id = original['id']
        override = options.get('app_overrides', {}).get(app_id)
        if override is None:
            raise ValueError('App plugins require reviewed target settings')
        secure = override.get('secureJsonData', {})
        if not {k for k, v in original.get('secureJsonFields', {}).items() if v}.issubset(secure):
            raise ValueError('App secure fields require target Secret references')
        if any(not ENV_REF.fullmatch(v) for v in secure.values()):
            raise ValueError('App secrets must be target environment references')
        app = {'type': app_id, 'org_id': 1, 'disabled': not original.get('enabled', False), 'pinned': original.get('pinned', False), 'jsonData': override.get('jsonData', {}), 'secureJsonData': secure}
        reject_credentials(app); apps.append(app)
    apps_name = prefix + '-apps'
    resources[apps_name + '.yaml'] = cm(apps_name, {'apps.yaml': yaml.safe_dump({'apiVersion': 1, 'apps': apps}, sort_keys=False)}, namespace)
    inventory = {'format': 1, 'source_version': source['health']['version'], 'dashboards': models, 'folders': folders, 'datasources': {d['uid']: {'name': d['name'], 'type': d['type']} for d in datasources}, 'historical_data_migration': 'NOT_RUN', 'production_alert_channels': 'NOT_IMPORTED'}
    checksum = digest({'inventory': inventory, 'resources': resources})
    inventory_name = prefix + '-inventory'
    resources[inventory_name + '.yaml'] = cm(inventory_name, {'inventory.yaml': yaml.safe_dump(inventory, sort_keys=False, allow_unicode=True)}, namespace)
    overlay = {'grafana': {'datasourcesConfigMap': ds_name, 'appsConfigMap': apps_name, 'dashboardProviders': providers, 'bundleChecksum': checksum}}
    values_name = prefix + '-values'
    resources[values_name + '.yaml'] = cm(values_name, {'values.yaml': yaml.safe_dump(overlay, sort_keys=False)}, namespace)
    resources['kustomization.yaml'] = {'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization', 'resources': sorted(resources)}
    return resources, inventory, checksum

def write_bundle(resources, directory, apply):
    root = Path(directory).resolve()
    planned = []
    for name, value in sorted(resources.items()):
        path = root / name
        text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=110)
        if not path.exists() or path.read_text() != text:
            planned.append(name)
            if apply:
                root.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    return planned

def verify(base, auth, inventory, target_url):
    dashboards = []
    for uid, expected in inventory['dashboards'].items():
        found = api_get(base, '/api/dashboards/uid/' + urllib.parse.quote(uid, safe=''), auth)
        d = found['dashboard']
        for key in ['id', 'version']:
            d.pop(key, None)
        if d['title'] != expected['title'] or found['meta'].get('folderUid', '') != expected['folder_uid']:
            raise ValueError('Target dashboard identity or folder differs')
        if digest(semantic(d.get('panels', []))) != expected['panels_sha256'] or digest(variable_definition(d.get('templating', {}))) != expected['variables_sha256'] or digest(d.get('links', [])) != expected['links_sha256']:
            raise ValueError('Target panels, variables, or navigation links differ')
        dashboards.append(uid)
    sources = {x['uid']: x for x in api_get(base, '/api/datasources', auth)}
    for uid, expected in inventory['datasources'].items():
        if uid not in sources or any(sources[uid][key] != expected[key] for key in ['name', 'type']):
            raise ValueError('Target datasource UID contract differs')
    folders = {x['uid']: x['title'] for x in api_get(base, '/api/folders?limit=1000', auth)}
    if any(folders.get(uid) != name for uid, name in inventory['folders'].items()):
        raise ValueError('Target source-folder inventory differs')
    return {'verified_dashboards': len(dashboards), 'verified_datasources': len(inventory['datasources']), 'verified_folders': len(inventory['folders'])}

def main():
    p = json.load(sys.stdin)
    operation = p['operation']
    if operation == 'verify':
        result = verify(p['target_url'], p['target_auth'], p['inventory'], p['target_url'])
    else:
        source = p.get('snapshot') or snapshot(p['source_url'], p['source_auth'], p.get('app_ids', []))
        resources, inventory, checksum = build(source, p['options'])
        planned = write_bundle(resources, p['output_dir'], p.get('apply', False))
        result = {'dashboards': len(inventory['dashboards']), 'datasources': len(inventory['datasources']), 'folders': len(inventory['folders']), 'bundle_sha256': checksum, 'changed_files': planned if p.get('apply') else [], 'would_change_files': planned, 'source_writes': False, 'target_api_writes': False}
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Configuration responses can contain sensitive metadata; never echo payloads or credentials.
        print(json.dumps({'status': 'BLOCKED', 'error_type': type(error).__name__, 'http_status': getattr(error, 'code', None), 'reason': str(error) if isinstance(error, ValueError) else 'Configuration API operation failed'}), file=sys.stderr)
        sys.exit(1)
