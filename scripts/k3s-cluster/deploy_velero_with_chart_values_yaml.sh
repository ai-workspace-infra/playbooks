#!/bin/bash
set -e

# ======= 配置项 =======
VELERO_NAMESPACE="velero"
VELERO_RELEASE_NAME="velero"
VELERO_BUCKET="k8s-resources-backup"
VELERO_REGION="ap-northeast-1"
VELERO_PROVIDER="aws"
VELERO_SNAPSHOT_LOCATION="default"

AWS_ACCESS_KEY_ID=""
AWS_SECRET_ACCESS_KEY=""

CREDENTIALS_FILE="/tmp/credentials-velero"
CHART_REPO_URL="https://github.com/vmware-tanzu/helm-charts.git"
CHART_PATH="./helm-charts/charts/velero"
PROVIDER_PLUGIN_TAG="v1.7.0"
VALUES_FILE="/tmp/velero-values.yaml"

# ======= 创建临时凭证文件 =======
echo "📝 生成临时凭证文件：$CREDENTIALS_FILE"
cat <<EOF > "$CREDENTIALS_FILE"
[default]
aws_access_key_id=$AWS_ACCESS_KEY_ID
aws_secret_access_key=$AWS_SECRET_ACCESS_KEY
EOF

# ======= 克隆 Helm Chart 仓库（如不存在）=======
if [ ! -d "./helm-charts" ]; then
  echo "📦 克隆 VMware Tanzu Helm Charts..."
  git clone "$CHART_REPO_URL"
else
  echo "✅ Helm Charts 已存在，跳过克隆。"
fi

# ======= 生成 values.yaml 文件 =======
echo "📄 生成 Helm values 文件：$VALUES_FILE"
cat <<EOF > "$VALUES_FILE"
kubectl:
  image:
    repository: images.onwalk.net/public/bitnami/kubectl 
    tag: 1.31
    pullPolicy: IfNotPresent
image:
  repository: images.onwalk.net/public/velero/velero
  tag: v1.15.2
  pullPolicy: IfNotPresent
credentials:
  secretContents:
    cloud: |
      [default]
      aws_access_key_id=$AWS_ACCESS_KEY_ID
      aws_secret_access_key=$AWS_SECRET_ACCESS_KEY

configuration:
  backupStorageLocation:
    - name: default
      provider: ${VELERO_PROVIDER}
      bucket: ${VELERO_BUCKET}
      config:
        region: ${VELERO_REGION}

  volumeSnapshotLocation:
    - name: ${VELERO_SNAPSHOT_LOCATION}
      provider: ${VELERO_PROVIDER}
      config:
        region: ${VELERO_REGION}

initContainers:
  - name: velero-plugin-for-${VELERO_PROVIDER}
    image: images.onwalk.net/public/velero/velero-plugin-for-${VELERO_PROVIDER}:${PROVIDER_PLUGIN_TAG}
    volumeMounts:
      - mountPath: /target
        name: plugins
EOF

# ======= 安装 Velero =======
echo "🚀 使用 Helm 安装 Velero..."
helm upgrade --install "$VELERO_RELEASE_NAME" "$CHART_PATH" \
  --namespace "$VELERO_NAMESPACE" \
  --create-namespace \
  -f "$VALUES_FILE"

echo "✅ Velero 安装完成！"
