#!/bin/bash
# make_k3s_offline_package.sh - v1.0.4
set -e

VERSION="v1.32.4+k3s1"
ARCH_LIST=("amd64")
BASE_DIR="k3s-offline-package"
K3S_URL_BASE="https://github.com/k3s-io/k3s/releases/download/${VERSION}"
CNI_VERSION="v1.3.0"
HELM_VERSION="v3.14.2"
NERDCTL_VERSION="2.0.4"

mkdir -p "${BASE_DIR}/"{bin,images,cni-plugins,addons,registry/docker.io,registry/ghcr.io,install}

safe_copy() {
  local src_url=$1
  local dest_path=$2
  if [[ -f "${dest_path}" ]]; then
    echo "[SKIP] 已存在：${dest_path}"
  else
    echo "[DOWNLOAD] ${src_url} -> ${dest_path}"
    curl -sLo "$dest_path" "$src_url"
  fi
}

export_airgap_images() {
  local arch=$1
  local out="${BASE_DIR}/images/k3s-airgap-images-${arch}.tar"
  local ns="k8s.io"

  nerd() {
    sudo nerdctl --namespace $ns --address /run/k3s/containerd/containerd.sock "$@"
  }

  # ---- 核心镜像列表 ----
  local core_imgs=(
    docker.io/rancher/mirrored-pause:3.6
    docker.io/rancher/mirrored-metrics-server:v0.6.3
    docker.io/rancher/mirrored-coredns-coredns:1.10.1
    docker.io/rancher/mirrored-prometheus-node-exporter:v1.3.1
    docker.io/rancher/mirrored-kube-state-metrics-kube-state-metrics:v2.12.0
  )

  echo "[INFO] 拉取核心镜像…"
  for img in "${core_imgs[@]}"; do
    nerd pull "$img"
  done

  echo "[INFO] 保存离线包 → $out"
  mkdir -p "$(dirname "$out")"
  nerd save -o "$out" "${core_imgs[@]}"

  echo "[OK] 完成：$out 已生成"
}

########################################
# 写 node‑exporter YAML → addons/node-exporter.yaml
########################################
generate_node_exporter_yaml() {
  local ADDON_DIR=${BASE_DIR}/addons
  mkdir -p "$ADDON_DIR"

  cat > "${ADDON_DIR}/node-exporter.yaml" <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: node-exporter
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: node-exporter}
rules:
- apiGroups: [""]
  resources: ["nodes", "nodes/proxy", "services", "endpoints"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: node-exporter}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: node-exporter}
subjects:
- kind: ServiceAccount
  name: node-exporter
  namespace: kube-system
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-exporter
  namespace: kube-system
spec:
  selector: {matchLabels: {app: node-exporter}}
  template:
    metadata: {labels: {app: node-exporter}}
    spec:
      hostPID: true
      hostNetwork: true
      serviceAccountName: node-exporter
      containers:
      - name: node-exporter
        image: docker.io/rancher/mirrored-prometheus-node-exporter:v1.3.1
        imagePullPolicy: IfNotPresent
        args:
          - "--path.procfs=/host/proc"
          - "--path.sysfs=/host/sys"
          - "--path.rootfs=/host/root"
        securityContext: {privileged: true}
        resources:
          requests: {cpu: "50m", memory: "30Mi"}
        volumeMounts:
        - {name: proc,   mountPath: /host/proc,  readOnly: true}
        - {name: sys,    mountPath: /host/sys,   readOnly: true}
        - {name: rootfs, mountPath: /host/root,  readOnly: true}
      volumes:
      - {name: proc,   hostPath: {path: /proc}}
      - {name: sys,    hostPath: {path: /sys}}
      - {name: rootfs, hostPath: {path: /}}
---
apiVersion: v1
kind: Service
metadata:
  name: node-exporter
  namespace: kube-system
  labels: {app: node-exporter}
spec:
  clusterIP: None
  selector: {app: node-exporter}
  ports:
  - {name: metrics, port: 9100, targetPort: 9100}
