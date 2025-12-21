#!/bin/bash

# 确保你有管理员权限
if [ "$(id -u)" -ne 0 ]; then
  echo "请使用管理员权限运行此脚本"
  exit 1
fi

NAMESPACE="cilium-secrets"

# Step 1: 强制删除 Pod、Deployment、StatefulSet 和 DaemonSet
echo "正在强制删除 $NAMESPACE 命名空间中的资源..."
kubectl delete pod --all --force --grace-period=0 -n $NAMESPACE
kubectl delete deployment --all --force --grace-period=0 -n $NAMESPACE
kubectl delete statefulset --all --force --grace-period=0 -n $NAMESPACE
kubectl delete daemonset --all --force --grace-period=0 -n $NAMESPACE

# Step 2: 删除命名空间（如果它无法删除）
echo "尝试强制删除命名空间 $NAMESPACE..."
kubectl get namespace $NAMESPACE -o json | jq '.spec.finalizers = []' > tmp.json
kubectl replace --raw "/api/v1/namespaces/$NAMESPACE/finalize" -f tmp.json

# Step 3: 确认资源删除
echo "正在确认命名空间和资源是否已删除..."
kubectl get ns
kubectl get all -n $NAMESPACE

# Step 4: 删除 Helm Release 如果存在
echo "如果 Helm Release 存在，尝试删除..."
helm delete $NAMESPACE --namespace $NAMESPACE || echo "Helm release $NAMESPACE 未找到或已删除"

sudo ip link delete cilium_net
sudo ip link delete cilium_host
sudo ip link delete cilium_vxlan

echo "清理完毕！"

