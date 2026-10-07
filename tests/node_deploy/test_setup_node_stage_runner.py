import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / ".github/actions/setup-node-stage-runner/scripts/setup.sh"


class SetupNodeStageRunnerTests(unittest.TestCase):
    def run_setup(self, **overrides):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fake_python = root / "python3"
        log = root / "calls.log"
        fake_python.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf 'bootstrap:%s\\n' "$*" >> "$CALL_LOG"
[[ "$1 $2" == '-m venv' ]]
mkdir -p "$3/bin"
cat > "$3/bin/python" <<'EOF'
#!/usr/bin/env bash
printf 'python:%s\\n' "$*" >> "$CALL_LOG"
EOF
cat > "$3/bin/ansible-galaxy" <<'EOF'
#!/usr/bin/env bash
printf 'galaxy:%s\\n' "$*" >> "$CALL_LOG"
EOF
chmod +x "$3/bin/python" "$3/bin/ansible-galaxy"
"""
        )
        fake_python.chmod(0o755)
        github_path = root / "github-path"
        env = dict(os.environ)
        env.update(
            NODE_STAGE_VENV=str(root / "venv"),
            NODE_STAGE_PYTHON=str(fake_python),
            GITHUB_PATH=str(github_path),
            CALL_LOG=str(log),
        )
        env.update(overrides)
        result = subprocess.run(["bash", str(SETUP)], env=env, capture_output=True, text=True)
        return result, root, log, github_path

    def test_preserves_isolated_runtime_and_exact_versions(self):
        result, root, log, github_path = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text()
        self.assertIn(f"bootstrap:-m venv {root / 'venv'}", calls)
        self.assertIn("python:-m pip install --disable-pip-version-check pyyaml==6.0.2 ansible-core==2.17.14", calls)
        self.assertIn("galaxy:collection install ansible.posix:==2.1.0", calls)
        self.assertEqual(github_path.read_text().strip(), str(root / "venv/bin"))

    def test_rejects_relative_path_and_non_exact_version_before_bootstrap(self):
        for overrides in (
            {"NODE_STAGE_VENV": "relative/venv"},
            {"NODE_STAGE_ANSIBLE_CORE_VERSION": "latest"},
        ):
            with self.subTest(overrides=overrides):
                result, _root, log, _github_path = self.run_setup(**overrides)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
