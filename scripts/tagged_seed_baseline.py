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
    keys = {'schema', 'accounts_sha', 'billing_sha', 'accounts_image', 'accounts_manifest_sha256',
            'accounts_native_sha256', 'billing_schema_sha256', 'image_manifest_sha256',
            'migration_version', 'business_table_count'}
    if (not isinstance(inputs, dict) or set(inputs) != keys or inputs.get('schema') != 'sit-data-input/v1' or
        any(not isinstance(inputs.get(k), str) or not re.fullmatch(r'[0-9a-f]{40}', inputs[k]) for k in ('accounts_sha', 'billing_sha')) or
        any(not isinstance(inputs.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', inputs[k]) for k in
            ('accounts_manifest_sha256', 'accounts_native_sha256', 'billing_schema_sha256', 'image_manifest_sha256')) or
        not isinstance(inputs.get('accounts_image'), str) or
        not re.fullmatch(r'ghcr\.io/ai-workspace-services/accounts@sha256:[0-9a-f]{64}', inputs['accounts_image']) or
        type(inputs.get('migration_version')) is not int or inputs['migration_version'] <= 0 or
        type(inputs.get('business_table_count')) is not int or inputs['business_table_count'] <= 0):
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
    if (components['accounts'].get('source_repository') != 'ai-workspace-services/accounts' or
        components['billing'].get('source_repository') != 'ai-workspace-services/billing-service' or
        inputs.get('accounts_image') != components['accounts'].get('image') or
        inputs.get('accounts_sha') != components['accounts'].get('source_sha') or
        inputs.get('billing_sha') != components['billing'].get('source_sha')):
        raise ValueError('baseline sources differ from candidate components')
    return {'feature_id': feature, 'feature_tag': tag, 'namespace': baseline['namespace'],
            'namespace_uid': namespace_uid, 'candidate_sha256': fingerprint,
            'data_baseline_sha256': digest}


def validate_receipt_inputs(receipt, namespace_uid):
    """Check inputs projected by the authenticated same-run candidate receipt.

    The adapter must first authenticate receipt provenance with
    ``validate_candidate_receipt``. This helper cannot authenticate a receipt.
    """
    if not isinstance(receipt, dict) or not isinstance(receipt.get('projection'), dict):
        raise ValueError('candidate receipt required')
    lane = receipt['projection'].get('tagged_lane')
    if not isinstance(lane, dict) or not isinstance(lane.get('feature_id'), str):
        raise ValueError('candidate lane required')
    baseline = {'schema': 'sit-data-baseline/v1', 'result': 'verified', 'database_status': 'NOT_RUN',
                'feature_id': lane['feature_id'], 'namespace': 'feature-' + lane['feature_id'] + '-sit',
                'data_baseline_sha256': receipt.get('data_baseline_sha256'),
                'inputs': receipt.get('data_baseline_inputs')}
    return validate(receipt, baseline, namespace_uid)
