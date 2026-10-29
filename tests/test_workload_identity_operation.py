import copy
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
    def test_real_candidate_evidence_and_mismatched_instance(self):
        candidate=Path('/tmp/gitops-v6-identity-gate/operations/workload-identities/prod-k3s-v6/vault-tls-reader.yaml')
        receipt=Path('/tmp/v6-receipt-verified/v6-bootstrap-receipt.json')
        inventory=Path('/tmp/v6-iac-summary-verified/inventory.json')
        if not all(p.exists() for p in (candidate,receipt,inventory)):self.skipTest('verified candidate files unavailable')
        op=yaml.safe_load(candidate.read_text());raw=receipt.read_bytes();record=json.loads(raw);record['_raw']=raw
        cmdb=next(iter(json.loads(inventory.read_text()).values()))
        with tempfile.NamedTemporaryFile() as f:
            f.write(b'attested-key');f.flush()
            config=m.build_config(op,cmdb,record,f.name,'verify')
            self.assertEqual(config['node_name'],op['spec']['target']['node'])
            altered=copy.deepcopy(cmdb);altered[config['node_name']]['instance_id']='999'
            with self.assertRaises(ValueError):m.build_config(op,altered,record,f.name,'verify')
            altered=copy.deepcopy(op);altered['spec']['identity']['allowedPath']='kv/data/*'
            with self.assertRaises(m.VaultError):m.build_config(altered,cmdb,record,f.name,'verify')

if __name__=='__main__':unittest.main()
