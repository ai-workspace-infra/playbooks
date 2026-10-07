#!/usr/bin/env bash
set -euo pipefail

venv_path="${NODE_STAGE_VENV:?NODE_STAGE_VENV is required}"
python_bin="${NODE_STAGE_PYTHON:-python3}"
pyyaml_version="${NODE_STAGE_PYYAML_VERSION:-6.0.2}"
ansible_core_version="${NODE_STAGE_ANSIBLE_CORE_VERSION:-2.17.14}"
ansible_posix_version="${NODE_STAGE_ANSIBLE_POSIX_VERSION:-2.1.0}"

[[ "${venv_path}" == /* && "${venv_path}" != / ]] || {
  echo '::error::node stage venv must be an absolute non-root path' >&2
  exit 2
}
for version in "${pyyaml_version}" "${ansible_core_version}" "${ansible_posix_version}"; do
  [[ "${version}" =~ ^[0-9]+([.][0-9]+){1,3}$ ]] || {
    echo "::error::invalid exact dependency version ${version}" >&2
    exit 2
  }
done

"${python_bin}" -m venv "${venv_path}"
"${venv_path}/bin/python" -m pip install --disable-pip-version-check \
  "pyyaml==${pyyaml_version}" "ansible-core==${ansible_core_version}"
"${venv_path}/bin/ansible-galaxy" collection install "ansible.posix:==${ansible_posix_version}"
printf '%s\n' "${venv_path}/bin" >> "${GITHUB_PATH:?GITHUB_PATH is required}"
