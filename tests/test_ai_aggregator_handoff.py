"""Reject stale and altered IaC evidence before host operations."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aggregator_entry', ROOT / 'roles/vhosts/ai_aggregator_v1/files/run_handoff.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = {'instance_id': '12345', 'ip': '192.0.2.8', 'ansible_user': 'root',
                     'cloud_provider': 'akamai-cloud', 'groups': ['ai_aggregator_gateway', 'ai_aggregator_cpa']}
        self.hostname = 'gateway.example.net'
        self.inventory = {'target': {self.hostname: self.host}}
        context = {'environment': 'uat', 'run_id': '20', 'run_attempt': '2',
                   'gitops_sha': 'a' * 40, 'iac_sha': 'b' * 40, 'action': 'apply'}
        context['contract_sha256'] = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        encoded = (json.dumps(self.inventory['target'], sort_keys=True, indent=2) + '\n').encode()
        self.summary = {'context': context, 'result': 'applied_verified',
                        'receipts': [{**context, 'target_id': 'target', 'stage': 'resources',
                                      'inventory_sha256': hashlib.sha256(encoded).hexdigest()}],
                        'deploy_matrix': [dict(target_id='target', host=self.hostname, **self.host)]}
        self.config = {'operation': 'plan', 'environment': 'uat', 'hostname': self.hostname,
                       'domain': 'gateway.example.net', 'gitops_root': str(self.root), 'iac_root': str(self.root),
                       'gitops_sha': 'a' * 40, 'iac_sha': 'b' * 40, 'service_manifest': 'service.yaml',
                       'summary_file': str(self.root / 'summary.json'), 'cmdb_file': str(self.root / 'cmdb.json'),
                       'target_manifest': 'iac/targets/target.yaml', 'correlation_id': '20:2:aggregator'}
        self.manifest = json.dumps({'kind': 'PersonalAIAggregator', 'metadata': {'environment': 'uat'},
                                    'spec': {'enabled': True, 'entrypoint': {'domain': self.config['domain']},
                                             'nodes': [{'inventory_host': self.hostname, 'ssh_user': 'root', 'role': 'gateway', 'roles': ['gateway', 'cpa']}]}}).encode()
        (self.root / 'service.yaml').write_bytes(self.manifest)
        self.save()

    def save(self):
        raw = json.dumps(self.summary).encode()
        (self.root / 'summary.json').write_bytes(raw)
        self.config['summary_sha256'] = hashlib.sha256(raw).hexdigest()
        (self.root / 'inventory.json').write_text(json.dumps(self.inventory))
        (self.root / 'cmdb.json').write_text(json.dumps({self.hostname: self.host}))

    def preflight(self):
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '20', 'GITHUB_RUN_ATTEMPT': '2'}), \
             patch.object(entry, 'revision'), patch.object(entry.subprocess, 'check_output', return_value=self.manifest), \
             patch.object(entry.subprocess, 'run') as owner:
            result = entry.preflight(self.config)
            self.assertEqual(owner.call_args.kwargs['env']['IAC_CORRELATION'], self.config['correlation_id'])
            return result

    def test_valid_current_run_uses_owner_verifier(self):
        self.assertEqual(self.preflight()[1], self.host)

    def test_stale_attempt_and_cross_environment_rejected(self):
        for key, value in [('environment', 'prod'), ('run_attempt', '1')]:
            with self.subTest(key=key):
                original = copy.deepcopy(self.summary)
                self.summary['context'][key] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.preflight()
                self.summary = original

    def test_changed_runtime_address_rejected(self):
        self.inventory['target'][self.hostname]['ip'] = '192.0.2.9'
        (self.root / 'inventory.json').write_text(json.dumps(self.inventory))
        with self.assertRaises(ValueError):
            self.preflight()

    def test_forged_cmdb_and_extra_host_rejected(self):
        (self.root / 'cmdb.json').write_text(json.dumps({'other': self.host}))
        with self.assertRaises(ValueError):
            self.preflight()

    def test_duplicate_receipt_rejected(self):
        self.summary['receipts'] *= 2
        self.save()
        with self.assertRaises(ValueError):
            self.preflight()

    def test_unpinned_service_domain_rejected(self):
        self.config['domain'] = 'different.example.net'
        with self.assertRaises(ValueError):
            self.preflight()

    def test_wrong_matrix_rejected(self):
        self.summary['deploy_matrix'] = []
        self.save()
        with self.assertRaises(ValueError):
            self.preflight()

    def test_failed_iac_owner_verification_stops_deployment(self):
        with patch.object(entry, 'revision'), patch.object(entry.subprocess, 'check_output', return_value=self.manifest), \
             patch.dict(os.environ, {'GITHUB_RUN_ID': '20', 'GITHUB_RUN_ATTEMPT': '2'}), \
             patch.object(entry.subprocess, 'run', side_effect=entry.subprocess.CalledProcessError(1, ['verify-summary'])):
            with self.assertRaises(entry.subprocess.CalledProcessError):
                entry.preflight(self.config)


if __name__ == '__main__':
    unittest.main()
