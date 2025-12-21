#!/bin/bash

set -e

POD_NAME=${1:-test-pod}
NAMESPACE=${2:-default}

echo "🔍 获取 Pod IP..."
POD_IP=$(kubectl get pod "$POD_NAME" -n "$NAMESPACE" -o jsonpath='{.status.podIP}')
NODE_NAME=$(kubectl get pod "$POD_NAME" -n "$NAMESPACE" -o jsonpath='{.spec.nodeName}')
echo "✅ Pod IP: $POD_IP"
echo "✅ Node: $NODE_NAME"

echo -e "\n🧠 查询 Cilium egress gateway BPF policy 命中情况..."
kubectl -n kube-system exec ds/cilium -- cilium-dbg bpf egress list | grep "$POD_IP" || echo "❌ 没有命中 egress policy"

echo -e "\n🌐 在节点上检查 SNAT 规则 (iptables POSTROUTING)..."
ssh "$NODE_NAME" "sudo iptables -t nat -L POSTROUTING -n -v --line-numbers | grep -E '10\.42|SNAT|wg0|eth0'"

echo -e "\n🌍 从 Pod 内 curl ifconfig.me 获取出口 IP..."
kubectl exec -n "$NAMESPACE" "$POD_NAME" -- curl -s --max-time 5 ifconfig.me || echo "❌ curl 出口失败"

echo -e "\n🚦 路由确认：从 Pod 查看 route 表..."
kubectl exec -n "$NAMESPACE" "$POD_NAME" -- ip route

echo -e "\n🎯 检查目标 Gateway IP 是否可达 (ping 网关)..."
GATEWAY_IP="172.30.0.11"
kubectl exec -n "$NAMESPACE" "$POD_NAME" -- ping -c 3 "$GATEWAY_IP" || echo "❌ 无法 ping 通 $GATEWAY_IP"

echo -e "\n✅ 检查完成"

