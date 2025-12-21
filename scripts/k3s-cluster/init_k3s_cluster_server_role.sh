#!/bin/bash

ROLE_NAME="k3s-cluster-server"
BASE_DIR="roles/$ROLE_NAME"

echo "Creating role structure for $ROLE_NAME..."

# Create directories
mkdir -p $BASE_DIR/{tasks,templates,vars,defaults}

# Create main tasks file
cat > $BASE_DIR/tasks/main.yml <<EOL
- name: Execute action on K3s cluster server
  include_tasks: "{{ action }}.yml"
EOL

# Create individual task files
touch $BASE_DIR/tasks/{bootstrap.yml,upgrade.yml,destroy.yml,add-master.yml,backup.yml,recovery.yml}

# Create vars file
cat > $BASE_DIR/vars/main.yml <<EOL
action: 'bootstrap'
cluster:
  name: 'cn-k3s-cluster-1'
  token: 'your_default_token'
  server_disable: "traefik,servicelb"
  datastore_endpoint: "mysql://user:password@tcp(database_url:3306)/k3s"
  registry: "registry.cn-hangzhou.aliyuncs.com"
  data_dir: "/opt/rancher/k3s"
  apiserver_arg: "service-node-port-range=0-50000"
  bind_address: "0.0.0.0"
  tls_san: "cn-k3s-server.svc.plus"
  advertise_address: "8.130.93.47"
  node_ip: "10.254.0.3"
  node_external_ip: "8.130.93.47"
  flannel_iface: "wg0"
  cluster_cidr: "10.42.0.0/16"
  service_cidr: "10.43.0.0/16"
EOL

# Create templates file
cat > $BASE_DIR/templates/install_k3s_server.sh.j2 <<EOL
#!/bin/bash

INSTALL_K3S_SKIP_DOWNLOAD=true bash /usr/local/share/k3s/install.sh -s - --disable={{ cluster.server_disable }} \
  --token='{{ cluster.token }}' \
  --datastore-endpoint='{{ cluster.datastore_endpoint }}' \
  --system-default-registry '{{ cluster.registry }}' \
  --data-dir='{{ cluster.data_dir }}' \
  --kube-apiserver-arg '{{ cluster.apiserver_arg }}' \
  --bind-address='{{ cluster.bind_address }}' \
  --tls-san='{{ cluster.tls_san }}' \
  --advertise-address='{{ cluster.advertise_address }}' \
  --node-ip='{{ cluster.node_ip }}' \
  --node-external-ip '{{ cluster.node_external_ip }}' \
  --flannel-iface '{{ cluster.flannel_iface }}' \
  --cluster-cidr '{{ cluster.cluster_cidr }}' \
  --service-cidr '{{ cluster.service_cidr }}'
EOL

# Create defaults file
cat > $BASE_DIR/defaults/main.yml <<EOL
# Default values for $ROLE_NAME role
EOL

echo "Role structure for $ROLE_NAME created successfully."



