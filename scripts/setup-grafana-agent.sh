#!/bin/bash
set -e

########################################################################################################

helm repo add grafana https://grafana.github.io/helm-charts
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
kubectl create ns deepflow || true

helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  --namespace deepflow --create-namespace

helm upgrade --install node-exporter prometheus-community/prometheus-node-exporter \
  --namespace deepflow --create-namespace \
  --set service.type=ClusterIP \
  --set service.port=9100

cat > grafana-agent-values.yaml << EOF
global:
  image:
    registry: "images.onwalk.net/public"
agent:
  mode: 'static'
  configMap:
    create: true
    content: ''
logs:
  enabled: false
traces:
  enabled: false
EOF

helm upgrade --install grafana-agent grafana/grafana-agent --namespace deepflow -f grafana-agent-values.yaml

cat > grafana-agent-configmap.yaml << EOF
apiVersion: v1
data:
  config.yaml: |-
    server:
      log_level: info
      log_format: logfmt
    metrics:
      global:
        scrape_interval: 1m
      configs:
        - name: agent
          scrape_configs:
            - job_name: kube-state-metrics
              static_configs:
                - targets: ['10.43.155.169:8080']
            - job_name: node-metrics
              static_configs:
                - targets: ['10.43.68.133:9100']
          remote_write:
            - url: http://deepflow-agent.deepflow.svc.cluster.local/api/v1/prometheus
kind: ConfigMap
metadata:
  annotations:
    meta.helm.sh/release-name: grafana-agent
    meta.helm.sh/release-namespace: deepflow
  labels:
    app.kubernetes.io/instance: grafana-agent
    app.kubernetes.io/managed-by: Helm
    app.kubernetes.io/name: grafana-agent
    app.kubernetes.io/version: v0.42.0
    helm.sh/chart: grafana-agent-0.42.0
  name: grafana-agent
  namespace: deepflow
EOF

kubectl apply -f grafana-agent-configmap.yaml

kubectl get pods -n deepflow