EOF
  echo "[OK] 生成 ${ADDON_DIR}/node-exporter.yaml"
}

########################################
# 写 kube‑state‑metrics YAML → addons/kube-state-metrics.yaml
########################################
generate_kube_state_metrics_yaml() {
  local ADDON_DIR=${BASE_DIR}/addons
  mkdir -p "$ADDON_DIR"

  cat > "${ADDON_DIR}/kube-state-metrics.yaml" <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kube-state-metrics
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: kube-state-metrics}
rules:
- apiGroups: [""]
  resources:
    ["pods","nodes","namespaces","services","endpoints",
     "persistentvolumes","persistentvolumeclaims",
     "configmaps","secrets","limitranges","replicationcontrollers"]
  verbs: ["get","list","watch"]
- apiGroups: ["apps"]
  resources: ["statefulsets","daemonsets","deployments","replicasets"]
  verbs: ["get","list","watch"]
- apiGroups: ["batch"]
  resources: ["cronjobs","jobs"]
  verbs: ["get","list","watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: kube-state-metrics}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: kube-state-metrics}
subjects:
- kind: ServiceAccount
  name: kube-state-metrics
  namespace: kube-system
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kube-state-metrics
  namespace: kube-system
spec:
  replicas: 1
  selector: {matchLabels: {app: kube-state-metrics}}
  template:
    metadata: {labels: {app: kube-state-metrics}}
    spec:
      serviceAccountName: kube-state-metrics
      containers:
      - name: kube-state-metrics
        image: docker.io/rancher/mirrored-kube-state-metrics-kube-state-metrics:v2.12.0
        imagePullPolicy: IfNotPresent
        ports:
        - {name: metrics,    containerPort: 8080}
        - {name: telemetry,  containerPort: 8081}
        resources:
          requests: {cpu: "40m", memory: "60Mi"}
---
apiVersion: v1
kind: Service
metadata:
  name: kube-state-metrics
  namespace: kube-system
  labels: {app: kube-state-metrics}
spec:
  selector: {app: kube-state-metrics}
  ports:
  - {name: metrics,   port: 8080, targetPort: 8080}
  - {name: telemetry, port: 8081, targetPort: 8081}
EOF
  echo "[OK] 生成 ${ADDON_DIR}/kube-state-metrics.yaml"
}

for ARCH in "${ARCH_LIST[@]}"; do
  echo -e "\n[INFO] 准备架构：${ARCH}"

  safe_copy "${K3S_URL_BASE}/k3s" "${BASE_DIR}/bin/k3s-${ARCH}"
  chmod +x "${BASE_DIR}/bin/k3s-${ARCH}"

  safe_copy "https://dl.k8s.io/release/v1.29.1/bin/linux/${ARCH}/kubectl" "${BASE_DIR}/bin/kubectl-${ARCH}"
  chmod +x "${BASE_DIR}/bin/kubectl-${ARCH}"

  TMP_HELM="/tmp/helm-${ARCH}.tgz"
  safe_copy "https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz" "$TMP_HELM"
  tar -xzf "$TMP_HELM" -C /tmp
  mv "/tmp/linux-${ARCH}/helm" "${BASE_DIR}/bin/helm-${ARCH}"
  chmod +x "${BASE_DIR}/bin/helm-${ARCH}"

  safe_copy "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" \
    "/tmp/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz"
  tar -xzf "/tmp/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" -C /tmp
  cp "/tmp/nerdctl" "${BASE_DIR}/bin/nerdctl-${ARCH}"
  chmod +x "${BASE_DIR}/bin/nerdctl-${ARCH}"

  safe_copy "https://github.com/containernetworking/plugins/releases/download/${CNI_VERSION}/cni-plugins-linux-${ARCH}-${CNI_VERSION}.tgz" \
    "${BASE_DIR}/cni-plugins/cni-plugins-linux-${ARCH}-${CNI_VERSION}.tgz"

  export_airgap_images "$ARCH"

  generate_node_exporter_yaml
  generate_kube_state_metrics_yaml
done

