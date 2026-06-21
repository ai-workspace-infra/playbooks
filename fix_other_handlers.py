def replace_file(path, old_content, new_content):
    with open(path, 'w') as f:
        f.write(new_content)

# qmd
with open('roles/vhosts/qmd/handlers/main.yml', 'w') as f:
    f.write("""---
- name: Reload QMD
  ansible.builtin.systemd:
    name: "{{ qmd_service_name }}"
    state: restarted
    daemon_reload: true
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
  listen: Restart QMD

- name: Unload QMD on macOS
  ansible.builtin.command: "launchctl unload {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.qmd.plist"
  failed_when: false
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart QMD

- name: Load QMD on macOS
  ansible.builtin.command: "launchctl load -w {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.qmd.plist"
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart QMD
""")

with open('roles/vhosts/qmd/tasks/macos.yml', 'r') as f:
    c = f.read().replace('notify: Restart QMD on macOS', 'notify: Restart QMD')
with open('roles/vhosts/qmd/tasks/macos.yml', 'w') as f:
    f.write(c)


# gateway_openclaw
with open('roles/vhosts/gateway_openclaw/handlers/main.yml', 'w') as f:
    f.write("""---
- name: Reload systemd
  ansible.builtin.systemd:
    daemon_reload: true
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
  listen: Restart openclaw

- name: Restart openclaw gateway
  ansible.builtin.shell: |
    set -eu
    uid="{{ gateway_openclaw_effective_service_uid | default(gateway_openclaw_service_uid, true) }}"
    if [ -z "$uid" ]; then
      uid="$(id -u {{ gateway_openclaw_service_user }})"
    fi
    loginctl enable-linger {{ gateway_openclaw_service_user }} || true
    systemctl start "user@${uid}.service" || true
    runuser -u {{ gateway_openclaw_service_user }} -- env \\
      HOME={{ gateway_openclaw_home | quote }} \\
      XDG_RUNTIME_DIR="/run/user/${uid}" \\
      DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \\
      systemctl --user restart {{ gateway_openclaw_service_name }}.service
  args:
    executable: /bin/bash
  become: true
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
  listen: Restart openclaw

- name: Unload openclaw on macOS
  ansible.builtin.command: "launchctl unload {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.openclaw.plist"
  failed_when: false
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart openclaw

- name: Load openclaw on macOS
  ansible.builtin.command: "launchctl load -w {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.openclaw.plist"
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart openclaw

- name: Reload caddy
  ansible.builtin.systemd:
    name: caddy
    state: reloaded
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
""")

with open('roles/vhosts/gateway_openclaw/tasks/macos.yml', 'r') as f:
    c = f.read().replace('notify: Restart openclaw on macOS', 'notify: Restart openclaw')
with open('roles/vhosts/gateway_openclaw/tasks/macos.yml', 'w') as f:
    f.write(c)

# xworkmate_bridge
with open('roles/vhosts/xworkmate_bridge/handlers/main.yml', 'w') as f:
    f.write("""---
- name: Reload bridge
  ansible.builtin.systemd:
    name: "{{ xworkmate_bridge_service_name }}"
    state: restarted
    daemon_reload: true
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
  listen: Restart bridge

- name: Unload bridge on macOS
  ansible.builtin.command: "launchctl unload {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.bridge.plist"
  failed_when: false
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart bridge

- name: Load bridge on macOS
  ansible.builtin.command: "launchctl load -w {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.bridge.plist"
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart bridge
""")

with open('roles/vhosts/xworkmate_bridge/tasks/macos.yml', 'r') as f:
    c = f.read().replace('notify: Restart bridge on macOS', 'notify: Restart bridge')
with open('roles/vhosts/xworkmate_bridge/tasks/macos.yml', 'w') as f:
    f.write(c)

# litellm
with open('roles/vhosts/litellm/handlers/main.yml', 'w') as f:
    f.write("""---
- name: Reload caddy
  ansible.builtin.systemd:
    name: caddy
    state: reloaded
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'

- name: Reload litellm
  ansible.builtin.systemd:
    name: "{{ litellm_service_name }}"
    state: restarted
    daemon_reload: true
  when:
    - not ansible_check_mode
    - ansible_os_family != 'Darwin'
  listen: Restart litellm

- name: Unload litellm on macOS
  ansible.builtin.command: "launchctl unload {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.litellm.plist"
  failed_when: false
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart litellm

- name: Load litellm on macOS
  ansible.builtin.command: "launchctl load -w {{ ansible_env.HOME }}/Library/LaunchAgents/plus.svc.xworkspace.litellm.plist"
  changed_when: false
  when: ansible_system == 'Darwin'
  listen: Restart litellm
""")

with open('roles/vhosts/litellm/tasks/macos.yml', 'r') as f:
    c = f.read().replace('notify: Restart litellm on macOS', 'notify: Restart litellm')
with open('roles/vhosts/litellm/tasks/macos.yml', 'w') as f:
    f.write(c)

print("Done")
