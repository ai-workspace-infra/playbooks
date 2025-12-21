#!/bin/bash

ROLE_NAME="k3s-cluster-agent"
BASE_DIR="roles/$ROLE_NAME"

echo "Creating role structure for $ROLE_NAME..."

# Create directories
mkdir -p $BASE_DIR/{tasks,templates,vars,defaults}

# Create main tasks file
cat > $BASE_DIR/tasks/main.yml <<EOL
- name: Execute action on K3s cluster agent
  include_tasks: "{{ action }}.yml"
EOL

# Create individual task files
touch $BASE_DIR/tasks/{bootstrap.yml,destroy.yml,upgrade.yml}

# Create vars file
cat > $BASE_DIR/vars/main.yml <<EOL
action: 'bootstrap'
agent:
  node_ip: '10.254.0.1'
  server_token: 'your_server_token'
  extra_vars: '--node-label deployment=true --node-external-ip 110.42.238.110 --node-ip {{ agent.node_ip }} --flannel-iface wg0'
EOL

# Create templates file
cat > $BASE_DIR/templates/install_k3s_agent.sh.j2 <<EOL
#!/bin/bash

curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | \
  INSTALL_K3S_MIRROR=cn \
  K3S_URL=https://{{ agent.k3s_url }}:6443 \
  K3S_TOKEN={{ agent.server_token }} \
  INSTALL_K3S_EXEC="{{ agent.extra_vars }}" sh -
EOL

# Create defaults file
cat > $BASE_DIR/defaults/main.yml <<EOL
# Default values for $ROLE_NAME role
EOL

echo "Role structure for $ROLE_NAME created successfully."

