from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'roles/vhosts/vector-agent/files/vector-agent-domain-env'


class VectorAgentDomainEnvTests(unittest.TestCase):
    def run_helper(self, config, print_only=False):
        directory = tempfile.TemporaryDirectory()
        source = Path(directory.name) / 'account-agent.yaml'
        source.write_text(config, encoding='utf-8')
        output = Path(directory.name) / 'agent-domain.env'
        command = ['python3', str(HELPER)]
        if print_only:
            command.append('--print')
        else:
            command.extend(['--output', str(output)])
        command.append(str(source))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result, output, directory

    def test_prints_only_agent_id(self):
        result, _, directory = self.run_helper(
            'mode: agent\nagent:\n  id: node.example.test\n  apiToken: must-not-leak\n',
            print_only=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'node.example.test\n')
        self.assertNotIn('must-not-leak', result.stdout + result.stderr)
        directory.cleanup()

    def test_writes_only_protected_domain_environment(self):
        result, output, directory = self.run_helper(
            'mode: agent\nagent:\n  id: node.example.test\n  apiToken: must-not-leak\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), 'AGENT_PROXY_DOMAIN=node.example.test\n')
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('must-not-leak', output.read_text())
        directory.cleanup()

    def test_rejects_missing_or_invalid_agent_id(self):
        for config in ('mode: agent\n', 'mode: agent\nagent:\n  id: bad value\n'):
            result, _, directory = self.run_helper(config, print_only=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('bad value', result.stderr)
            directory.cleanup()


if __name__ == '__main__':
    unittest.main()
