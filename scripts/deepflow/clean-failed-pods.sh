!/bin/bash
# 脚本名称: clean-failed-pods.sh
# 作用: 删除指定命名空间中非 Running 状态的 Pod

# 定义需要处理的命名空间
NAMESPACES=("deepflow" "openebs" "kube-system")

# 遍历命名空间
for NAMESPACE in "${NAMESPACES[@]}"; do
  echo "正在删除 $NAMESPACE 命名空间中非 Running 状态的 Pod..."
  kubectl get pods -n $NAMESPACE | grep -v Running | awk 'NR>1 {print $1}' | xargs kubectl delete pod -n $NAMESPACE --force
  kubectl delete jobs --all -n $NAMESPACE
  echo "$NAMESPACE 命名空间清理完成！"
done
