#!/bin/bash

# Set the base directory name
ROLE_NAME="alloy"

# Create the directory structure
mkdir -p ${ROLE_NAME}/{defaults,tasks,templates,files}

# Create the main.yml file
cat > ${ROLE_NAME}/defaults/main.yml << EOF
# Default variables for ${ROLE_NAME}
loki_journal_sources:
  - name: "xray"
    unit: "xray.service"
  - name: "xray_tproxy"
    unit: "xray-tproxy.service"

loki_endpoint_url: "https://logs-prod-030.grafana.net/loki/api/v1/push"
loki_basic_auth_username: "{{ loki_username }}"
loki_basic_auth_password: "{{ loki_password }}"
EOF

# Create tasks/main.yml file
cat > ${ROLE_NAME}/tasks/main.yml << EOF
---
- name: Install GPG
  apt:
    name: gpg
    state: present

- name: Create APT keyrings directory
  file:
    path: /etc/apt/keyrings/
    state: directory
    mode: '0755'

- name: Add Grafana GPG key
  ansible.builtin.get_url:
    url: "{{ grafana_gpg_key_url }}"
    dest: /etc/apt/keyrings/grafana.gpg
    mode: '0644'

- name: Add Grafana Alloy APT source
  apt_repository:
    repo: "{{ grafana_apt_source }}"
    state: present

- name: Update APT package list and install Grafana Alloy
  apt:
    name: alloy
    state: present
    update_cache: yes

- name: Create Alloy configuration directory
  file:
    path: /etc/alloy
    state: directory
    mode: '0755'

- name: Create Alloy configuration file
  template:
    src: config.alloy.j2
    dest: "{{ alloy_config_path }}"
    mode: '0644'

- name: Reload and restart Alloy service
  systemd:
    name: alloy
    state: restarted
    daemon_reload: yes
EOF

# Create templates/config.alloy.j2 file
cat > ${ROLE_NAME}/templates/config.alloy.j2 << EOF
loki.write "grafanacloud" {
  endpoint {
    url = "{{ loki_endpoint_url }}"

    basic_auth {
      username = "{{ loki_basic_auth_username }}"
      password = "{{ loki_basic_auth_password }}"
    }
  }
}

{% for source in loki_journal_sources %}
loki.source.journal "{{ source.name }}"  {
  format_as_json  = true
  labels          = {job = "{{ source.name }}"}
  matches         = "_SYSTEMD_UNIT={{ source.unit }}"
  forward_to      = [loki.write.grafanacloud.receiver]
}
{% endfor %}
EOF

# Create files/grafana.gpg file (an empty file is created here; you can manually add the content)
touch ${ROLE_NAME}/files/grafana.gpg

echo "Ansible Role directory structure for '${ROLE_NAME}' has been initialized."
