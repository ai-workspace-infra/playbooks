#!/bin/bash
set -x
export domain=$1
export secret=$2
export namespace=$3

cat > values.yaml << EOF
chaosDaemon:
  runtime: containerd
  socketPath: /run/k3s/containerd/containerd.sock
dashboard:
  create: true
  ingress:
    enabled: true
    ingressClassName: "nginx"
    hosts:
      - name: chaos-mesh.$domain
        tls: true
        tlsSecret: $secret
EOF

helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n $namespace --create-namespace  --version 2.6.3 -f values.yaml
