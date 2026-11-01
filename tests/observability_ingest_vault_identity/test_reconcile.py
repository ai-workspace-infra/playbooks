import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[2] / 'roles/vhosts/observability_ingest_vault_identity/files/reconcile.py'
spec = importlib.util.spec_from_file_location('ingest_vault_reconcile', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    name = 'observability-ingest-reader'
    policy = {'apiVersion': 'security.gitops/v1alpha1', 'kind': 'VaultACLPolicy', 'metadata': {'name': name}, 'spec': {'paths': [
        {'path': 'kv/data/CICD/observability', 'capabilities': ['read']},
        {'path': 'auth/token/renew-self', 'capabilities': ['update']},
        {'path': 'auth/token/lookup-self', 'capabilities': ['read']} ]}}
    role = {'apiVersion': 'security.gitops/v1alpha1', 'kind': 'VaultKubernetesAuthRole', 'metadata': {'name': name}, 'spec': {
        'bound_service_account_names': ['observability-ingest-vault-reader'],
        'bound_service_account_namespaces': ['web-saas-k3s-v5'],
        'audience': 'vault-web-saas-k3s-v5-prod', 'token_policies': [name],
        'token_no_default_policy': True, 'token_type': 'service', 'token_ttl': '5m', 'token_max_ttl': '15m'}}
    return {'address': 'https://vault.invalid', 'token': 'example', 'mount': 'test-mount', 'policy': policy, 'role': role, 'apply': True}


class ReconcileTests(unittest.TestCase):
    def test_missing_resources_created_and_read_back(self):
        req = fixture(); stored = {}; calls = []
        def api(_address, _token, method, path, payload=None):
            calls.append((method, path))
            if path == 'sys/auth': return {'data': {'test-mount/': {'type': 'kubernetes'}}}
            if method == 'GET': return stored.get(path)
            if path.startswith('sys/policies/'): stored[path] = {'data': payload}
            else:
                value = dict(payload); value.update(token_ttl=300, token_max_ttl=900); stored[path] = {'data': value}
            return {}
        with mock.patch.object(module, 'request', side_effect=api), mock.patch('sys.stdin', io.StringIO(json.dumps(req))), contextlib.redirect_stdout(io.StringIO()) as out:
            module.main()
        self.assertEqual(json.loads(out.getvalue()), {'status': 'matched', 'changed': 2})
        self.assertEqual([m for m, _ in calls if m != 'GET'], ['PUT', 'POST'])

    def test_existing_policy_drift_stops_before_any_write(self):
        req = fixture(); calls = []
        def api(_address, _token, method, path, payload=None):
            calls.append(method)
            if path == 'sys/auth': return {'data': {'test-mount/': {'type': 'kubernetes'}}}
            if path.startswith('sys/policies/'): return {'data': {'policy': 'path "kv/data/*" { capabilities = ["read"] }'}}
            return None
        with mock.patch.object(module, 'request', side_effect=api), mock.patch('sys.stdin', io.StringIO(json.dumps(req))):
            with self.assertRaises(ValueError): module.main()
        self.assertNotIn('PUT', calls); self.assertNotIn('POST', calls)

    def test_extra_policy_path_is_rejected_before_network(self):
        req = fixture(); req['policy']['spec']['paths'].append({'path': 'kv/data/other', 'capabilities': ['read']})
        with mock.patch.object(module, 'request') as api, mock.patch('sys.stdin', io.StringIO(json.dumps(req))):
            with self.assertRaises(ValueError): module.main()
        api.assert_not_called()


if __name__ == '__main__': unittest.main()
