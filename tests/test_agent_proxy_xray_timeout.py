"""Render both managed Xray configs and verify their shared idle-timeout policy."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/vhosts/agent-proxy"


class AgentProxyXrayTimeoutTest(unittest.TestCase):
    def test_xhttp_and_tcp_configs_render_the_same_long_upload_idle_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            playbook = output_dir / "render-xray-configs.yml"
            tasks = []
            for template_name in (
                "xray.xhttp.template.json.j2",
                "xray.tcp.template.json.j2",
            ):
                tasks.append({
                    "ansible.builtin.template": {
                        "src": str(ROLE / "templates" / template_name),
                        "dest": str(output_dir / template_name.removesuffix(".j2")),
                    }
                })
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost",
                "gather_facts": False,
                "tasks": [
                    {"ansible.builtin.include_vars": str(ROLE / "defaults/main.yml")},
                    *tasks,
                ],
            }]))

            subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
                check=True,
                capture_output=True,
                text=True,
            )

            for template_name in (
                "xray.xhttp.template.json",
                "xray.tcp.template.json",
            ):
                with (output_dir / template_name).open() as rendered_file:
                    config = json.load(rendered_file)
                self.assertEqual(config["policy"]["levels"]["0"]["connIdle"], 3600)


if __name__ == "__main__":
    unittest.main()
