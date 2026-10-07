"""Offline localhost guard tests. No engine tasks, credentials, facts or service connections."""
from pathlib import Path
import os
import json
import subprocess
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[2]

class EntryGuardTests(unittest.TestCase):
    def test_entry_guards(self):
        with tempfile.TemporaryDirectory(prefix='database-guard-') as td:
            root=Path(td)
            (root/'ansible.cfg').write_text('[defaults]\nretry_files_enabled=False\n')
            env={'PATH':os.environ['PATH'],'HOME':td,'ANSIBLE_CONFIG':str(root/'ansible.cfg'),
                 'ANSIBLE_LOCAL_TEMP':str(root/'local'),'ANSIBLE_REMOTE_TEMP':str(root/'remote'),'ANSIBLE_NOCOLOR':'1'}
            for role,prefix in [('postgres','postgresql'),('postgresql_service','postgresql_service'),('postgres_exporter','postgres_exporter')]:
                path=ROOT/'roles/vhosts'/role
                for op,expected in [('unspecified',2),('inspect',0),('roles',2),('rotate',2),('bootstrap',2),('engine_reconfigure',2)]:
                    with self.subTest(role=role,operation=op):
                        play=[{'hosts':'localhost','connection':'local','gather_facts':False,
                               'vars_files':[str(path/'defaults/main.yml')],'vars':{prefix+'_operation':op},
                               'tasks':[{'ansible.builtin.import_tasks':str(path/'tasks/contract.yml')}]}]
                        fixture=root/'fixture.yml';fixture.write_text(yaml.safe_dump(play))
                        proc=subprocess.run(['ansible-playbook','-i','localhost,',str(fixture),'-e',json.dumps(play[0]['vars'])],env=env,capture_output=True,text=True)
                        self.assertEqual(proc.returncode,expected,proc.stdout+proc.stderr)

if __name__=='__main__': unittest.main()
