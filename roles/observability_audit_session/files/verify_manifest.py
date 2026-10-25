#!/usr/bin/env python3
"""Opt-in bounded dashboard queries; consumes only an already-verified Viewer GET callback."""
import hashlib
import json
import math
from pathlib import Path
import re
import time
import urllib.parse

import yaml

UIDS = {'StarsL_cn_K8S', 'StarsL-JOB-node', 'process-exporter-with-tree'}
JOBS = {'node_': 'node-exporter', 'namedprocess_': 'process-exporter', 'kube_': 'kube-state-metrics',
        'kubelet_': 'kubelet', 'container_': 'cadvisor'}


def load_manifest(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError('manifest_path_or_size_invalid')
    raw = path.read_bytes()
    try:
        body = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise ValueError('manifest_yaml_invalid') from None
    if not isinstance(body, dict) or body.get('schema_version') != 1:
        raise ValueError('manifest_schema_invalid')
    dashboards = body.get('dashboards')
    if not isinstance(dashboards, list) or len(dashboards) != 3 or {d.get('uid') for d in dashboards} != UIDS:
        raise ValueError('manifest_dashboard_set_invalid')
    count = 0
    for dashboard in dashboards:
        variables, targets = dashboard.get('variables_in_order'), dashboard.get('targets')
        if not isinstance(variables, list) or len(variables) > 20 or not isinstance(targets, list) or len(targets) > 200:
            raise ValueError('manifest_query_bounds_invalid')
        for item in variables + targets:
            if not isinstance(item, dict):
                raise ValueError('manifest_item_invalid')
            value = item.get('query', item.get('expr'))
            if not isinstance(value, str) or not 0 < len(value) <= 16000:
                raise ValueError('manifest_query_invalid')
        for target in targets:
            if type(target.get('hidden')) is not bool or type(target.get('panel_id')) is not int or not isinstance(target.get('ref_id'), str):
                raise ValueError('manifest_target_identity_invalid')
        if not isinstance(dashboard.get('families'), list) or len(dashboard['families']) > 120:
            raise ValueError('manifest_family_bounds_invalid')
        for metric in dashboard['families']:
            if not isinstance(metric, str) or not re.fullmatch(r'(?:node_|namedprocess_|kube_|kubelet_|container_)[A-Za-z0-9_:]+', metric):
                raise ValueError('manifest_family_invalid')
        count += len(variables) + len(targets) + len(dashboard['families'])
    if count > 500:
        raise ValueError('manifest_total_query_bound_invalid')
    body['_sha256'] = hashlib.sha256(raw).hexdigest()
    return body


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


def contract(model):
    variables = []
    for var in model.get('templating', {}).get('list', []):
        if var.get('type') == 'query':
            query = var.get('query', '')
            variables.append((var['name'], query.get('query') if isinstance(query, dict) else query))
    targets = [(p['id'], t.get('refId', ''), bool(t.get('hide', False)), t['expr'])
               for p in panels(model.get('panels', [])) for t in p.get('targets', []) if t.get('expr')]
    return variables, targets


def expand(expression, values):
    def replace(match):
        key = match.group(1) or match.group(3)
        fmt = match.group(2)
        if key not in values or fmt not in (None, 'regex'):
            raise ValueError('unresolved_or_unsupported_macro')
        return values[key]
    result = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([a-zA-Z]+))?\}|\$([A-Za-z_][A-Za-z0-9_]*)', replace, expression)
    if '$' in result:
        # A regex end-anchor is legitimate; unexpanded variables are not.
        if re.search(r'\$[A-Za-z_{]', result):
            raise ValueError('unresolved_macro')
    return result


def regex_values(options):
    return json.dumps('|'.join(re.escape(value) for value in options))[1:-1]


