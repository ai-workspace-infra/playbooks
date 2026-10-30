#!/bin/bash
set -x
export domain=$1
export secret=$2
export namespace=$3

cat << EOF > values-custom.yaml
clickhouse:
  enabled: true
server:
  enabled: true
deepflow-agent:
  enabled: true
grafana:
  enabled: true
  service:
  ingress:
    enabled: true
    ingressClassName: nginx
    hosts:
      - grafana.onwalk.net
    tls:
      - secretName: obs-tls
        hosts:
          - grafana.onwalk.net
EOF
helm repo add deepflow https://deepflowio.github.io/deepflow
helm repo update deepflow # use `helm repo update` when helm < 3.7.0
helm upgrade --install deepflow -n monitoring deepflow/deepflow --create-namespace --version 6.4.9 -f values-custom.yaml
