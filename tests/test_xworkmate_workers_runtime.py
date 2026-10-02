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
from types import SimpleNamespace
import threading
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
            self.assertIn('ConditionPathExists=!/var/lib/xworkmate-workers/cancelled/' + profile + '-' + RUN_ID, argv)
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

    def test_cancel_scope_receipt_no_config_or_runtime_credential_read(self):
        original_lstat = Path.lstat
        original_fstat = launcher.os.fstat
        root_fd = lambda fd: SimpleNamespace(st_mode=original_fstat(fd).st_mode, st_uid=0)
        root_metadata = lambda path: SimpleNamespace(st_mode=original_lstat(path).st_mode, st_uid=0)
        with tempfile.TemporaryDirectory() as directory:
            marker_root = Path(directory).resolve() / 'cancelled'
            replies = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=3, stdout='inactive\n')]
            with patch.object(launcher, 'CANCEL_ROOT', marker_root), patch.object(Path, 'lstat', root_metadata), patch.object(launcher.os, 'fstat', root_fd), \
                 patch.object(launcher.os, 'geteuid', return_value=0), \
                 patch.object(launcher, 'load_config', side_effect=AssertionError('cancel must not load configuration')), \
                 patch.object(launcher, 'read_runtime_env', side_effect=AssertionError('cancel must not read credentials')), \
                 patch.object(launcher.subprocess, 'run', side_effect=replies) as execute, patch('builtins.print') as output:
                self.assertEqual(launcher.main(['cancel', 'dsh-acp', RUN_ID]), 0)
                receipt = json.loads(output.call_args.args[0])
                self.assertEqual(receipt, {'profile': 'dsh-acp', 'runId': RUN_ID, 'workerStopped': True})
                self.assertTrue((marker_root / ('dsh-acp-' + RUN_ID)).is_file())
                self.assertEqual(execute.call_args_list[0].args[0], ['/usr/bin/systemctl', 'stop', 'xworkmate-dsh-acp-' + RUN_ID + '.service'])
                self.assertEqual(execute.call_args_list[0].kwargs['timeout'], 60)
                self.assertLessEqual(execute.call_args_list[1].kwargs['timeout'], 65)

    def test_cancel_rejects_live_unknown_failures_and_accepts_notloaded_idempotently(self):
        original_lstat = Path.lstat
        original_fstat = launcher.os.fstat
        root_fd = lambda fd: SimpleNamespace(st_mode=original_fstat(fd).st_mode, st_uid=0)
        root_metadata = lambda path: SimpleNamespace(st_mode=original_lstat(path).st_mode, st_uid=0)
        with tempfile.TemporaryDirectory() as directory:
            marker_root = Path(directory).resolve() / 'cancelled'
            cases = [(0, 0, 'active', False), (0, 3, 'failed', False),
                     (0, 3, 'deactivating', False), (0, 1, '', False),
                     (1, 3, 'inactive', False), (5, 4, 'unknown', True), (0, 3, 'inactive', True)]
            for stop_rc, state_rc, state, accepted in cases:
                replies = [SimpleNamespace(returncode=stop_rc), SimpleNamespace(returncode=state_rc, stdout=state)]
                with self.subTest(state=state, stop=stop_rc), patch.object(launcher, 'CANCEL_ROOT', marker_root), \
                     patch.object(Path, 'lstat', root_metadata), patch.object(launcher.os, 'fstat', root_fd), patch.object(launcher.subprocess, 'run', side_effect=replies):
                    if accepted:
                        self.assertTrue(launcher.cancel('dsh-sdk', RUN_ID)['workerStopped'])
                    else:
                        with self.assertRaises(ValueError):
                            launcher.cancel('dsh-sdk', RUN_ID)

    def test_cancel_timeout_keeps_fence_and_invalid_scope_has_no_privileged_side_effects(self):
        with patch.object(launcher.subprocess, 'run') as execute:
            for profile, run in [('shell', RUN_ID), ('dsh-acp', '../etc'), ('dsh-sdk', RUN_ID + ' extra')]:
                with self.subTest(profile=profile, run=run), self.assertRaises(ValueError):
                    launcher.cancel(profile, run)
            execute.assert_not_called()
        original_lstat = Path.lstat
        original_fstat = launcher.os.fstat
        root_fd = lambda fd: SimpleNamespace(st_mode=original_fstat(fd).st_mode, st_uid=0)
        root_metadata = lambda path: SimpleNamespace(st_mode=original_lstat(path).st_mode, st_uid=0)
        with tempfile.TemporaryDirectory() as directory:
            marker_root = Path(directory).resolve() / 'cancelled'
            with patch.object(launcher, 'CANCEL_ROOT', marker_root), patch.object(Path, 'lstat', root_metadata), patch.object(launcher.os, 'fstat', root_fd), \
                 patch.object(launcher.subprocess, 'run', side_effect=launcher.subprocess.TimeoutExpired('systemctl', 60)):
                with self.assertRaises(launcher.subprocess.TimeoutExpired):
                    launcher.cancel('dsh-acp', RUN_ID)
                self.assertTrue((marker_root / ('dsh-acp-' + RUN_ID)).exists())

    def test_cancelled_uuid_cannot_launch_or_read_model_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            marker_root = Path(directory) / 'cancelled'
            marker_root.mkdir()
            (marker_root / ('dsh-acp-' + RUN_ID)).touch()
            with patch.object(launcher, 'CANCEL_ROOT', marker_root), patch.object(launcher, 'preflight') as inspect, \
                 patch.object(launcher.subprocess, 'Popen') as execute:
                with self.assertRaises(ValueError):
                    launcher.launch(config(), 'dsh-acp', RUN_ID)
                inspect.assert_not_called()
                execute.assert_not_called()

    def test_cancel_waits_for_launch_registration_under_same_real_advisory_lock(self):
        original_lstat, original_fstat = Path.lstat, launcher.os.fstat
        root_metadata = lambda path: SimpleNamespace(st_mode=original_lstat(path).st_mode, st_uid=0)
        root_fd = lambda fd: SimpleNamespace(st_mode=original_fstat(fd).st_mode, st_uid=0)
        registration_started, permit_registration = threading.Event(), threading.Event()
        cancel_started, cancelled = threading.Event(), threading.Event()
        registered = []
        errors, receipts = [], []
        def manager(argv, **kwargs):
            if argv[1] == 'show':
                registration_started.set()
                if not permit_registration.wait(2):
                    raise AssertionError('fixture registration was not released')
                registered.append(True)
                return SimpleNamespace(returncode=0, stdout='loaded\n')
            if argv[1] == 'stop':
                if not registered:
                    raise AssertionError('cancel raced ahead of admitted unit registration')
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=3, stdout='inactive\n')
        def settled_wait():
            if not cancelled.wait(2):
                raise AssertionError('fixture owned worker was not cancelled')
            return 0
        child = SimpleNamespace(poll=lambda: None, wait=settled_wait)
        with tempfile.TemporaryDirectory() as directory:
            case = config()
            case['stateRoot'] = str(Path(directory).resolve())
            (Path(case['stateRoot']) / 'runs/dsh-acp').mkdir(parents=True)
            marker_root = Path(case['stateRoot']) / 'cancelled'
            def start():
                try:
                    launcher.launch(case, 'dsh-acp', RUN_ID)
                except BaseException as error:
                    errors.append(error)
            def stop_owned():
                cancel_started.set()
                try:
                    receipts.append(launcher.cancel('dsh-acp', RUN_ID))
                except BaseException as error:
                    errors.append(error)
                finally:
                    cancelled.set()
            with patch.object(launcher, 'CANCEL_ROOT', marker_root), patch.object(Path, 'lstat', root_metadata), \
                 patch.object(launcher.os, 'fstat', root_fd), patch.object(launcher, 'preflight'), \
                 patch.object(launcher.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())), \
                 patch.object(launcher.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=os.getgid())), \
                 patch.object(launcher.os, 'chown'), patch.object(launcher.signal, 'signal'), \
                 patch.object(launcher.subprocess, 'Popen', return_value=child) as execute, \
                 patch.object(launcher.subprocess, 'run', side_effect=manager):
                start_thread = threading.Thread(target=start)
                cancel_thread = threading.Thread(target=stop_owned)
                start_thread.start()
                self.assertTrue(registration_started.wait(2))
                cancel_thread.start()
                self.assertTrue(cancel_started.wait(2))
                self.assertFalse(cancelled.wait(0.1), 'cancel returned before unit registration settled')
                self.assertFalse((marker_root / ('dsh-acp-' + RUN_ID)).exists())
                permit_registration.set()
                start_thread.join(2)
                cancel_thread.join(2)
                self.assertFalse(start_thread.is_alive() or cancel_thread.is_alive())
                self.assertFalse(errors)
                self.assertTrue(receipts[0]['workerStopped'])
                condition = 'ConditionPathExists=!' + str(marker_root / ('dsh-acp-' + RUN_ID))
                self.assertIn(condition, execute.call_args.args[0])
                # A manager-side delayed start after launcher hard kill reads the
                # negative condition against the now-present root-owned fence.
                self.assertTrue((marker_root / ('dsh-acp-' + RUN_ID)).exists())

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
