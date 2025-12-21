#!/bin/bash
set -x

export version=$1
export cni=$2
export pod_cidr=$3
export svc_cidr=$4
export enable_api_access=$5
export advertise-address=$6

function setup_k3s()
{
  local extra_opts=$1
  mkdir -pv /opt/rancher/k3s
  
  ping -c 1 google.com > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "当前主机在国际网络上"
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=$version sh -s - $extra_opts
  else
    echo "当前主机在大陆网络上"
    curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh | INSTALL_K3S_VERSION=$version  INSTALL_K3S_MIRROR=cn sh -s - $extra_opts
  fi
  mkdir -pv ~/.kube/ && cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
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
    rm -rf helm.tar.gz* /usr/local/bin/helm || echo true
    sudo wget --no-check-certificate https://mirrors.onwalk.net/tools/linux-${ARCH}/helm.tar.gz && sudo tar -xvpf helm.tar.gz -C /usr/local/bin/
    sudo chmod 755 /usr/local/bin/helm
  fi
}


function set_apiserver_l4_proxy()
{
  sudo apt update && apt install nginx -y        
cat > /etc/nginx/sites-available/default << EOF

load_module /usr/lib64/nginx/modules/ngx_stream_module.so;

worker_processes 4;
worker_rlimit_nofile 40000;


events {
    worker_connections 8192;
}

stream {
    log_format logs '$remote_addr - - [$time_local] $protocol $status $bytes_sent $bytes_received $session_time "$upstream_addr"';

    access_log /var/log/nginx/access.log logs;

    upstream K3s_api_server {
        least_conn;
        server 127.0.0.1:6443 max_fails=3 fail_timeout=5s;
    }
    server {
        listen 8022;
        server_name k3s-cluster.onwalk.net;
        proxy_pass K3s_api_server;
    }
}
EOF
  sudo systemctl restart nginx
}

###### function set_apiserver_l7_proxy #######
function set_apiserver_l7_proxy()
{
  sudo apt update && apt install nginx -y        
cat > /etc/nginx/sites-available/default << EOF

http {
    upstream api {
        kubernetes.default.svc.cluster.local:6443;
    }   
 
    server {
      listen 6443 ssl;
      ssl_certificate /usr/local/nginx/ssl/apiserver.crt;               # kube-apiserver cert
      ssl_certificate_key /usr/local/nginx/ssl/apiserver.key;           # kube-apiserver key
      ssl_trusted_certificate /usr/local/nginx/ssl/ca.crt;              # ca.pem
        
      location / {
      }

      location /api/ {
        rewrite ^/api(/.*)$ $1 break;
        proxy_pass https://api;
        proxy_ssl_certificate         /etc/nginx/k8s-client-certificate.pem;
        proxy_ssl_certificate_key     /etc/nginx/k8s-client-key.key;
        proxy_ssl_session_reuse on;
      }
    }
}
EOF
  sudo systemctl restart nginx
}

disable_proxy="--disable-kube-proxy"
disable_cni="--flannel-backend=none --disable-network-policy"
default="--disable=traefik,servicelb --data-dir=/opt/rancher/k3s --kube-apiserver-arg service-node-port-range=0-50000"

case $enable_api_access in
  'true')  api_opts="--bind-address=0.0.0.0" ;;
  *) api_opts="" ;;
esac

case $cni in
	'default')  opts="$default $api_opts" ;;
	'kubeovn')  opts="$default $disable_cni $api_opts" ;;
	'cilium')   opts="$default $disable_cni $disable_proxy $api_opts" ;;
        *) echo "error args" ;;
esac

setup_k3s "$opts"
setup_helm
#set_apiserver_l4_proxy
#set_apiserver_l7_proxy
