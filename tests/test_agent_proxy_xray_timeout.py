"""Verify Xray policy rendering, Agent reloads and scoped runtime reconciliation."""

import json
import importlib.util
import copy
import grp
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/vhosts/agent-proxy"


class AgentProxyXrayTimeoutTest(unittest.TestCase):
    def test_existing_configuration_is_preserved_and_runtime_drift_requests_reload(self):
        spec = importlib.util.spec_from_file_location("upload_tuner", ROLE / "files/configure_upload_timeout.py")
        tuner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tuner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            names = ["templates/xray.xhttp.template.json", "templates/xray.tcp.template.json", "config.json", "tcp-config.json"]
            for name in names:
                (root / name).write_text(json.dumps({"policy": {"levels": {"0": {"connIdle": 120, "bufferSize": 512}}}, "marker": "{{ UUID }}"}))
            self.assertTrue(tuner.apply(directory, 3600)["reload_required"])
            for name in names[:2]:
                value = json.loads((root / name).read_text())
                self.assertEqual(value["marker"], "{{ UUID }}")
                self.assertEqual(value["policy"]["levels"]["0"]["bufferSize"], 512)
            # Disk templates now match, but cached Agent/runtime is still stale.
            self.assertTrue(tuner.apply(directory, 3600)["reload_required"])
            for name in names[2:]:
                (root / name).write_text(json.dumps({"policy": {"levels": {"0": {"connIdle": 3600}}}}))
            self.assertFalse(tuner.apply(directory, 3600)["reload_required"])
            self.assertEqual(len(list(root.glob("upload-timeout-backup-*"))), 1)

    def test_template_changes_reload_the_agent_once_and_unchanged_runs_do_not(self):
        source_tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        template_tasks = [copy.deepcopy(task) for task in source_tasks
                          if task.get("name") in ("Deploy XHTTP template", "Deploy TCP template")]
        self.assertEqual(len(template_tasks), 2)
        for task in template_tasks:
            task["ansible.builtin.template"]["src"] = str(
                ROLE / "templates" / task["ansible.builtin.template"]["src"]
            )
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            counter = output_dir / "agent-reloads"
            playbook = output_dir / "render-and-reload.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "tasks": [
                    {"ansible.builtin.include_vars": str(ROLE / "defaults/main.yml")},
                    *template_tasks,
                ],
                "handlers": [{
                    "name": "Restart agent-proxy",
                    "ansible.builtin.command": {"argv": [
                        sys.executable, "-c",
                        "import sys; open(sys.argv[1], 'a').write('x')", str(counter),
                    ]},
                }],
            }]))
            command = [
                "ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook),
                "-e", json.dumps({
                    "xray_template_dir": str(output_dir),
                    "agent_proxy_user": pwd.getpwuid(os.getuid()).pw_name,
                    "agent_proxy_group": grp.getgrgid(os.getgid()).gr_name,
                }),
            ]
            def render():
                subprocess.run(command, check=True, capture_output=True, text=True)

            render()
            self.assertEqual(counter.read_text(), "x")
            render()
            self.assertEqual(counter.read_text(), "x")
            for filename, expected in (("xray.xhttp.template.json", "xx"),
                                       ("xray.tcp.template.json", "xxx")):
                path = output_dir / filename
                path.write_text(path.read_text() + "\n")
                render()
                self.assertEqual(counter.read_text(), expected)

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
