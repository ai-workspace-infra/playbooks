#!/bin/bash

export namespace=$1
export registry=$2

cat > values.yaml << EOF
global:
  imageRegistry: "$registry"
EOF

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo up bitnami
kubectl create ns $namespace || true
helm upgrade --install redis bitnami/redis --set architecture=standalone -n $namespace -f values.yaml
