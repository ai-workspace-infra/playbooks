#!/bin/bash
set -euo pipefail

# === 全局变量 ===
MASTER_IP=$(hostname -I | awk '{print $1}')
USER=${USER:-$(whoami)}
SSH_KEY="${HOME}/.ssh/id_rsa"
K8S_VERSION="labring/kubernetes:v1.25.16"
CILIUM_VERSION="labring/cilium:v1.13.4"
HELM_VERSION="labring/helm:v3.9.4"
NVIDIA_DRIVER_VERSION="nvidia-driver-535"
NVIDIA_PLUGIN_VERSION="v0.17.1"
NERDCTL_VERSION="2.1.2"
PROXY_ADDR="http://127.0.0.1:1081"
USE_PROXY=${USE_PROXY:-false}

# === 选项代理 ===
configure_proxy() {
  if [ "$USE_PROXY" = true ]; then
    export http_proxy=$PROXY_ADDR
    export https_proxy=$PROXY_ADDR
    export HTTP_PROXY=$PROXY_ADDR
    export HTTPS_PROXY=$PROXY_ADDR
    echo "🌐 代理已启用: $PROXY_ADDR"
  else
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
    echo "🌐 代理已关闭"
  fi
}

proxy_curl() {
  if [ "$USE_PROXY" = true ]; then
    curl --proxy "$PROXY_ADDR" "$@"
  else
    curl "$@"
  fi
}

install_base() {
  echo "[1/8] 安装基础依赖"
  sudo apt-get update -y
  sudo apt-get install -y curl gnupg2 ca-certificates lsb-release \
    apt-transport-https software-properties-common openssh-client \
    openssh-server uidmap
}

install_containerd() {
  echo "[2/8] 安装 containerd + nerdctl"
  sudo apt-get purge -y docker.io docker-ce docker-ce-cli containerd.io || true
  sudo apt-get install -y containerd

  tmpdir=$(mktemp -d)
  archive="nerdctl-full-${NERDCTL_VERSION}-linux-amd64.tar.gz"
  url="https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/${archive}"
  echo "🔽 下载 nerdctl: $url"
  proxy_curl -fLo "${tmpdir}/${archive}" "$url"

  echo "📆 解压 nerdctl 到 /usr/local"
  sudo tar -xzf "${tmpdir}/${archive}" -C /usr/local

  sudo mkdir -p /etc/containerd
  sudo containerd config default | sudo tee /etc/containerd/config.toml > /dev/null
  sudo systemctl enable --now containerd
  nerdctl --version && echo "✅ nerdctl 安装成功" || echo "❌ nerdctl 安装失败"
}

install_nvidia() {
  echo "[3/8] 安装 NVIDIA 驱动和容器工具"
  distribution="ubuntu22.04"
  proxy_curl -sL https://nvidia.github.io/nvidia-docker/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  proxy_curl -sL https://nvidia.github.io/nvidia-docker/${distribution}/nvidia-docker.list | \
    sed 's|^deb |deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] |' | \
    sudo tee /etc/apt/sources.list.d/nvidia-docker.list

  sudo apt-get update -y
  sudo apt-get install -y ${NVIDIA_DRIVER_VERSION} nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=containerd --set-as-default
  sudo systemctl restart containerd

  if ! command -v nvidia-smi >/dev/null; then echo "❌ nvidia-smi 未找到"; exit 1; fi
  nvidia-smi || { echo "❌ NVIDIA 驱动有问题"; exit 1; }
}

install_sealos() {
  echo "[4/8] 安装 Sealos"
  if ! command -v sealos &>/dev/null; then
    proxy_curl -sfL https://raw.githubusercontent.com/labring/sealos/main/scripts/install.sh | bash
  fi
}

setup_ssh() {
  echo "[5/8] 配置 SSH 免密"
  [ ! -f "${SSH_KEY}" ] && ssh-keygen -f "${SSH_KEY}" -N ""
  cat "${SSH_KEY}.pub" >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh
  sudo systemctl enable --now ssh || sudo systemctl enable --now sshd
}

deploy_k8s() {
  echo "[6/8] 使用 Sealos 部署 K8s"
  sealos run "${K8S_VERSION}" "${CILIUM_VERSION}" "${HELM_VERSION}" \
    --masters "${MASTER_IP}" --user "${USER}" --pk "${SSH_KEY}" \
    --env '{}' --cmd "kubeadm init --skip-phases=addon/kube-proxy"

  echo "[6.1] 禁用 sealos containerd, 启用系统 containerd"
  sudo systemctl disable --now sealos-containerd || true
  sudo systemctl enable --now containerd
  sleep 3
  sudo systemctl status containerd --no-pager | grep Active
}

deploy_plugin() {
  echo "[7/8] 部署 NVIDIA Device Plugin"
  plugin_url="https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${NVIDIA_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"
  if [ "$USE_PROXY" = true ]; then
    HTTPS_PROXY=$PROXY_ADDR HTTP_PROXY=$PROXY_ADDR \
    kubectl apply -f "$plugin_url"
  else
    kubectl apply -f "$plugin_url"
  fi
  sleep 15
  kubectl -n kube-system get pods | grep nvidia || echo "⚠️ 插件未启动"
  kubectl describe node | grep -A10 Capacity | grep -i nvidia
}

run_test() {
  echo "[8/8] 运行 CUDA vectoradd GPU 测试"
  kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: gpu-pod
spec:
  restartPolicy: Never
  containers:
  - name: cuda-test
    image: nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0
    resources: { limits: { "nvidia.com/gpu": 1 } }
  tolerations:
  - key: nvidia.com/gpu
    operator: Exists
    effect: NoSchedule
EOF
  kubectl wait pod/gpu-pod --for=condition=Succeeded --timeout=120s || true
  kubectl logs gpu-pod || echo "⚠️ 未获取日志"
}

show_help() {
  echo -e "用法: ./gpu-k8s.sh [阶段参数...]\n"
  echo "可用阶段:"
  echo "  --install-base         安装基础依赖"
  echo "  --install-containerd   安装 containerd + nerdctl"
  echo "  --install-nvidia       安装 NVIDIA 驱动和工具"
  echo "  --install-sealos       安装 Sealos"
  echo "  --setup-ssh            配置 SSH 免密"
  echo "  --deploy-k8s           使用 Sealos 部署 Kubernetes"
  echo "  --deploy-plugin        部署 NVIDIA Device Plugin"
  echo "  --run-test             运行 GPU 测试"
  echo "  --all                  全部步骤执行"
  echo -e "\n示例命令\t\t\t\t说明"
  echo "USE_PROXY=true ./gpu-k8s.sh --install-nvidia      # 只安装 NVIDIA 工具包并走代理"
  echo "USE_PROXY=false ./gpu-k8s.sh --all                # 全流程执行但不使用代理"
  echo "./gpu-k8s.sh --install-sealos --deploy-k8s        # 默认关闭代理执行指定阶段"
}

# === 执行 ===
configure_proxy

if [ $# -eq 0 ]; then
  show_help
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --install-base) install_base ;;
    --install-containerd) install_containerd ;;
    --install-nvidia) install_nvidia ;;
    --install-sealos) install_sealos ;;
    --setup-ssh) setup_ssh ;;
    --deploy-k8s) deploy_k8s ;;
    --deploy-plugin) deploy_plugin ;;
    --run-test) run_test ;;
    --all)
      install_base
      install_containerd
      install_nvidia
      install_sealos
      setup_ssh
      deploy_k8s
      deploy_plugin
      run_test
      ;;
    *) show_help; exit 1 ;;
  esac
  echo
done
