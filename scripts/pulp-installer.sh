#!/bin/bash
set -e

echo "🚀 开始离线安装 Pulp Operator..."

# 安装 nerdctl（如存在）
if [ -f nerdctl.tar.gz ]; then
  echo "📦 解压 nerdctl..."
  tar xzvf nerdctl.tar.gz -C /usr/local/bin/
fi

# 导入镜像
echo "🚀 导入 pulp-operator 镜像..."

IMAGES=(
  "images/pulp-operator.tar"
  "images/kube-rbac-proxy.tar"
)

if command -v docker &>/dev/null && docker info &>/dev/null; then
  for img in "${IMAGES[@]}"; do
    docker load -i "$img"
  done
elif [ -S /run/k3s/containerd/containerd.sock ]; then
  export CONTAINERD_ADDRESS=/run/k3s/containerd/containerd.sock
  for img in "${IMAGES[@]}"; do
    nerdctl --namespace k8s.io load -i "$img"
  done
elif [ -S /run/containerd/containerd.sock ]; then
  export CONTAINERD_ADDRESS=/run/containerd/containerd.sock
  for img in "${IMAGES[@]}"; do
    nerdctl --namespace k8s.io load -i "$img"
  done
else
  echo "❌ 没有可用的容器运行时"
  exit 1
fi

# 创建命名空间
kubectl create namespace pulp || true

# 安装 chart
echo "📦 安装本地 Helm Chart..."
helm upgrade --install pulp-operator ./charts/pulp-operator/ -n pulp

# 等待 CRD 注册
sleep 10

# 生成默认 CR yaml（可改为 values 覆盖渲染）
echo "📝 生成 CR manifests/pulp-cr.yaml..."
mkdir -p manifests
cat > manifests/pulp-cr.yaml <<EOF
apiVersion: repo-manager.pulpproject.org/v1beta2
kind: Pulp
metadata:
  name: pulp
  namespace: pulp
spec:
  deployment_type: pulp
  image_version: stable
  image_web_version: 3.63.4
  inhibit_version_constraint: true

  ingress_type: ingress
  ingress_host: artifacts.svc.plus
  ingress_class_name: nginx
  is_nginx_ingress: true

  api:
    replicas: 1
  content:
    replicas: 1
  worker:
    replicas: 1
  web:
    replicas: 1

  migration_job:
    container:
      resource_requirements:
        requests:
          cpu: 250m
        limits:
          cpu: 500m

  database:
    postgres_storage_class: standard

  file_storage_access_mode: "ReadWriteOnce"
  file_storage_size: "2Gi"
  file_storage_storage_class: standard

  cache:
    enabled: true
    redis_storage_class: standard

  pulp_settings:
    api_root: "/pulp/"
    allowed_export_paths:
      - /tmp
    allowed_import_paths:
      - /tmp
    telemetry: false
    token_server: https://artifacts.svc.plus/token/
    content_origin: https://artifacts.svc.plus
    ansible_api_hostname: https://artifacts.svc.plus
    installed_plugins:
      - pulp_container
      - pulp_rpm
      - pulp_deb
      - pulp_helm
      - pulp_file
      - pulp_nuget
EOF

# 应用 CR
echo "✅ 应用 Pulp CR"
kubectl apply -f manifests/pulp-cr.yaml

echo "🎉 Pulp 安装完成，查看状态：kubectl -n pulp get pods"
