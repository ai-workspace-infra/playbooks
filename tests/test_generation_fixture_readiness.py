"""Fixture readiness must test the same TCP transport as integration DSNs."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FixtureReadinessTests(unittest.TestCase):
    def test_all_postgres_probes_use_tcp_not_the_init_socket(self):
        for script in ['postgres_cases.py', 'restore_runtime.py']:
            tree = ast.parse((ROOT / 'roles/web_saas_generation_fresh/files' / script).read_text())
            probes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.List):
                    literals = [v.value for v in node.elts if isinstance(v, ast.Constant)]
                    if 'pg_isready' in literals:
                        probes.append(literals)
                        self.assertIn('-h', literals)
                        self.assertEqual(literals[literals.index('-h') + 1], '127.0.0.1')
            self.assertTrue(probes, script)


if __name__ == '__main__':
    unittest.main()