safe_copy "https://get.k3s.io" "${BASE_DIR}/install/k3s-official-install.sh"
chmod +x "${BASE_DIR}/install/k3s-official-install.sh"

# 生成 install-server.sh
cat > "${BASE_DIR}/install-server.sh" <<'EOF'
#!/bin/bash
set -e

ARCH=$(uname -m)
case "$ARCH" in
  x86_64 | amd64)  ARCH="amd64"  ;;   # Intel/AMD 64 位
  aarch64 | arm64) ARCH="arm64"  ;;   # ARM 64 位
  *)
    echo "[ERROR] 不支持的架构：$ARCH"
    exit 1
    ;;
esac

# 路径定义
BIN_DIR="./bin"
K3S_BIN="${BIN_DIR}/k3s-${ARCH}"
HELM_BIN="${BIN_DIR}/helm-${ARCH}"
KUBECTL_BIN="${BIN_DIR}/kubectl-${ARCH}"
NERDCTL_BIN="${BIN_DIR}/nerdctl-${ARCH}"

echo "[INFO] 安装 CLI 工具（${ARCH}）到 /usr/local/bin"

install_bin() {
  local src=$1
  local dst=$2
  echo " ↳ $dst"
  sudo cp "$src" "$dst"
  sudo chmod +x "$dst"
}

install_bin "$K3S_BIN" /usr/local/bin/k3s
install_bin "$HELM_BIN" /usr/local/bin/helm
install_bin "$KUBECTL_BIN" /usr/local/bin/kubectl
install_bin "$NERDCTL_BIN" /usr/local/bin/nerdctl

echo "[INFO] 执行官方离线安装脚本"
INSTALL_K3S_SKIP_DOWNLOAD=true \
INSTALL_K3S_EXEC="server \
  --write-kubeconfig-mode 644 \
  --disable=traefik,servicelb,local-storage \
  --kube-apiserver-arg=service-node-port-range=0-50000" \
bash "install/k3s-official-install.sh"

echo "[INFO] 准备 airgap 镜像"
sudo nerdctl             \
--namespace k8s.io  \
--address /run/k3s/containerd/containerd.sock load -i images/k3s-airgap-images-amd64.tar

echo "[INFO] 等待 K3s 启动..."
sleep 5

echo "[INFO] 应用默认组件（如存在）"
mkdir -pv ~/.kube/
cp -v /etc/rancher/k3s/k3s.yaml ~/.kube/config
kubectl apply -f addons/node-exporter.yaml || true
kubectl apply -f addons/kube-state-metrics.yaml || true

echo "[SUCCESS] 离线 K3s 安装完成 ✅"
EOF

chmod +x "${BASE_DIR}/install-server.sh"

# 生成 install-agent.sh
cat > "${BASE_DIR}/install-agent.sh" <<'EOF'
#!/bin/bash
set -e

ARCH=$(uname -m)
case "$ARCH" in
  x86_64 | amd64)  ARCH="amd64"  ;;
  aarch64 | arm64) ARCH="arm64"  ;;
  *)
    echo "[ERROR] 不支持的架构：$ARCH"
    exit 1
    ;;
esac

if [[ -z "$K3S_TOKEN" || -z "$K3S_URL" ]]; then
  echo "[ERROR] 你必须设置环境变量 K3S_TOKEN 和 K3S_URL"
  echo "例如："
  echo "  export K3S_TOKEN=K10xxxxxxxx"
  echo "  export K3S_URL=https://<server-ip>:6443"
  exit 1
fi

echo "[INFO] 安装 CLI 工具（${ARCH}）到 /usr/local/bin"

# 路径定义
BIN_DIR="./bin"
K3S_BIN="${BIN_DIR}/k3s-${ARCH}"
NERDCTL_BIN="${BIN_DIR}/nerdctl-${ARCH}"


install_bin() {
  local src=$1
  local dst=$2
  echo " ↳ $dst"
  sudo cp "$src" "$dst"
  sudo chmod +x "$dst"
}

echo "[INFO] 安装 CLI 工具（${ARCH}）到 /usr/local/bin"

