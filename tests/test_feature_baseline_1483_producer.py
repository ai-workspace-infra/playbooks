import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import produce_feature_baseline_1483 as producer

class ProducerTest(unittest.TestCase):
    def setUp(self):
        self.row = dict(producer.ACCOUNTS)
        self.proof = {'schema':'main-image-build/v1', 'source_repository':self.row['repo'],
                      'source_sha':self.row['sha'], 'image':self.row['image'], 'event':'push',
                      'ref':'refs/heads/main', 'run_id':self.row['run'], 'attempt':1}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('main-image-build.json',json.dumps(self.proof))
        self.archive=stream.getvalue()
        self.run={'id':self.row['run'],'path':'.github/workflows/ci-pipeline.yml','event':'push',
                  'head_branch':'main','head_sha':self.row['sha'],'run_attempt':1,'status':'completed',
                  'conclusion':'success','repository':{'full_name':self.row['repo']},
                  'head_repository':{'full_name':self.row['repo']}}
        self.meta={'id':self.row['artifact'],'expired':False,'name':f"{self.row['prefix']}-{self.row['run']}-1",
                   'workflow_run':{'id':self.row['run'],'head_sha':self.row['sha']},
                   'size_in_bytes':len(self.archive),'digest':'sha256:'+hashlib.sha256(self.archive).hexdigest()}
    def fake_api(self,path,*,binary=False):
        if path.endswith('/zip'): return self.archive
        if '/actions/artifacts/' in path: return self.meta
        if '/actions/runs/' in path: return self.run
        if '/git/ref/heads/main' in path: return {'object':{'sha':self.row['sha']}}
        raise AssertionError(path)
    def test_authenticates_run_and_artifact_and_refuses_drift(self):
        with patch.object(producer,'api',side_effect=self.fake_api):
            producer.check_main(self.row); producer.check_build(self.row)
            self.run['head_sha']='f'*40
            with self.assertRaises(ValueError): producer.check_build(self.row)
            self.run['head_sha']=self.row['sha']; self.meta['digest']='sha256:'+'0'*64
            with self.assertRaises(ValueError): producer.check_build(self.row)
    def test_registry_token_must_be_read_packages_only_and_match_username(self):
        class Response(io.BytesIO):
            def __init__(self, scopes, login):
                super().__init__(json.dumps({'login':login}).encode()); self.headers={'X-OAuth-Scopes':scopes}
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        for scopes,login,allowed in [('read:packages','reader',True), ('read:packages, repo','reader',False),
                                     ('write:packages','reader',False), ('','reader',False), ('read:packages','other',False)]:
            with self.subTest(scopes=scopes,login=login), patch.object(producer.urllib.request,'urlopen',return_value=Response(scopes,login)):
                if allowed: producer.validate_registry_credential('reader','fixture')
                else:
                    with self.assertRaises(ValueError): producer.validate_registry_credential('reader','fixture')

    def test_denies_unprotected_workflow_context(self):
        with patch.dict('os.environ', {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'ai-workspace-infra/playbooks',
                    'GITHUB_REF':'refs/heads/other','GITHUB_EVENT_NAME':'workflow_dispatch',
                    'GITHUB_WORKFLOW_REF':'ai-workspace-infra/playbooks/.github/workflows/feature-baseline-1483.yml@refs/heads/main'},clear=True):
            with patch.object(producer.subprocess,'run') as run:
                with self.assertRaises(ValueError): producer.main()
                run.assert_not_called()

if __name__ == '__main__': unittest.main()
