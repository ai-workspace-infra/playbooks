#!/bin/bash

export namespace=$1
export registry=$2

helm repo add bitnami https://charts.bitnami.com/bitnami || echo true
helm repo up
cat > values.yaml << EOF
global:
  imageRegistry: "$registry"
EOF
kubectl create ns $namespace || echo true
helm upgrade --install postgresql bitnami/postgresql --version 12.8.2 -n $namespace -f values.yaml
