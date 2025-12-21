for image in \
kube.registry.local:5000/acl-controller:v6.4.182 \
kube.registry.local:5000/alarm:v6.4.703 \
kube.registry.local:5000/cerebro:0.9.0 \
kube.registry.local:5000/deepflow-agent:v6.4.4729 \
kube.registry.local:5000/deepflow-app:v6.4.178 \
kube.registry.local:5000/df-help:v6.4.1211 \
kube.registry.local:5000/df-web-qiankun-core:v6.4.11721 \
kube.registry.local:5000/df-web-service:v6.4.647 \
kube.registry.local:5000/df-web-metrics-explore:v6.4.5342 \
kube.registry.local:5000/df-env:v6.4.884 \
kube.registry.local:5000/fauths:v6.4.482 \
kube.registry.local:5000/fpermit:v6.4.278 \
kube.registry.local:5000/apientry:v6.4.210 \
kube.registry.local:5000/fuser:v6.4.356 \
kube.registry.local:5000/grafana-agent:v0.38.0 \
kube.registry.local:5000/grafana-agent-reload:v0.8.0 \
kube.registry.local:5000/deepflow-init-grafana-ce:latest \
kube.registry.local:5000/kibana:6.8.8 \
kube.registry.local:5000/kube-rbac-proxy:v0.14.0 \
kube.registry.local:5000/kube-state-metrics:v2.9.2 \
kube.registry.local:5000/manager:v6.4.695 \
kube.registry.local:5000/mntnct:v6.4.1320 \
kube.registry.local:5000/mysql-server:8.0.39 \
kube.registry.local:5000/pcap:v6.4.194 \
kube.registry.local:5000/postman:v6.4.55 \
kube.registry.local:5000/querier-js:v6.4.303 \
kube.registry.local:5000/rabbitmq:3.10.25 \
kube.registry.local:5000/redis:7.0.12 \
kube.registry.local:5000/report:v6.4.267 \
kube.registry.local:5000/statistics:v6.4.2171 \
kube.registry.local:5000/talker:v6.4.2987 \
kube.registry.local:5000/warrant:v6.4.88 \
kube.registry.local:5000/df-web-sched:v6.4.213 \
kube.registry.local:5000/web-tools:v6.4.231 \
kube.registry.local:5000/webssh:v6.4.25
do
  echo "🔄 Pulling $image ..."
  nerdctl --insecure-registry -n k8s.io pull "$image"
done
