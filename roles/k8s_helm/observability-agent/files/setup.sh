#!/bin/bash
set -euo pipefail
set -x
export domain="${1:-svc.plus}"
export deepflowserverip="${2:-}"
export deepflowk8sclusterid="${3:-}"
export observability_endpoint="${OBSERVABILITY_ENDPOINT:-https://observability.${domain}}"

deepflow_enabled="false"
if [ -n "${deepflowserverip}" ]; then
  deepflow_enabled="true"
fi

cat > values.yaml << EOF
deepflow-agent:
  enabled: ${deepflow_enabled}
  deepflowServerNodeIPS:
    - ${deepflowserverip}
  deepflowK8sClusterID: ${deepflowk8sclusterid}
prometheus:
  enabled: true
  server:
    name: agent
    retention: "30m"
    extraFlags:
    - web.enable-lifecycle
    - enable-feature=expand-external-labels
    remoteWrite:
    - name: remote_prometheus
      url: '${observability_endpoint}/ingest/metrics/api/v1/write'
    persistentVolume:
      enabled: false
  alertmanager:
    enabled: false
  prometheus-pushgateway:
    enabled: false
  rometheus-pushgateway:
    enabled: false
  kube-state-metrics:
    enabled: false
  prometheus-node-exporter:
    enabled: false
promtail:
  enabled: true
  config:
    clients:
      - url: ${observability_endpoint}/ingest/logs/insert/loki/api/v1/push
EOF

node_name=$(kubectl get nodes | awk 'NR>1 {print $1}')
kubectl create namespace monitoring || true
for node in ${node_name}; do
  kubectl label nodes "${node}" prometheus=true --overwrite || true
done
helm repo add stable https://charts.onwalk.net/ || true
helm repo update
helm upgrade --install observabilityagent stable/observabilityagent -n monitoring -f values.yaml

