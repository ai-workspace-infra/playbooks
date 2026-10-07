import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'roles/vhosts/xconnect_lab_runtime/files/manage_private_probe.sh'
RUN = 'xcl-123-1'


def executable(path: Path, body: str) -> None:
    path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body + '\n')
    path.chmod(0o755)


class PrivateProbeHelperTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        commands = root / 'bin'
        commands.mkdir()
        active = root / 'active'
        log = root / 'commands.log'
        executable(commands / 'ip', 'echo "2: xconzero0 inet 10.77.0.1/32 scope global xconzero0"')
        executable(commands / 'sleep', ':')
        executable(commands / 'systemd-run', 'echo "systemd-run $*" >> "$STUB_LOG"; touch "$STUB_ACTIVE"')
        executable(commands / 'curl', 'printf "%s" "${STUB_MARKER:-xcl-123-1}"')
        executable(commands / 'systemctl', r'''
echo "systemctl $*" >> "$STUB_LOG"
case "$1" in
  show) [[ -e "$STUB_ACTIVE" ]] && echo loaded || echo not-found ;;
  stop)
    [[ "${STUB_STOP_FAIL:-false}" != true ]] || exit 4
    rm -f "$STUB_ACTIVE" ;;
  is-active) [[ -e "$STUB_ACTIVE" ]] ;;
  is-failed) exit 1 ;;
  *) exit 2 ;;
esac
''')
        env = dict(os.environ,
                   PATH=f'{commands}:/usr/bin:/bin',
                   XCONNECT_PRIVATE_PROBE_RUNTIME_ROOT=str(root / 'run'),
                   STUB_ACTIVE=str(active), STUB_LOG=str(log))
        return root, env, active, log

    def invoke(self, env, operation):
        return subprocess.run([
            'bash', str(HELPER), operation, RUN, '10.77.0.1', 'xconzero0', '8080',
        ], env=env, capture_output=True, text=True)

    def test_run_id_cannot_escape_the_runtime_root(self):
        root, env, _active, _ = self.fixture()
        result = subprocess.run([
            'bash', str(HELPER), 'cleanup', 'xcl-123-1/../escape',
            '10.77.0.1', 'xconzero0', '8080',
        ], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((root / 'escape').exists())
        self.assertFalse((root / 'run').exists())

    def test_setup_requires_exact_unit_and_marker_then_cleanup_removes_it(self):
        root, env, active, log = self.fixture()
        result = self.invoke(env, 'setup')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'private_probe=ready run={RUN}', result.stdout)
        self.assertTrue(active.exists())
        marker = root / f'run/xconnect-one-{RUN}/index.html'
        self.assertEqual(marker.read_text(), RUN + '\n')
        cleanup = self.invoke(env, 'cleanup')
        self.assertEqual(cleanup.returncode, 0, cleanup.stdout + cleanup.stderr)
        self.assertIn(f'private_probe=removed run={RUN}', cleanup.stdout)
        self.assertFalse(marker.parent.exists())
        self.assertIn(f'systemctl stop xconnect-lab-probe-{RUN}.service', log.read_text())

    def test_unrelated_listener_marker_cannot_satisfy_readiness(self):
        root, env, active, _ = self.fixture()
        env['STUB_MARKER'] = 'some-other-service'
        result = self.invoke(env, 'setup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not become ready with the exact marker', result.stderr)
        self.assertFalse(active.exists())
        self.assertFalse((root / f'run/xconnect-one-{RUN}').exists())

    def test_failed_setup_always_removes_transient_state(self):
        root, env, active, _ = self.fixture()
        executable(Path(env['PATH'].split(':', 1)[0]) / 'systemctl', r'''
case "$1" in
  show) [[ -e "$STUB_ACTIVE" ]] && echo loaded || echo not-found ;;
  stop) rm -f "$STUB_ACTIVE" ;;
  is-active) exit 1 ;;
  is-failed) exit 1 ;;
esac
''')
        result = self.invoke(env, 'setup')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(active.exists())
        self.assertFalse((root / f'run/xconnect-one-{RUN}').exists())

    def test_cleanup_failure_is_reported_and_never_claimed_removed(self):
        root, env, active, _ = self.fixture()
        active.touch()
        probe = root / f'run/xconnect-one-{RUN}'
        probe.mkdir(parents=True)
        env['STUB_STOP_FAIL'] = 'true'
        result = self.invoke(env, 'cleanup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unable to stop private probe unit', result.stderr)
        self.assertNotIn('private_probe=removed', result.stdout)
        self.assertTrue(probe.exists())

    def test_unit_query_failure_blocks_directory_removal_and_success_receipt(self):
        root, env, _active, _ = self.fixture()
        probe = root / f'run/xconnect-one-{RUN}'
        probe.mkdir(parents=True)
        executable(Path(env['PATH'].split(':', 1)[0]) / 'systemctl', r'''
if [[ "$1" == show ]]; then
  echo "unit query unavailable" >&2
  exit 3
fi
exit 2
''')
        result = self.invoke(env, 'cleanup')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unable to inspect private probe unit', result.stderr)
        self.assertNotIn('private_probe=removed', result.stdout)
        self.assertTrue(probe.exists())

    def test_empty_or_unknown_load_state_is_not_treated_as_not_found(self):
        root, env, _active, _ = self.fixture()
        probe = root / f'run/xconnect-one-{RUN}'
        probe.mkdir(parents=True)
        for state in ('', 'mystery'):
            with self.subTest(state=state):
                executable(Path(env['PATH'].split(':', 1)[0]) / 'systemctl', f'''
if [[ "$1" == show ]]; then printf "%s\\n" "{state}"; exit 0; fi
exit 2
''')
                result = self.invoke(env, 'cleanup')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unexpected private probe unit state', result.stderr)
                self.assertNotIn('private_probe=removed', result.stdout)
                self.assertTrue(probe.exists())


if __name__ == '__main__':
    unittest.main()
