"""Keyless deployment/runtime contracts; never invoke systemd or a model."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/xworkmate_workers'
spec = importlib.util.spec_from_file_location('worker_launcher', ROLE / 'files/xworkmate-worker-launch.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
RUN_ID = '550e8400-e29b-41d4-a716-446655440000'


def config():
    return {
        'user': 'xworkmate-worker', 'group': 'xworkmate-worker', 'gatewayUser': 'openclaw',
        'stateRoot': '/var/lib/xworkmate-workers', 'configDir': '/etc/xworkmate-workers', 'gatewayTasksRoot': '/srv/openclaw/workspace/tasks',
        'modelEnvFile': '/run/xworkmate-workers/model.env', 'opencodeEnvFile': '/run/xworkmate-workers/opencode.env',
        'modelService': {'baseUrl': 'https://aggregate.example/v1', 'model': 'test-model'},
        'egressCidrs': ['192.0.2.11/32'], 'exportMaxBytes': 1048576,
        'dsh': {'releaseDir': '/opt/xworkmate-workers/releases/dsh-test', 'entrypoint': 'bin/dsh', 'sourceRevision': 'a' * 40},
        'opencode': {'releaseDir': '/opt/xworkmate-workers/releases/opencode-test', 'entrypoint': 'bin/opencode', 'sourceRevision': 'b' * 40, 'port': 4097},
        'limits': {'memoryMax': '2G', 'cpuQuota': '200%', 'tasksMax': 256, 'maxRuntimeSec': 900, 'stopTimeoutSec': 30},
    }


class WorkerRuntimeContractTest(unittest.TestCase):
    def test_no_implicit_deployment_or_unpinned_release(self):
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.assertFalse(defaults['xworkmate_workers_enabled'])
        self.assertEqual(defaults['xworkmate_workers_dsh_artifact_url'], '')
        self.assertEqual(defaults['xworkmate_workers_opencode_artifact_sha256'], '')
        self.assertEqual(defaults['xworkmate_workers_dsh_source_revision'], '639ed015397290b3745d163aafe02ffee4aa3f84')
        self.assertEqual(defaults['xworkmate_workers_opencode_source_revision'], '35a41b5d53c71ae0337e614ec192fd5d1a5c7eb8')

    def test_url_credentials_ambient_models_and_unbounded_egress_rejected(self):
        launcher.validate_config(config())
        for url in ('http://aggregate.example/v1', 'https://u:p@aggregate.example/v1',
                    'https://aggregate.example/v1?token=fixture', 'https://aggregate.example/v1#x'):
            case = config()
            case['modelService']['baseUrl'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                launcher.validate_config(case)
        for networks in ([], ['0.0.0.0/0'], ['::/0']):
            case = config()
            case['egressCidrs'] = networks
            with self.subTest(networks=networks), self.assertRaises(ValueError):
                launcher.validate_config(case)

    def test_profile_and_uuid_cannot_inject_root_commands(self):
        for profile, run in [('bash', RUN_ID), ('dsh-acp', '../etc'), ('dsh-sdk', RUN_ID + ';id'), ('dsh-sdk', RUN_ID.upper())]:
            with self.subTest(profile=profile, run=run), self.assertRaises(ValueError):
                launcher.namespace(profile, run)
        for profile, upstream in [('dsh-acp', 'acp'), ('dsh-sdk', 'sdk')]:
            argv, root, unit = launcher.launch_argv(config(), profile, RUN_ID)
            self.assertIn('--pipe', argv)
            self.assertIn('--wait', argv)
            self.assertEqual(argv[argv.index('--profile') + 1], upstream)
            self.assertIn('User=xworkmate-worker', argv)
            self.assertIn('KillMode=control-group', argv)
            self.assertIn('IPAddressDeny=any', argv)
            self.assertIn('BindPaths=' + str(root), argv)
            self.assertIn('TemporaryFileSystem=/var/lib/xworkmate-workers:ro', argv)
            self.assertEqual(str(root), '/var/lib/xworkmate-workers/runs/' + profile + '/' + RUN_ID)
            self.assertNotIn('XWORKMATE_LLM_API_KEY', ' '.join(argv))

    def test_archive_path_and_link_escape_rejected_before_root_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, link in [('../escape', None), ('/escape', None), ('bin/dsh', '../../escape')]:
                file = Path(directory) / 'unsafe.tar'
                with tarfile.open(file, 'w') as archive:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = link
                    archive.addfile(member)
                with self.subTest(name=name, link=link), self.assertRaises(ValueError):
                    launcher.archive_check(file)

    def test_artifact_manifest_and_executable_revision_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'bin').mkdir()
            entry = root / 'bin/dsh'
            entry.write_text('#!/bin/false\n')
            entry.chmod(0o755)
            manifest = root / 'runtime-manifest.json'
            manifest.write_text(json.dumps({'engine': 'dsh', 'sourceRevision': 'a' * 40}))
            self.assertEqual(launcher.artifact_check(config(), 'dsh', root), entry)
            manifest.write_text(json.dumps({'engine': 'dsh', 'sourceRevision': 'b' * 40}))
            with self.assertRaises(ValueError):
                launcher.artifact_check(config(), 'dsh', root)

    def test_runtime_env_literal_only_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'model.env'
            file.write_text('XWORKMATE_LLM_API_KEY="fixture-only$literal"\n')
            file.chmod(0o600)
            result = launcher.read_runtime_env(file, {'XWORKMATE_LLM_API_KEY'}, {'XWORKMATE_LLM_API_KEY'}, (os.getuid(),))
            self.assertEqual(result['XWORKMATE_LLM_API_KEY'], 'fixture-only$literal')
            file.chmod(0o644)
            with self.assertRaises(ValueError):
                launcher.read_runtime_env(file, {'XWORKMATE_LLM_API_KEY'}, {'XWORKMATE_LLM_API_KEY'}, (os.getuid(),))
            file.chmod(0o600)
            file.write_text('PATH=/tmp/attacker\nXWORKMATE_LLM_API_KEY=fixture\n')
            with self.assertRaises(ValueError):
                launcher.read_runtime_env(file, {'XWORKMATE_LLM_API_KEY'}, {'XWORKMATE_LLM_API_KEY'}, (os.getuid(),))

    def test_export_only_completed_output_bytes_rejects_symlinks_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            case = config()
            case['stateRoot'] = str(Path(directory).resolve())
            output = Path(case['stateRoot']) / 'runs/dsh-acp' / RUN_ID / 'workspace/output'
            output.mkdir(parents=True)
            (output / 'report.md').write_text('verified artifact')
            result = launcher.export_files(case, 'dsh-acp', RUN_ID)
            self.assertEqual(result['runId'], RUN_ID)
            file = result['artifacts'][0]
            self.assertEqual(base64.b64decode(file['contentBase64']), b'verified artifact')
            self.assertEqual(file['relativePath'], 'report.md')
            case['exportMaxBytes'] = 1
            with self.assertRaises(ValueError):
                launcher.export_files(case, 'dsh-acp', RUN_ID)
            case['exportMaxBytes'] = 1048576
            (output / 'escape').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                launcher.export_files(case, 'dsh-acp', RUN_ID)

    def test_templates_share_one_model_and_keep_sdk_no_escalation(self):
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        context = {**defaults,
                   'xworkmate_workers_llm_base_url': 'https://aggregate.example/v1',
                   'xworkmate_workers_llm_model': 'test-model',
                   'xworkmate_workers_gateway_tasks_root': '/srv/openclaw/workspace/tasks',
                   'xworkmate_workers_dsh_release_dir': '/opt/xworkmate-workers/releases/dsh-test',
                   'xworkmate_workers_opencode_release_dir': '/opt/xworkmate-workers/releases/opencode-test',
                   'xworkmate_workers_model_env_file': '/run/xworkmate-workers/model.env',
                   'xworkmate_workers_opencode_env_file': '/run/xworkmate-workers/opencode.env',
                   'xworkmate_workers_egress_cidrs': ['192.0.2.11/32']}
        env = Environment(undefined=StrictUndefined)
        env.filters['to_nice_json'] = lambda value: json.dumps(value, indent=2)
        env.filters['to_json'] = json.dumps
        rendered = lambda name: env.from_string((ROLE / 'templates' / name).read_text()).render(context)
        server = json.loads(rendered('opencode.json.j2'))
        self.assertEqual(server['model'], {'providerID': 'xworkmate', 'model': 'test-model'})
        self.assertEqual(server['providers']['xworkmate']['settings']['apiKey'], '{env:XWORKMATE_LLM_API_KEY}')
        self.assertEqual(server['providers']['xworkmate']['package'], '@opencode/ai/providers/openai-compatible')
        self.assertEqual(server['update'], 'disable')
        rules = server['permissions']
        self.assertEqual(rules[0], {'action': '*', 'resource': '*', 'effect': 'deny'})
        self.assertEqual(rules[-1], {'action': 'external_directory', 'resource': '*', 'effect': 'deny'})
        self.assertFalse(any(rule['action'] == 'shell' for rule in rules))
        self.assertEqual(json.loads(rendered('gateway-tool-opt-in.json.j2')), {'tools': {'allow': ['xworkmate_worker']}})
        gateway = json.loads(rendered('gateway-model-service.json.j2'))
        self.assertEqual(gateway['models']['providers']['xworkmate']['apiKey'],
                         {'source': 'env', 'provider': 'default', 'id': 'XWORKMATE_LLM_API_KEY'})
        self.assertEqual(gateway['agents']['list'][0]['model'], 'xworkmate/test-model')
        self.assertEqual(yaml.safe_load(rendered('dsh-acp.patch.yml.j2'))[0]['config']['provider'], 'xworkmate')
        sdk = yaml.safe_load(rendered('dsh-sdk.patch.yml.j2'))
        self.assertEqual(sdk[0]['config']['policy'], 'never')
        self.assertFalse(sdk[1]['config']['maxTokensAsSuccess'])
        worker = json.loads(rendered('gateway-worker-runtime.json.j2'))['workerRuntime']
        self.assertEqual(worker['command'], ['/usr/bin/sudo', '-n', '/usr/local/libexec/xworkmate-worker-launch'])
        self.assertEqual(worker['stateRoot'], '/var/lib/xworkmate-workers/runs')
        self.assertEqual(worker['modelService']['credentialEnvFile'], '/run/xworkmate-workers/model.env')
        self.assertLessEqual(worker['timeoutMs'], 900000)
        self.assertTrue(worker['collectArtifacts'])
        service = rendered('opencode.service.j2')
        self.assertIn('serve --hostname 127.0.0.1 --port 4097', service)
        self.assertIn('IPAddressDeny=any', service)
        self.assertIn('IPAddressAllow=192.0.2.11/32', service)
        self.assertNotIn('fixture-only', service)

    def test_reused_run_namespace_is_rejected_before_root_chown_or_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            case = config()
            case['stateRoot'] = str(Path(directory).resolve())
            run = Path(case['stateRoot']) / 'runs/dsh-acp' / RUN_ID
            run.mkdir(parents=True)
            (run / 'home').symlink_to('/etc')
            with patch.object(launcher, 'preflight'), patch.object(launcher.subprocess, 'Popen') as execute:
                with self.assertRaises(ValueError):
                    launcher.launch(case, 'dsh-acp', RUN_ID)
                execute.assert_not_called()

    def test_fixed_adapter_paths_and_timeout_fail_closed(self):
        for field, value in [('stateRoot', '/var/lib/other-workers'), ('maxRuntimeSec', 901), ('port', 4098)]:
            case = config()
            if field == 'maxRuntimeSec':
                case['limits'][field] = value
            elif field == 'port':
                case['opencode'][field] = value
            else:
                case[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                launcher.validate_config(case)

    def test_release_candidate_rejects_external_dependency_links(self):
        spec = importlib.util.spec_from_file_location('worker_builder', ROOT / 'scripts/build_xworkmate_workers.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'candidate'
            root.mkdir()
            (root / 'dependency').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                builder.archive_candidate(root, 'dsh', Path(directory), 'linux-x64')

    def test_readiness_redirects_cannot_forward_credentials(self):
        with self.assertRaises(ValueError):
            launcher.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.example')


if __name__ == '__main__':
    unittest.main()
