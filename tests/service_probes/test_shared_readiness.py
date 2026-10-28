import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/pipeline/check-shared-readiness.sh'

class SharedReadiness(unittest.TestCase):
    def run_probe(self, obs_status='200', obs_body='{"database":"ok"}'):
        with tempfile.TemporaryDirectory() as directory:
            curl = Path(directory) / 'curl'
            curl.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv
out=args[args.index('--output')+1]
url=args[-1]
if '/v1/sys/health' in url: body='{"initialized":true,"sealed":false}'; status='200'
elif '/.well-known/openid-configuration' in url: body='{"issuer":"https://iam.example"}'; status='200'
elif url == 'https://obs.example/api/health': body=os.environ['TEST_BODY']; status=os.environ['TEST_STATUS']
else: body='unexpected path'; status='404'
open(out,'w').write(body)
print(status,end='')
''')
            curl.chmod(0o755)
            env={**os.environ, 'PATH':directory+os.pathsep+os.environ['PATH'],
                 'SHARED_VAULT_ENDPOINT':'https://vault.example', 'SHARED_OBSERVABILITY_ENDPOINT':'https://obs.example',
                 'SHARED_IAM_ENDPOINT':'https://iam.example', 'SHARED_IAM_ISSUER':'https://iam.example',
                 'TEST_STATUS':obs_status, 'TEST_BODY':obs_body}
            return subprocess.run(['bash',str(SCRIPT)],env=env,capture_output=True,text=True)
    def test_current_endpoint_healthy(self):
        result=self.run_probe()
        self.assertEqual(result.returncode,0,result.stderr)
    def test_redirect_and_unhealthy_database_block(self):
        for status,body in [('302',''),('200','{"database":"failed"}'),('200','not json')]:
            with self.subTest(status=status,body=body):
                self.assertNotEqual(self.run_probe(status,body).returncode,0)

if __name__ == '__main__': unittest.main()
