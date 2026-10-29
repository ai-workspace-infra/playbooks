"""Compare authenticated baseline data with a verified candidate; never execute SQL.

Callers must independently authenticate both artifacts and their producers before
using this pure consistency check. It does not authorize initialization.
"""
import hashlib
import json
import re


def validate(receipt, baseline, namespace_uid):
    if not isinstance(receipt, dict) or not isinstance(baseline, dict):
        raise ValueError('candidate and baseline documents required')
    projection = receipt.get('projection', {})
    lane = projection.get('tagged_lane', {})
    feature = lane.get('feature_id', '')
    tag = lane.get('feature_tag', '')
    fingerprint = lane.get('candidate_sha256', '')
    if (receipt.get('schema_version') != 'dev-feature-candidate/v2' or
        receipt.get('result') != 'verified' or receipt.get('stage') != 'sit' or
        not isinstance(feature, str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,45}', feature) or
        not isinstance(tag, str) or not tag.endswith('-' + feature) or
        not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', tag[:-(len(feature)+1)]) or
        not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint) or
        receipt.get('candidate_sha256') != fingerprint or
        not isinstance(namespace_uid, str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', namespace_uid)):
        raise ValueError('invalid candidate lane identity')
    inputs = baseline.get('inputs')
    if not isinstance(inputs, dict) or inputs.get('schema') != 'sit-data-input/v1':
        raise ValueError('missing baseline inputs')
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (baseline.get('schema') != 'sit-data-baseline/v1' or baseline.get('result') != 'verified' or
        baseline.get('database_status') != 'NOT_RUN' or baseline.get('feature_id') != feature or
        baseline.get('namespace') != 'feature-' + feature + '-sit' or
        baseline.get('data_baseline_sha256') != digest or receipt.get('data_baseline_sha256') != digest):
        raise ValueError('baseline differs from candidate')
    components = projection.get('components', {})
    if not isinstance(components, dict) or not isinstance(components.get('accounts'), dict) or not isinstance(components.get('billing'), dict):
        raise ValueError('missing source components')
    if (inputs.get('accounts_image') != components['accounts'].get('image') or
        inputs.get('accounts_sha') != components['accounts'].get('source_sha') or
        inputs.get('billing_sha') != components['billing'].get('source_sha')):
        raise ValueError('baseline sources differ from candidate components')
    return {'feature_id': feature, 'feature_tag': tag, 'namespace': baseline['namespace'],
            'namespace_uid': namespace_uid, 'candidate_sha256': fingerprint,
            'data_baseline_sha256': digest}
