#!/bin/sh

function get_local_ip() {
    local_ip=$(hostname -I | awk '{print $1}')
    echo "$local_ip"
}

function setup_k3s() {
  local disable_proxy="--disable-kube-proxy"
  local disable_cni="--flannel-backend=none --disable-network-policy"
  local default="--disable=traefik,servicelb --data-dir=/opt/rancher/k3s --kube-apiserver-arg service-node-port-range=0-50000"

  sudo mkdir -pv /opt/rancher/k3s

  ping -c 1 google.com > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "当前主机在国际网络上"
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=$version sh -s - $default
  else
    echo "当前主机在大陆网络上"
    curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_VERSION=$version  INSTALL_K3S_MIRROR=cn sh -s - $default
  fi
  mkdir -pv ~/.kube/ && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
}

function setup_helm()
{
  ping -c 1 google.com > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "当前主机在国际网络上"
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  else
    echo "当前主机在大陆网络上"
    case `uname -m` in
  	x86_64) ARCH=amd64; ;;
          aarch64) ARCH=arm64; ;;
          loongarch64) ARCH=loongarch64; ;;
          *) echo "un-supported arch, exit ..."; exit 1; ;;
    esac
    sudo rm -rf helm.tar.gz* /usr/local/bin/helm || echo true
    sudo wget --no-check-certificate https://mirrors.onwalk.net/tools/linux-${ARCH}/helm.tar.gz && sudo tar -xvpf helm.tar.gz -C /usr/local/bin/
    sudo chmod 755 /usr/local/bin/helm
  fi
}

function setup_k3s_ingress() {
  local ingress_ip=$(get_local_ip)

  cat > value.yaml <<EOF
controller:
  nginxplus: false
  ingressClass: nginx
  replicaCount: 2
  service:
    enabled: true
    type: NodePort
    externalIPs:
      - $ingress_ip
EOF

  cat > nginx-cm.yaml << EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: nginx-nginx-ingress
  namespace: ingress
data:
  use-ssl-certificate-for-ingress: "false"
  external-status-address: $ingress_ip
  proxy-connect-timeout: 10s
  proxy-read-timeout: 10s
  client-header-buffer-size: 64k
  client-body-buffer-size: 64k
  client-max-body-size: 1000m
  proxy-buffers: 8 32k
  proxy-body-size: 1024m
  proxy-buffer-size: 32k
  proxy-connect-timeout: 10s
  proxy-read-timeout: 10s
EOF

  helm repo add nginx-stable https://helm.nginx.com/stable || echo true
  helm repo up
  kubectl create namespace ingress || echo true
  helm upgrade --install nginx nginx-stable/nginx-ingress --version=0.15.0 --namespace ingress -f value.yaml
  kubectl apply -f nginx-cm.yaml
  kubectl patch svc nginx-nginx-ingress -n ingress --patch-file nginx-svc-patch.yaml
}

# Main script
setup_k3s
setup_helm
setup_k3s_ingress
