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
    #curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=$version sh -s - $default --system-default-registry "registry.cn-hangzhou.aliyuncs.com"
    curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_VERSION=$version  INSTALL_K3S_MIRROR=cn sh -s - $default --system-default-registry "registry.cn-hangzhou.aliyuncs.com"
  else
    echo "当前主机在大陆网络上"
    curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_VERSION=$version  INSTALL_K3S_MIRROR=cn sh -s - $default --system-default-registry "registry.cn-hangzhou.aliyuncs.com"
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
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    #sudo rm -rf helm.tar.gz* /usr/local/bin/helm || echo true
    #sudo wget --no-check-certificate https://mirrors.onwalk.net/tools/linux-${ARCH}/helm.tar.gz && sudo tar -xvpf helm.tar.gz -C /usr/local/bin/
    #sudo chmod 755 /usr/local/bin/helm
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

  cat > nginx-svc-patch.yaml << EOF
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

function setup_k3s_gitops() {
   cat > fluxcd-values.yaml << EOF
cli:
  image: artifact.onwalk.net/public/fluxcd/flux-cli
  tag: v2.2.0
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
helmController:
  create: true
  image: artifact.onwalk.net/public/fluxcd/helm-controller
  tag: v0.37.0
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
imageAutomationController:
  image: artifact.onwalk.net/public/fluxcd/image-automation-controller
  tag: v0.37.0
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
imageReflectionController:
  image: artifact.onwalk.net/public/fluxcd/image-reflector-controller
  tag: v0.31.1
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
kustomizeController:
  create: true
  image: artifact.onwalk.net/public/fluxcd/kustomize-controller
  tag: v1.2.0
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
notificationController:
  create: false
  image: artifact.onwalk.net/public/fluxcd/notification-controller
  tag: v1.2.2
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
sourceController:
  create: true
  image: artifact.onwalk.net/public/fluxcd/source-controller
  tag: v1.2.2
  resources:
    request:
      cpu: 100m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 100Mi
EOF

  cat > cluster-config.yaml << EOF
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: GitRepository
metadata:
  name: stable
  namespace: gitops-system
spec:
  interval: 1m0s
  ref:
    branch: main
  url: https://github.com/svc-design/gitops.git
---
apiVersion: kustomize.toolkit.fluxcd.io/v1beta2
kind: Kustomization
metadata:
  name: cluster
  namespace: gitops-system
spec:
  interval: 1m0s
  sourceRef:
    kind: GitRepository
    name: stable
  path: ./clusters/k3s-local
  prune: true
EOF

  helm repo add stable https://charts.onwalk.net
  helm repo update
  kubectl create namespace gitops-system || true
  helm upgrade --install fluxcd stable/flux2 --version 2.12.1 -n gitops-system -f fluxcd-values.yaml
  kubectl apply -f cluster-config.yaml && rm cluster-config.yaml -f
}

# Main script
setup_k3s
setup_helm
setup_k3s_ingress
