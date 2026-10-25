#!/usr/bin/env python3
"""Offline, plan-first host Dashboard scope adaptation and Viewer query manifest."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

import yaml

HOSTS = {'StarsL-JOB-node': ('Node-Exporter-Dashboard.json', 'node-exporter', 'node_uname_info'),
         'process-exporter-with-tree': ('process-exporter-dashboard-with-treemap.json', 'process-exporter', 'namedprocess_namegroup_num_procs')}
SCOPE = 'cluster=~"${cluster:regex}",environment=~"${environment:regex}"'
DROPPED = {11: 'file descriptors', 12: 'process read bytes', 13: 'process write bytes'}
PSS = ('PSS requires readable /proc/<pid>/smaps. The non-root collector may have partial or no '
       'access; absence is No data, not zero. Resident memory and CPU do not prove PSS coverage.')


def tokens(value):
    """Yield outside-string identifiers with offsets (Grafana variables stay quoted)."""
    i = 0
    while i < len(value):
        if value[i] in '\"\'`':
            quote = value[i]
            i += 1
            while i < len(value):
                if value[i] == '\\' and quote != '`':
                    i += 2
                elif value[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        elif value[i].isalpha() or value[i] in '_:':
            match = re.match(r'[a-zA-Z_:][a-zA-Z0-9_:]*', value[i:])
            end = i + len(match[0])
            yield match[0], i, end
            i = end
        else:
            i += 1


def selector_end(value, start):
    quote, escaped = None, False
    for i in range(start + 1, len(value)):
        char = value[i]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in '\"\'`':
            quote = char
        elif char == '}':
            return i
    raise ValueError('Unclosed metric selector')


def split_matchers(body):
    items, start, quote, escaped = [], 0, None, False
    for i, char in enumerate(body):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote != '`':
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == ',':
            items.append(body[start:i].strip())
            start = i + 1
    items.append(body[start:].strip())
    if quote:
        raise ValueError('Unclosed matcher string')
    return [item for item in items if item]


def scope_query(value, job):
    edits = []
    # Only metric identifiers outside quotes are touched, never label values.
    for name, start, end in tokens(value):
        if not name.startswith(('node_', 'namedprocess_')):
            continue
        j = end
        while j < len(value) and value[j].isspace():
            j += 1
        remaining = ''
        stop = end
        if j < len(value) and value[j] == '{':
            stop = selector_end(value, j) + 1
            body = value[j + 1:stop - 1]
            # Match complete label matchers, allowing escaped delimiters in strings.
            labels = split_matchers(body)
            remaining = ','.join(x.strip() for x in labels if not re.match(r'\s*(cluster|environment|job)\s*(?:=~|!~|!=|=)', x))
        replacement = name + '{job="' + job + '",' + SCOPE + (',' + remaining if remaining else '') + '}'
        edits.append((start, stop, replacement))
    for start, stop, replacement in reversed(edits):
        value = value[:start] + replacement + value[stop:]
    return value


def walk_panels(items):
    for panel in items:
        yield panel
        yield from walk_panels(panel.get('panels', []))


def rewrite_queries(item, job):
    if isinstance(item, list):
        return [rewrite_queries(x, job) for x in item]
    if isinstance(item, dict):
        return {k: scope_query(v, job) if k in ('expr', 'query', 'definition') and isinstance(v, str)
                else rewrite_queries(v, job) for k, v in item.items()}
    return item


def node_p99_queries():
    selector = '{job="node-exporter",' + SCOPE + '}'
    idle = '{job="node-exporter",' + SCOPE + ',mode="idle"}'
    cpu = '(100 * (1 - avg by(instance,cluster,environment)(rate(node_cpu_seconds_total' + idle + '[3m]))))'
    memory = ('(100 * (1 - avg by(instance,cluster,environment)(node_memory_MemAvailable_bytes'
              + selector + ' / node_memory_MemTotal_bytes' + selector + ')))')
    return {'B': 'quantile_over_time(0.99,' + cpu + '[7d:5m])',
            'C': 'quantile_over_time(0.99,' + memory + '[7d:5m])'}


def adapt(model):
    uid = model['uid']
    _, job, base = HOSTS[uid]
    result = rewrite_queries(copy.deepcopy(model), job)
    variables = [v for v in result['templating']['list'] if v['name'] not in ('cluster', 'environment')]
    datasource = 'victoriametrics' if uid == 'StarsL-JOB-node' else '${DS_METRICS}'
    def variable(name, query):
        return {'name': name, 'label': name, 'type': 'query', 'datasource': {'type': 'prometheus', 'uid': datasource},
                'query': {'query': query, 'refId': name}, 'definition': query, 'refresh': 1,
                'current': {}, 'options': [], 'includeAll': False, 'multi': False}
    prefix = [v for v in variables if v['type'] == 'datasource']
    rest = [v for v in variables if v['type'] != 'datasource']
    for var in rest:
        if var['type'] == 'query':
            var.update(current={}, options=[], refresh=1)
    for var in prefix:
        if var['name'] == 'DS_METRICS':
            var['current'] = {'text': 'VictoriaMetrics', 'value': 'victoriametrics'}
    result['templating']['list'] = [*prefix,
        variable('cluster', 'label_values(' + base + '{job="' + job + '"},cluster)'),
        variable('environment', 'label_values(' + base + '{job="' + job + '",cluster=~"${cluster:regex}"},environment)'), *rest]
    for panel in walk_panels(result.get('panels', [])):
        if uid == 'StarsL-JOB-node' and panel.get('id') == 200:
            queries = node_p99_queries()
            for target in panel.get('targets', []):
                if target.get('refId') in queries:
                    target['expr'] = queries[target['refId']]
            panel['description'] = (
                'P99 CPU/memory percentages are derived from raw node-exporter metrics, without recording rules. '
                'CPU uses the idle-rate complement; memory uses 1 - MemAvailable/MemTotal. '
                'The [7d:5m] subquery samples the available history within a seven-day window. '
                'A newly collected node may have only minutes of history: this does not prove seven days of coverage. '
                'Missing source metrics remain No data. Rows join by instance within the selected cluster/environment.')
        defaults = panel.get('fieldConfig', {}).get('defaults', {})
        if defaults.get('noValue') in (0, '0'):
            defaults['noValue'] = 'No data'
        if uid == 'process-exporter-with-tree':
            if panel['id'] in DROPPED:
                label = DROPPED[panel['id']]
                panel.update(type='text', targets=[], options={'mode': 'markdown', 'content':
                    '### Not collected: ' + label + '\n\nThe non-root agent profile deliberately drops process IO/FD metrics. '
                    'This panel does not query them and does not display a synthetic zero. Enabling them requires a separately reviewed collector permission/profile change.'})
                panel.pop('transformations', None)
            if panel['id'] in (2, 4):
                panel['description'] = PSS
                if not panel['title'].endswith(' (permission-limited)'):
                    panel['title'] += ' (permission-limited)'
    marker = ' Select cluster/environment; queries use the canonical exporter job. Missing telemetry remains No data.'
    if not result.get('description', '').endswith(marker):
        result['description'] = result.get('description', '') + marker
    return result


def model_text(model):
    return json.dumps(model, ensure_ascii=False, indent=2) + '\n'


def families(expr):
    return sorted({name for name, _, _ in tokens(expr) if name.startswith(('node_', 'namedprocess_', 'kube_', 'kubelet_', 'container_'))})


def manifest(models):
    dashboards = []
    for model in models:
        variables = []
        for variable in model['templating']['list']:
            if variable.get('type') != 'query':
                continue
            query = variable.get('query', '')
            if isinstance(query, dict):
                query = query['query']
            variables.append({'name': variable['name'], 'query': query, 'families': families(query)})
        targets = []
        for panel in walk_panels(model.get('panels', [])):
            for target in panel.get('targets', []):
                if target.get('expr'):
                    targets.append({'panel_id': panel['id'], 'ref_id': target.get('refId', ''),
                                    'hidden': bool(target.get('hide', False)), 'expr': target['expr'],
                                    'families': families(target['expr'])})
        dashboards.append({'uid': model['uid'], 'model_sha256': hashlib.sha256(model_text(model).encode()).hexdigest(),
                           'variables_in_order': variables, 'targets': targets,
                           'families': sorted({f for t in [*variables, *targets] for f in t['families']})})
    return {'schema_version': 1, 'purpose': 'Existing restricted Viewer lifecycle only; offline query manifest, not execution or acceptance evidence.',
        'procedure': ['Bind the exact datasource UID and explicit cluster/environment and externally verified expected nodes.',
            'Resolve variables in listed order via bounded Grafana datasource resource GETs; expand Grafana macros with the selected actual values.',
            'Execute every active target as bounded instant/range GET through the existing Viewer owner; hidden targets are separately recorded.',
            'Check semantic errors, fresh samples, all externally expected nodes, interval coverage, joins and duplicates; family presence alone is insufficient.',
            'Record target statuses and only sanitized summaries; no tokens, raw process profiles or full labels in evidence. No management fallback.'],
        'canonical_jobs': {'node_*': 'node-exporter', 'namedprocess_*': 'process-exporter',
                           'kube_*': 'kube-state-metrics', 'kubelet_*': 'kubelet', 'container_*': 'cadvisor'},
        'conditional_data': {'kube_node_spec_taint': 'No taints can be valid; corroborate actual node applicability.',
            'kube_pod_container_resource_limits': 'Unset limits can be valid; corroborate workload configuration.',
            'kube_pod_container_resource_requests': 'Unset requests can be valid; corroborate workload configuration.',
            'kube_pod_spec_volumes_persistentvolumeclaims_info': 'Require an actual bound PVC before volume acceptance.',
            'kubelet_volume_stats_available_bytes': 'Requires actual mounted PVC and volume plugin stats; otherwise NOT_RUN.',
            'kubelet_volume_stats_used_bytes': 'Requires actual mounted PVC and volume plugin stats; otherwise NOT_RUN.',
            'container_fs_limit_bytes': 'Check actual id=/ and /dev device applicability on containerd; empty results are not healthy zero.',
            'container_fs_usage_bytes': 'Check actual id=/ and /dev device applicability on containerd; empty results are not healthy zero.',
            'namedprocess_namegroup_memory_bytes': PSS,
            'node_memory_SwapTotal_bytes': 'A real zero is valid when swap disabled; absence is not zero.'},
        'not_collected': ['namedprocess_namegroup_open_filedesc', 'namedprocess_namegroup_worst_fd_ratio',
                          'namedprocess_namegroup_read_bytes_total', 'namedprocess_namegroup_write_bytes_total'],
        'additional_process_health_queries': ['namedprocess_scrape_errors{job="process-exporter",' + SCOPE + '}',
                                               'namedprocess_scrape_partial_errors{job="process-exporter",' + SCOPE + '}'],
        'dashboards': dashboards}


def render(playbooks_root, gitops_root=None, manifest_path=None):
    root = Path(playbooks_root).resolve()
    source = root / 'roles/docker/observability-server/files'
    files, models = {}, []
    for uid, (filename, _, _) in HOSTS.items():
        path = source / filename
        model = json.loads(path.read_text())
        if model['uid'] != uid:
            raise ValueError('Unexpected host Dashboard UID')
        adapted = adapt(model)
        files[path] = model_text(adapted)
        models.append(adapted)
    models.append(json.loads((source / 'starsl-k8s-dashboard.json').read_text()))
    if gitops_root:
        target = Path(gitops_root).resolve() / 'services/platform-observability/prod/imported-grafana'
        path = target / 'source-grafana-general-0.yaml'
        cm = yaml.safe_load(path.read_text())
        seen = set()
        for key, value in cm['data'].items():
            model = json.loads(value)
            if model.get('uid') in HOSTS:
                if model['uid'] in seen:
                    raise ValueError('Duplicate host Dashboard UID')
                seen.add(model['uid'])
                cm['data'][key] = model_text(adapt(model))
        if seen != set(HOSTS):
            raise ValueError('Missing expected host Dashboard UID')
        files[path] = yaml.safe_dump(cm, sort_keys=False, allow_unicode=True)
        vpath = target / 'source-grafana-values.yaml'
        values_cm = yaml.safe_load(vpath.read_text())
        values = yaml.safe_load(values_cm['data']['values.yaml'])
        bundle = {}
        for candidate in sorted(target.glob('*.yaml')):
            if candidate == vpath:
                continue
            document = cm if candidate == path else yaml.safe_load(candidate.read_text())
            if isinstance(document, dict) and document.get('kind') == 'ConfigMap':
                bundle[document['metadata']['name']] = document.get('data', {})
        values['grafana']['bundleChecksum'] = hashlib.sha256(json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        values_cm['data']['values.yaml'] = yaml.safe_dump(values, sort_keys=False, allow_unicode=True)
        files[vpath] = yaml.safe_dump(values_cm, sort_keys=False, allow_unicode=True)
        # Use actual consumer models for the acceptance manifest, including retained metadata/customizations.
        models = [json.loads(value) for value in cm['data'].values() if json.loads(value).get('uid') in HOSTS]
        stars = yaml.safe_load((target / 'starsl-k8s-dashboard.yaml').read_text())
        models += [json.loads(value) for value in stars['data'].values()]
    if manifest_path:
        files[Path(manifest_path).resolve()] = yaml.safe_dump(manifest(models), sort_keys=False, allow_unicode=True)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--playbooks-root', required=True)
    parser.add_argument('--gitops-root')
    parser.add_argument('--manifest')
    parser.add_argument('--apply', action='store_true', help='Write local files only; otherwise print a plan')
    args = parser.parse_args()
    files = render(args.playbooks_root, args.gitops_root, args.manifest)
    changed = [str(path) for path, body in files.items() if not path.exists() or path.read_text() != body]
    if args.apply:
        for path, body in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    print(json.dumps({'applied': args.apply, 'changed_files': changed}))


if __name__ == '__main__':
    main()
