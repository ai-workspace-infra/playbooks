import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('owner',ROOT/'scripts/workload_identity_operation.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Owner(unittest.TestCase):
    def test_mount_diagnostic_allowlist_rejects_untrusted_fields(self):
        safe=m.sanitize_mount_diagnostic('vault_mount',{
            'operation':'auth_mount_create','http_status':403,'token':'must-not-escape','body':'secret'})
        self.assertEqual(safe,{'operation':'auth_mount_create','http_status':403})
        self.assertIsNone(m.sanitize_mount_diagnostic('vault_config',{'operation':'auth_mount_create','http_status':403}))
        self.assertIsNone(m.sanitize_mount_diagnostic('vault_mount',{'operation':'unknown','http_status':403}))

    def test_release_modes_are_rejected(self):
        with self.assertRaises((ValueError, KeyError)):
            m.build_config({'apiVersion': 'operations.svc.plus/v1alpha1', 'kind': 'VaultWorkloadIdentityOperation',
                'spec': {'target': {}, 'vault': {}, 'identity': {}, 'environment': 'prod'}}, {}, {}, '', 'release-root')

    def test_refresh_requires_reviewed_fingerprints(self):
        operation={'apiVersion':'operations.svc.plus/v1alpha1','kind':'VaultWorkloadIdentityOperation',
          'spec':{'target':{},'vault':{},'identity':{},'environment':'prod'}}
        with self.assertRaises((ValueError, KeyError)):
            m.build_config(operation,{}, {}, '', 'refresh', [])

if __name__=='__main__':unittest.main()
