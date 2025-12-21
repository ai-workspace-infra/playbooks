#!/bin/bash
set -e

INGRESS_IP="${1:-$(hostname -I | awk '{print $1}')}"
NODE_LABEL="$2"

echo "🚀 Ingress离线部署开始，IP: ${INGRESS_IP}"

# 解压 nerdctl 并安装
echo "📦 安装nerdctl..."
tar xzvf nerdctl.tar.gz -C /usr/local/bin/

echo "🚀 尝试导入镜像..."

if command -v docker &>/dev/null && docker info &>/dev/null; then
  echo "✅ 检测到 Docker 正常运行，使用 docker load 导入镜像"
  docker load -i images/nginx-ingress.tar
  docker load -i images/kube-webhook-certgen.tar

elif [ -S /run/k3s/containerd/containerd.sock ]; then
  echo "⚠️ Docker 不可用，检测到 K3s 的 containerd socket，使用 nerdctl 导入"

  # 设置 nerdctl 环境变量，连接到 K3s 的 containerd
  export CONTAINERD_ADDRESS=/run/k3s/containerd/containerd.sock

  # 确保 nerdctl 可执行
  if ! command -v nerdctl &>/dev/null; then
    echo "❌ nerdctl 未安装或未在 PATH 中，请检查"
    exit 1
  fi

  nerdctl --namespace k8s.io load -i images/nginx-ingress.tar
  nerdctl --namespace k8s.io load -i images/kube-webhook-certgen.tar

elif [ -S /run/containerd/containerd.sock ]; then
  echo "⚠️ Docker 和 K3s containerd 都不可用，退而使用默认 containerd socket"

  export CONTAINERD_ADDRESS=/run/containerd/containerd.sock

  if ! command -v nerdctl &>/dev/null; then
    echo "❌ nerdctl 未安装或未在 PATH 中，请检查"
    exit 1
  fi

  nerdctl --namespace k8s.io load -i images/nginx-ingress.tar
  nerdctl --namespace k8s.io load -i images/kube-webhook-certgen.tar

else
  echo "❌ 没有可用的容器运行时（docker/containerd），无法导入镜像"
  exit 1
fi

# 创建命名空间
kubectl create namespace ingress || true

# 生成 Helm values.yaml
cat > values.yaml <<EOF
controller:
  ingressClass: nginx
  ingressClassResource:
    enabled: true
  replicaCount: 2
  image:
    registry: docker.io
    image: nginx/nginx-ingress
    tag: "2.4.0"
  service:
    enabled: true
    type: NodePort
    externalIPs:
      - $INGRESS_IP
    nodePorts:
      http: 80
      https: 443
EOF

# 节点标签
if [[ -n "$2" ]]; then
cat >> values.yaml <<EOF
  nodeSelector:
    ${NODE_LABEL%%=*}: "${NODE_LABEL#*=}"
EOF
fi

# 安装 Helm Chart（使用本地chart）
helm upgrade --install nginx ./charts/nginx-ingress \
  --namespace ingress -f values.yaml

# 配置 ConfigMap 优化参数
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: nginx-nginx-ingress
  namespace: ingress
data:
  proxy-connect-timeout: "10"
  proxy-read-timeout: "10"
  client-header-buffer-size: 64k
  client-body-buffer-size: 64k
  client-max-body-size: 1000m
  proxy-buffers: "8 32k"
  proxy-buffer-size: 32k
EOF

echo "✅ 离线安装完成，Ingress IP: $INGRESS_IP"
