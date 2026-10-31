"""Exercise the real shell router without SSH or Ansible execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RuntimeRoute(unittest.TestCase):
    def test_business_and_runtime_groups_route_to_k3s_regardless_of_order(self):
        for groups in (['web_saas', 'k3s'], ['k3s', 'web_saas']):
            with self.subTest(groups=groups), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                scripts = root / 'playbooks/scripts/pipeline'
                scripts.mkdir(parents=True)
                cmdb = root / 'cmdb'
                cmdb.mkdir()
                (cmdb / 'cmdb.json').write_text(json.dumps({'node-example': {'groups': groups, 'host_vars': {'deployment_mode': 'kubernetes'}}}))
                shutil.copy(ROOT / 'scripts/pipeline/bootstrap-node.sh', scripts / 'bootstrap-node.sh')
                (scripts / 'bootstrap-k3s.sh').write_text('printf "k3s-owner:%s\\n" "$MATRIX_HOST"\n')
                result = subprocess.run(['bash', 'scripts/pipeline/bootstrap-node.sh'], cwd=root / 'playbooks', env={**os.environ, 'MATRIX_HOST': 'node-example'}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'k3s-owner:node-example')

if __name__ == '__main__':
    unittest.main()