def execute(manifest, args, get, clock=time.monotonic):
    deadline = clock() + 300
    used = 0
    summaries = []
    conditional = set(manifest.get('conditional_data', {}))
    scope = 'cluster=' + json.dumps(args.cluster) + ',environment=' + json.dumps(args.environment)
    proxy = '/api/datasources/proxy/uid/' + args.datasource_uid + '/api/v1/query?'

    def query(expr):
        nonlocal used
        if clock() >= deadline or used >= 500:
            return {'status': 'NOT_RUN', 'reason': 'bounded_query_budget_exhausted'}, []
        used += 1
        code, payload = get(proxy + urllib.parse.urlencode({'query': expr, 'timeout': '5s'}))
        if code != 200 or not isinstance(payload, dict) or payload.get('status') != 'success':
            return {'status': 'FAIL', 'http_status': code, 'reason': 'query_or_transport_error'}, []
        data = payload.get('data', {})
        rows = data.get('result') if isinstance(data, dict) else None
        if data.get('resultType') != 'vector' or not isinstance(rows, list) or len(rows) > 5000:
            return {'status': 'FAIL', 'http_status': code, 'reason': 'bounded_vector_schema_required'}, []
        try:
            if not all(isinstance(row, dict) and isinstance(row.get('metric'), dict) and math.isfinite(float(row['value'][1])) for row in rows):
                raise ValueError('non_finite_result')
        except (TypeError, ValueError, KeyError, IndexError):
            return {'status': 'FAIL', 'http_status': code, 'reason': 'invalid_or_nonfinite_sample'}, []
        return {'status': 'PASS' if rows else 'NOT_RUN', 'http_status': code, 'series_count': len(rows),
                'expr_sha256': hashlib.sha256(expr.encode()).hexdigest()}, rows

    def status(items):
        states = [item['status'] for item in items]
        return 'FAIL' if 'FAIL' in states else 'NOT_RUN' if 'NOT_RUN' in states else 'PASS'

    for plan in manifest['dashboards']:
        uid = plan['uid']
        code, payload = get('/api/dashboards/uid/' + uid)
        model = payload.get('dashboard', {}) if isinstance(payload, dict) else {}
        expected = ([(v['name'], v['query']) for v in plan['variables_in_order']],
                    [(t['panel_id'], t['ref_id'], t['hidden'], t['expr']) for t in plan['targets']])
        if code != 200 or contract(model) != expected:
            summaries.append({'uid': uid, 'status': 'FAIL', 'reason': 'live_dashboard_query_contract_mismatch'})
            continue
        values = {'cluster': regex_values([args.cluster]), 'environment': regex_values([args.environment]),
                  '__rate_interval': '3m', '__interval': '1m', '__range': '30m', '__range_s': '1800'}
        variable_results, target_results, family_results = [], [], []
        scope_gaps = 0
        for var in model.get('templating', {}).get('list', []):
            name = var['name']
            if var.get('type') == 'datasource':
                values[name] = args.datasource_uid
                continue
            if var.get('type') == 'custom' and name == 'interval' and var.get('query') == '3m':
                values[name] = '3m'
                continue
            if var.get('type') != 'query':
                continue
            raw = var.get('query', '')
            raw = raw.get('query') if isinstance(raw, dict) else raw
            try:
                expanded = expand(raw, values)
                label = re.fullmatch(r'label_values\((.+),\s*([A-Za-z_][A-Za-z0-9_]*)\)', expanded)
                result_query = re.fullmatch(r'query_result\((.+)\)', expanded)
                if label:
                    result, rows = query('count by(' + label[2] + ')(' + label[1] + ')')
                    options = sorted({row['metric'].get(label[2], '') for row in rows} - {''})
                elif result_query and name in ('total', 'maxmount'):
                    result, rows = query(result_query[1])
                    options = sorted({str(row['value'][1]) if name == 'total' else row['metric'].get('mountpoint', '') for row in rows} - {''})
                else:
                    raise ValueError('unsupported_variable_query')
                if len(options) > 200 or any(len(value) > 256 for value in options):
                    raise ValueError('variable_option_bound_exceeded')
                if name in ('cluster', 'environment'):
                    chosen = args.cluster if name == 'cluster' else args.environment
                    if chosen not in options:
                        result = {'status': 'FAIL', 'reason': 'explicit_scope_not_in_variable_options'}
                elif options:
                    if name == 'job' and uid == 'StarsL-JOB-node':
                        values[name] = 'node-exporter'
                        if 'node-exporter' not in options:
                            result = {'status': 'FAIL', 'reason': 'canonical_job_missing_from_variable_options'}
                    elif name in ('instance', 'name', 'Node'):
                        selected = sorted(set(options) & set(args.expected_node))
                        if set(selected) != set(args.expected_node):
                            result = {'status': 'FAIL', 'reason': 'expected_nodes_missing_from_variable_options'}
                        if selected:
                            values[name] = regex_values(selected)
                        result['unselected_option_count'] = len(set(options) - set(selected))
                    else:
                        values[name] = regex_values(options)
                if not options and result['status'] != 'FAIL' and result.get('reason') != 'bounded_query_budget_exhausted':
                    result['status'] = 'FAIL'
                    result['reason'] = 'variable_options_empty'
            except (ValueError, TypeError, KeyError):
                result = {'status': 'NOT_RUN', 'reason': 'variable_resolution_unsupported_or_bounded'}
            variable_results.append({'name': name, **result})
        for metric in plan['families']:
            job = next(value for prefix, value in JOBS.items() if metric.startswith(prefix))
            expr = 'time()-min by(node)(timestamp(' + metric + '{' + scope + ',job=' + json.dumps(job) + '}))'
            result, rows = query(expr)
            if rows:
                age = max(float(row['value'][1]) for row in rows)
                nodes = {row['metric'].get('node') for row in rows}
                nodes_ok = not metric.startswith(('node_', 'namedprocess_')) or nodes == set(args.expected_node)
                result['status'] = 'PASS' if 0 <= age <= 180 and nodes_ok else 'FAIL'
                if not nodes_ok:
                    result['reason'] = 'external_node_family_coverage_mismatch'
                result['worst_age_seconds'] = age
            elif result['status'] != 'FAIL' and result.get('reason') != 'bounded_query_budget_exhausted':
                excluded = metric in manifest.get('not_collected', [])
                result['status'] = 'NOT_RUN' if metric in conditional or excluded else 'FAIL'
                result['reason'] = ('deliberately_excluded_by_nonroot_profile' if excluded else
                                    'conditional_applicability_unverified' if metric in conditional else 'required_family_absent')
            family_results.append({'metric': metric, 'canonical_job': job, **result})
        for target in plan['targets']:
            metadata = {'panel_id': target['panel_id'], 'ref_id': target['ref_id']}
            if target['hidden']:
                target_results.append({**metadata, 'status': 'SKIPPED', 'reason': 'hidden_target'})
                continue
            try:
                expression = expand(target['expr'], values)
                result, rows = query(expression)
                scope_verified = bool(re.search(r'cluster\s*=~?', target['expr']) and re.search(r'environment\s*=~?', target['expr']))
                result['cluster_environment_scope_in_live_query'] = scope_verified
                if not scope_verified:
                    scope_gaps += 1
                    if result['status'] != 'FAIL':
                        result['status'] = 'NOT_RUN'
                        result['reason'] = 'live_query_lacks_cluster_environment_isolation'
                if not rows and result['status'] != 'FAIL':
                    result['reason'] = 'empty_result_needs_applicability_or_query_review'
            except ValueError:
                result = {'status': 'NOT_RUN', 'reason': 'unresolved_or_unsupported_macro'}
            target_results.append({**metadata, **result})
        summaries.append({'uid': uid, 'status': status(variable_results + family_results + target_results),
                          'unscoped_live_target_count': scope_gaps, 'variables': variable_results, 'families': family_results, 'targets': target_results})
    return {'status': status(summaries), 'manifest_sha256': manifest['_sha256'], 'dashboards': summaries,
            'query_count': used, 'scope': 'Existing verified Viewer token only; instant queries; canonical-family freshness.',
            'limitations': 'Variable choices evaluate the discovered option union, not every UI combination. Existing representative continuity checks remain separate; conditional empty queries are NOT_RUN. No claim of full rule visibility.'}
