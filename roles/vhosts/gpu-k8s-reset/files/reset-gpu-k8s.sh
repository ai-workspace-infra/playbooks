#!/bin/bash
set -e

if command -v sealos >/dev/null 2>&1; then
  sudo sealos reset --force || true
fi

sudo kubeadm reset -f || true
sudo rm -rf ~/.kube /etc/kubernetes /var/lib/etcd /var/lib/kubelet
sudo rm -rf /var/lib/cni /etc/cni/net.d

ip link delete cni0 2>/dev/null || true
ip link delete flannel.1 2>/dev/null || true
ip link delete docker0 2>/dev/null || true
ip link delete kube-ipvs0 2>/dev/null || true

iptables-save | grep -v KUBE- | grep -v CNI- | iptables-restore || true
