#!/bin/bash
set -e

cat << EOF > values-custom.yaml
deepflowServerNodeIPS:
- 10.50.1.111 
#deepflowK8sClusterID: "fffffff"  # FIXME: K8s ClusterID
image:
  repository: hub.deepflow.yunshan.net/public/deepflow-agent
  pullPolicy: Always
  tag: v6.5
EOF

helm repo add deepflow https://deepflowio.github.io/deepflow
helm repo update deepflow # use `helm repo update` when helm < 3.7.0
helm install deepflow-agent -n deepflow deepflow/deepflow-agent --create-namespace -f values-custom.yaml

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

########################################################################################################


helm repo add vector https://helm.vector.dev
helm repo update
cat << EOF > vector-values-custom.yaml
role: Agent
#nodeSelector:
#  allow/vector: "false"

# resources -- Set Vector resource requests and limits.
resources:
  requests:
    cpu: 200m
    memory: 256Mi
  limits:
    cpu: 200m
    memory: 256Mi
image:
  repository: images.onwalk.net/public/timberio/vector
  pullPolicy: Always
  tag: "0.37.1-distroless-libc"
podLabels:
  vector.dev/exclude: "true"
  app: deepflow
# extraVolumes -- Additional Volumes to use with Vector Pods.
  # extraVolumes:
  # - name: opt-log
  #   hostPath:
  #     path: "/opt/log/"
# extraVolumeMounts -- Additional Volume to mount into Vector Containers.
  # extraVolumeMounts:
  # - name: opt-log
  #   mountPath: "/opt/log/"
  #   readOnly: true
customConfig:
  ## The configuration comes from https://vector.dev/docs/reference/configuration/global-options/#data_dir
  data_dir: /vector-data-dir
  api:
    enabled: true
    address: 127.0.0.1:8686
    playground: false
  sources:
    kubernetes_logs:
      type: kubernetes_logs
      namespace_annotation_fields:
        namespace_labels: ""
      node_annotation_fields:
        node_labels: ""
      pod_annotation_fields:
        pod_annotations: ""
        pod_labels: ""

  transforms:
    remap_kubernetes_logs:
      type: remap
      inputs:
      - kubernetes_logs
      source: |-
        # try to parse json
        if is_string(.message) && is_json(string!(.message)) {
            tags = parse_json(.message) ?? {}
            .message = tags.message # FIXME: the log content key inside json
            del(tags.message)
            .json = tags
        }

        if !exists(.level) {
           if exists(.json) {
            .level = .json.level
            del(.json.level)
           } else {
            # match log levels surround by ``[]`` or ``<>`` with ignore case
            level_tags = parse_regex(.message, r'[\[\\\<](?<level>(?i)INFOR?(MATION)?|WARN(ING)?|DEBUG?|ERROR?|TRACE|FATAL|CRIT(ICAL)?)[\]\\\>]') ?? {}
            if !exists(level_tags.level) {
              # match log levels surround by whitespace, required uppercase strictly in case mismatching
              level_tags = parse_regex(.message, r'[\s](?<level>INFOR?(MATION)?|WARN(ING)?|DEBUG?|ERROR?|TRACE|FATAL|CRIT(ICAL)?)[\s]') ?? {}
            }
            if exists(level_tags.level) {
              level_tags.level = upcase(string!(level_tags.level))
              .level = level_tags.level
            }
          }
        }

        if !exists(._df_log_type) {
            # default log type
            ._df_log_type = "user"
        }

        if !exists(.app_service) {
            # FIXME: files 模块没有此字段，请通过日志内容注入应用名称
            .app_service = .kubernetes.container_name
        }
  sinks:
    http:
      encoding:
        codec: json
      inputs:
      - remap_kubernetes_logs # NOTE: 注意这里数据源是 transform 模块的 key
      type: http
      uri: http://deepflow-agent.deepflow/api/v1/log
EOF
helm upgrade --install vector vector/vector --namespace deepflow --create-namespace -f vector-values-custom.yaml

