"""Literal secrets survive the ordinary dotenv parser; no live credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import jinja2

ROOT=Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('docker'), 'requires the local Compose config parser')
class PrimaryRuntimeEnvfileTests(unittest.TestCase):
    def test_quotes_backslashes_dollars_and_spaces_are_literal(self):
        image=subprocess.run(['docker','image','inspect','postgres:17','--format','{{.Id}}'],capture_output=True,text=True)
        if image.returncode:
            self.skipTest('requires a pre-pulled disposable PostgreSQL 17 image; never pulls or deploys')
        env=jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters['bool']=bool
        env.filters['to_json']=lambda value,**kwargs:json.dumps(value,**kwargs)
        template=env.from_string((ROOT/'roles/web_saas_generation_fresh/templates/primary-runtime.env.j2').read_text())
        password='fixture $UNDEFINED ${POISON} \\ "quote" \'single\' # trailing $'
        rendered=template.render(
            generation_primary_smtp=dict(username='fixture@example.invalid',password=password),
            generation_primary_google=dict(enabled=True,client_id='fixture-client',client_secret=password),
            generation_primary_stripe=dict(key=password,webhook=password),
            generation_primary_runtime=dict(smtp=dict(host='smtp.invalid',port=587,tls_mode='starttls'),
                                            stripe=dict(secret_key='key',webhook_key='webhook')))
        with tempfile.TemporaryDirectory(prefix='dotenv-parser-fixture-') as folder:
            path=Path(folder)
            (path/'runtime.env').write_text(rendered)
            (path/'compose.yaml').write_text('services:\n  fixture:\n    image: '+image.stdout.strip()+'\n    network_mode: none\n    env_file: [runtime.env]\n')
            # `compose config` re-escapes dollars for round-trip serialization;
            # assert the actual process environment, not that rendered output.
            for key in ('SMTP_PASSWORD','GOOGLE_CLIENT_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET'):
                result=subprocess.run(['docker','compose','-f',str(path/'compose.yaml'),'run','--rm','--no-deps',
                                       '--entrypoint','printenv','fixture',key],capture_output=True,text=True,
                                      env=dict(os.environ,POISON='must-not-expand',UNDEFINED='must-not-expand'))
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.rstrip('\n'),password)


if __name__=='__main__':unittest.main()
