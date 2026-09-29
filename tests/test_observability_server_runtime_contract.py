"""Keep the standalone Observability entrypoint usable on a fresh VM."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_observability_server_installs_runtime_before_compose_role() -> None:
    playbook = yaml.safe_load((ROOT / "deploy_observability_server.yml").read_text())
    roles = playbook[0]["roles"]

    assert roles.index("vhosts/docker") < roles.index("docker/observability-server")
