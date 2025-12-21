#!/bin/bash
set -e

REPO_BASE_URL="https://raw.githubusercontent.com/<your-org-or-username>/<your-repo>/main/scripts"

echo "🚀 [Step 1/5] 安装 K3s 和 Helm..."
curl -fsSL ${REPO_BASE_URL}/install_k3s_and_helm.sh | bash

echo "🚀 [Step 2/5] 部署 Velero..."
curl -fsSL ${REPO_BASE_URL}/deploy_velero.sh | bash

echo "🚀 [Step 3/5] 节点打标签并解除控制面 Taint..."
curl -fsSL ${REPO_BASE_URL}/label_k8s_node.sh | bash

echo "🚀 [Step 4/5] 生成备份配置文件..."
curl -fsSL ${REPO_BASE_URL}/generate_backup_config.sh | bash

echo "🚀 [Step 5/5] 执行恢复（restore）..."
# 支持参数：backup / restore <tag> / list / delete <tag>
curl -fsSL ${REPO_BASE_URL}/run_backup_tool.sh | bash -s restore 202503211725

