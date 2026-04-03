#!/bin/bash

set -euo pipefail

readonly PROTECTED_DIRS=(
  "/opt/cloud-neutral"
)

is_protected_path() {
  local path="$1"
  local protected
  for protected in "${PROTECTED_DIRS[@]}"; do
    if [[ "$path" == "$protected" || "$path" == "$protected"/* ]]; then
      return 0
    fi
  done
  return 1
}

safe_rm_rf() {
  local path
  for path in "$@"; do
    [[ -z "$path" ]] && continue
    if is_protected_path "$path"; then
      echo "skip protected path: $path"
      continue
    fi
    rm -rf "$path"
  done
}

wget -q -O cleanup.sh https://raw.githubusercontent.com/kubeovn/kube-ovn/release-1.10/dist/images/cleanup.sh
bash cleanup.sh

systemctl stop k3s 2>/dev/null || true
systemctl stop k3s-agent 2>/dev/null || true
systemctl disable k3s 2>/dev/null || true
systemctl disable k3s-agent 2>/dev/null || true
pkill -9 -f '/usr/local/bin/k3s' 2>/dev/null || true
pkill -9 -f 'k3s server' 2>/dev/null || true
pkill -9 -f 'k3s agent' 2>/dev/null || true

safe_rm_rf /var/run/openvswitch
safe_rm_rf /var/run/ovn
safe_rm_rf /etc/origin/openvswitch/
safe_rm_rf /etc/origin/ovn/
safe_rm_rf /etc/cni/net.d/00-kube-ovn.conflist
safe_rm_rf /etc/cni/net.d/01-kube-ovn.conflist
safe_rm_rf /var/log/openvswitch
safe_rm_rf /var/log/ovn
safe_rm_rf /var/log/kube-ovn

/usr/local/bin/k3s-uninstall.sh || true
safe_rm_rf /opt/rancher/ /etc/rancher/ /var/lib/rancher/ "$HOME/.kube"
safe_rm_rf /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env
safe_rm_rf /etc/systemd/system/k3s-agent.service /etc/systemd/system/k3s-agent.service.env
safe_rm_rf /usr/local/bin/k3s-uninstall.sh /usr/local/bin/k3s-agent-uninstall.sh

safe_rm_rf /etc/cni/net.d/*

# 移除cni命名空间
ip netns show 2>/dev/null | grep cni- | xargs -r -t -n 1 ip netns delete
# 移除cnio网卡
ip link show 2>/dev/null | grep 'master cni0' | while read ignore iface ignore; do
    iface=${iface%%@*}
    [ -z "$iface" ] || ip link delete $iface
done
ip link delete cni0 2>/dev/null || true
ip link delete flannel.1 2>/dev/null || true
safe_rm_rf /var/lib/cni/
# 清理iptables
iptables-save | grep -v KUBE- | grep -v CNI- | iptables-restore
systemctl daemon-reload
