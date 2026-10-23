#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?disposable CI runtime directory required}"
task_runtime="${RUNNER_TEMP}/control-plane-owner-runtime"
python3 -m venv "$task_runtime"
"$task_runtime/bin/python" -m pip install --disable-pip-version-check \
  ansible-core==2.18.12 PyYAML==6.0.3 jsonschema==4.26.0
export PATH="$task_runtime/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
docker pull postgres@sha256:f4c66b820c6f974249089d3d16d86a3698eae11e8746eb6644b2271031e91232
export CONTROL_PLANE_POSTGRES_IMAGE=postgres@sha256:f4c66b820c6f974249089d3d16d86a3698eae11e8746eb6644b2271031e91232
export CONTROL_PLANE_POSTGRES_FIXTURE=1
python -m unittest discover -s tests/control_plane -v
bash -n roles/web_saas_data_backup/files/create_encrypted_backup.sh roles/web_saas_data_restore_verify/files/restore_verify.sh
git diff --check
