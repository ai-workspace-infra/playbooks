#!/bin/bash

# 检查参数是否为空
check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

# 检查参数是否为空
check_not_empty "$1" "DOMAIN" && DOMAIN=$1
check_not_empty "$2" "NAMESPACE" && NAMESPACE=$2
check_not_empty "$3" "SECRET_NAME" && SECRET_NAME=$3

cat > vaules.yaml << EOF
server:
  ingress:
    enabled: true
    ingressClassName: "nginx"
    hosts:
      - host: vault.$DOMAIN
        paths:
          - /
    tls:
      - secretName: $SECRET_NAME
        hosts:
          - vault.$DOMAIN
EOF

helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo up
kubectl create ns $NAMESPACE || echo true
helm upgrade --install vault-server hashicorp/vault -n $NAMESPACE --create-namespace -f vaules.yaml
