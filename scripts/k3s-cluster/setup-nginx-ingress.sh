#!/bin/bash

setup_k3s_ingress() {
  local ingress_ip="$1"
  local ingress_label="$2"

  if [[ -z "$ingress_ip" ]]; then
    ingress_ip=$(hostname -I | awk '{print $1}')
  fi

  echo "📦 使用 ingress IP: $ingress_ip"

  cat > value.yaml <<EOF
controller:
  ingressClassResource:
    name: nginx
    enabled: true
  ingressClass: nginx
  replicaCount: 1
  service:
    enabled: true
    type: NodePort
    externalIPs:
      - $ingress_ip
EOF

  cat > nginx-cm.yaml <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: nginx-nginx-ingress
  namespace: ingress
  labels:
    app.kubernetes.io/name: ingress-nginx
    app.kubernetes.io/instance: nginx
    app.kubernetes.io/component: controller
    app.kubernetes.io/managed-by: Helm
data:
  proxy-connect-timeout: "10"
  proxy-read-timeout: "10"
  client-header-buffer-size: 64k
  client-body-buffer-size: 64k
  client-max-body-size: 1000m
  proxy-buffers: "8 32k"
  proxy-buffer-size: 32k
EOF

  cat > nginx-svc-patch.yaml <<EOF
spec:
  externalIPs:
    - $ingress_ip
EOF

  echo "🔍 添加 Helm 仓库 ingress-nginx..."
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx || true
  helm repo update

  echo "📁 创建 ingress 命名空间..."
  kubectl create namespace ingress 2>/dev/null || true

  echo "🚀 安装 ingress-nginx..."
  helm upgrade --install nginx ingress-nginx/ingress-nginx \
    --version 4.9.0 \
    --namespace ingress \
    -f value.yaml

  echo "🔧 应用自定义 ConfigMap 和 Service IP Patch..."
  kubectl apply -f nginx-cm.yaml
  kubectl patch svc nginx-ingress-nginx-controller -n ingress --patch-file nginx-svc-patch.yaml

  if [[ -n "$ingress_label" ]]; then
    echo "🏷️ 设置节点标签: $ingress_label"
    kubectl label nodes --selector="kubernetes.io/hostname=$(hostname)" "$ingress_label" --overwrite || true
  fi

  echo "✅ NGINX Ingress Controller 安装完成，IP: $ingress_ip"
}

# 示例调用（你可以传入具体 IP）
setup_k3s_ingress 8.130.10.142

