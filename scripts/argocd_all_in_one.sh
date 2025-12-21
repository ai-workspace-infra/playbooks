#!/bin/bash

# 添加 Argo CD 的 Helm 仓库
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

# 使用 Helm 部署 Argo CD
helm install argocd argo/argo-cd -n argocd --create-namespace

# 等待 Argo CD 完全启动
echo "Waiting for Argo CD to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=180s

# 创建 Argo CD Application 配置文件
cat <<EOF > argocd-application.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: helmfile-application
  namespace: argocd
spec:
  project: default
  source:
    repoURL: <你的 Git 仓库 URL>
    path: <存放 Helmfile 的路径>
    targetRevision: HEAD
    helm:
      releaseName: helmfile-application
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
EOF

# 替换占位符为实际值
sed -i 's|<你的 Git 仓库 URL>|你的实际 Git 仓库 URL|g' argocd-application.yaml
sed -i 's|<存放 Helmfile 的路径>|你的实际 Helmfile 路径|g' argocd-application.yaml

# 应用 Argo CD Application 配置
kubectl apply -f argocd-application.yaml

echo "Argo CD deployment and configuration complete."
