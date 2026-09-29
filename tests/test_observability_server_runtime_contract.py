"""Keep the standalone Observability entrypoint usable on a fresh VM."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_observability_server_installs_runtime_before_compose_role() -> None:
    playbook = yaml.safe_load((ROOT / "deploy_observability_server.yml").read_text())
    roles = playbook[0]["roles"]

    assert roles.index("vhosts/docker") < roles.index("docker/observability-server")


def test_observability_server_creates_caddy_include_directory() -> None:
    tasks = (ROOT / "roles/docker/observability-server/tasks/main.yml").read_text()

    assert "Ensure Caddy configuration directory for observability exists" in tasks
    assert 'path: "{{ observability_caddy_conf_dir }}"' in tasks
    assert tasks.index("Ensure Caddy configuration directory for observability exists") < tasks.index(
        "Template Caddy configuration for observability"
    )