install_bin "$K3S_BIN" /usr/local/bin/k3s
install_bin "$NERDCTL_BIN" /usr/local/bin/nerdctl

sudo chmod +x /usr/local/bin/k3s
sudo chmod +x /usr/local/bin/neddctl

echo "[INFO] 执行官方 agent 安装脚本（使用离线模式）"
INSTALL_K3S_SKIP_DOWNLOAD=true \
INSTALL_K3S_EXEC="agent" \
bash install/k3s-official-install.sh

echo "[INFO] 准备 airgap 镜像"
sudo nerdctl             \
--namespace k8s.io  \
--address /run/k3s/containerd/containerd.sock load -i images/k3s-airgap-images-${ARCH}.tar

echo "[SUCCESS] Agent 节点已完成离线安装 ✅"

EOF

chmod +x "${BASE_DIR}/install-agent.sh"
echo "[OK] 已生成 install-agent.sh ✅"

cat > "${BASE_DIR}/README.md" <<EOF
# K3s 离线安装包（v${VERSION}，支持 amd64 / arm64）

## 📦 包含内容

- ✅ **K3s**（v${VERSION}）
- ✅ **kubectl / helm CLI**
- ✅ **cni-plugins** v${CNI_VERSION}
- ✅ **nerdctl** v${NERDCTL_VERSION} CLI（可连接 K3s 内置 containerd）
- ✅ **airgap 镜像包** \`images/k3s-airgap-images-\${ARCH}.tar\`

k3s-offline-package 包含：

  - pause:3.6
  - coredns:1.10.1
  - metrics-server:v0.6.3
  - node-exporter:v1.3.1
  - kube-state-metrics:v2.12.0
  - 其他 rancher/k3s 默认依赖组件
- ✅ **默认组件 YAML**
  - \`addons/metrics-server.yaml\`
  - \`addons/node-exporter.yaml\`
  - \`addons/kube-state-metrics.yaml\`
- ✅ **install-server.sh 安装脚本**
  - 调用官方 install.sh，自动加载 airgap 镜像
  - 支持设置 \`INSTALL_K3S_EXEC\` 追加参数
- ✅ **install-agent.sh  Agent 安装脚本**
  - 调用官方 install.sh，自动加载 airgap 镜像
  - 支持设置 \`INSTALL_K3S_EXEC,K3S_URL,K3S_TOKEN\` 追加参数

---

## 🚀 使用方法

### 1. 上传目录到离线节点（如 /opt/k3s-offline-package）

\`\`\`bash
scp -r k3s-offline-package/ user@remote:/opt/
\`\`\`

### 2. 安装执行

\`\`\`bash
cd /opt/k3s-offline-package
bash ./install-server.sh
\`\`\`

\`\`\`bash
cd /opt/k3s-offline-package
export K3S_URL=https://<server-ip>:6443
export K3S_TOKEN=K10xxxxxxxx
bash ./install-agent.sh
\`\`\`

### 3. 验证安装状态

\`\`\`bash
kubectl get nodes
kubectl get pods -A
\`\`\`

---

## 🛠️ 使用 nerdctl 操作 K3s 内部 containerd

\`\`\`bash
./bin/nerdctl-\$(uname -m) \\
  --namespace k8s.io \\
  --address /run/k3s/containerd/containerd.sock \\
  images
\`\`\`

---

## 📂 目录结构示例

\`\`\`
${BASE_DIR}/
├── bin/
│   ├── k3s-(amd64/arm64)
│   ├── helm-(amd64/arm64)
│   ├── kubectl-(amd64/arm64)
│   └── nerdctl-(amd64/arm64)
├── images/
│   └── k3s-airgap-images-amd64.tar
├── addons/
│   ├── metrics-server.yaml
│   ├── node-exporter.yaml
│   └── kube-state-metrics.yaml
├── install-agent.sh
├── install-server.sh
├── README.md
\`\`\`

---
EOF

echo -e "\n✅ [DONE] 离线安装包构建完成：${BASE_DIR}/"
tree "${BASE_DIR}" || ls -R "${BASE_DIR}"
