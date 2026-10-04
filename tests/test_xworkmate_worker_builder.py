"""Transported pinned source archive integrity and safe extraction contracts."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import os
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('worker_builder', ROOT / 'scripts/build_xworkmate_workers.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WorkerBuilderTest(unittest.TestCase):
    def test_shared_node_entries_are_required_and_temporary_configs_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            for project in ('packages/client/connection', 'packages/typert/registry', 'packages/api/gateway'):
                (repository / project).mkdir(parents=True)
            def emit(argv, cwd, env):
                config = Path(argv[-1])
                self.assertIn('import upstream from "./tsdown.config.ts"', config.read_text())
                self.assertIn('config.platform==="node"', config.read_text())
                (cwd / 'lib').mkdir()
                (cwd / 'lib/index.js').write_text('export {}')
            with patch.object(builder, 'command', side_effect=emit):
                builder.build_worker_node_halves(repository, {})
            self.assertEqual(list(repository.rglob('.xworkmate-worker-node.config.ts')), [])
            (repository / 'packages/client/connection/lib/index.js').unlink()
            with patch.object(builder, 'command', return_value=None):
                with self.assertRaises(ValueError):
                    builder.build_worker_node_halves(repository, {})

    def test_keyless_api_requires_authentication_without_a_model_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-opencode'
            binary.write_text('#!' + sys.executable + '\n' + '''import base64,json,os,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
assert 'XWORKMATE_LLM_API_KEY' not in os.environ
cfg=json.load(open(os.environ['OPENCODE_CONFIG']))
assert cfg['providers']['xworkmate']['package']=='@opencode/ai/providers/openai-compatible'
expected='Basic '+base64.b64encode(('opencode:'+os.environ['OPENCODE_PASSWORD']).encode()).decode()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path=='/api/info'
        self.send_response(200 if self.headers.get('Authorization')==expected else 401)
        self.end_headers(); self.wfile.write(b'{"version":"2.0.22"}')
    def log_message(self,*args): pass
HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
''')
            binary.chmod(0o755)
            result = builder.smoke_opencode_api(binary, root, {'PATH': os.environ['PATH']})
            self.assertTrue(result['authenticatedInfo'])
            self.assertEqual(result['unauthenticatedStatus'], 401)
            self.assertFalse(result['modelPromptSubmitted'])

    def test_keyless_smoke_sends_no_prompt_and_uses_isolated_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'fake-dsh'
            wrapper.write_text('#!' + sys.executable + '\n' + '''import json,sys,os
assert 'XWORKMATE_LLM_API_KEY' not in os.environ
assert os.environ['DSH_HOME'].endswith('-dsh-home')
profile=sys.argv[sys.argv.index('--profile')+1]
for line in sys.stdin:
    frame=json.loads(line); method=frame['method']
    assert method in ('initialize','session/new','session/set_config_option','session/close','shutdown')
    result={}
    if method=='initialize':
        result={'agentInfo':{'name':'deepseek-harness-acp'}} if profile=='acp' else {'serverInfo':{'name':'deepseek-harness-sdk-runtime'}}
    if method=='session/new': result={'sessionId':'fixture','configOptions':[{'id':'model','options':[{'value':'["xworkmate","fixture-model"]'}]}]}
    print(json.dumps({'jsonrpc':'2.0','id':frame['id'],'result':result}),flush=True)
    if method=='shutdown': break
''')
            wrapper.chmod(0o755)
            result = builder.smoke_dsh_protocols(wrapper, root, {'PATH': os.environ['PATH']})
            self.assertEqual(result['acp'], ['initialize', 'session/new', 'session/set_config_option', 'session/close'])
            self.assertEqual(result['sdk'], ['initialize', 'shutdown'])
            wrapper.write_text(wrapper.read_text().replace('for line in sys.stdin:',
                               "print('dsh: warning: 1 entry did not activate',file=sys.stderr,flush=True)\nfor line in sys.stdin:"))
            second = root / 'incomplete'
            second.mkdir()
            with self.assertRaisesRegex(ValueError, 'inactive plugin entries'):
                builder.smoke_dsh_protocols(wrapper, second, {'PATH': os.environ['PATH']})

    def test_host_build_releases_compiler_process_between_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            with patch.object(builder, 'command', side_effect=['["./first", "./second"]', None, None]) as run:
                builder.build_host_projects(Path('/tools/pnpm'), Path('/tools/node'), repository, {})
            self.assertEqual([call.args[0][-1] for call in run.call_args_list[1:]], ['./first', './second'])
            with patch.object(builder, 'command', return_value='["../outside"]'):
                with self.assertRaises(ValueError):
                    builder.build_host_projects(Path('/tools/pnpm'), Path('/tools/node'), repository, {})

    def test_source_transport_requires_matching_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'source.tar'
            with tarfile.open(archive, 'w') as target:
                data = b'{"name":"pinned-source"}'
                member = tarfile.TarInfo('package.json')
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
            with self.assertRaises(ValueError):
                builder.export_source(None, builder.PINS['dsh'], Path(directory) / 'bad', archive, '0' * 64)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            destination = Path(directory) / 'source'
            builder.export_source(None, builder.PINS['dsh'], destination, archive, checksum)
            self.assertEqual((destination / 'package.json').read_bytes(), data)

    def test_source_transport_rejects_escape_and_tracked_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('../escape', '.env'):
                archive = Path(directory) / 'source.tar'
                with tarfile.open(archive, 'w') as target:
                    member = tarfile.TarInfo(name)
                    target.addfile(member)
                checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
                with self.subTest(name=name), self.assertRaises((ValueError, tarfile.FilterError)):
                    builder.export_source(None, builder.PINS['dsh'], Path(directory) / ('case-' + name.replace('/', '_')), archive, checksum)


if __name__ == '__main__':
    unittest.main()
