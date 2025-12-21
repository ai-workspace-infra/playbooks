helm repo add deepflow https://deepflowio.github.io/deepflow
helm repo update deepflow
cat > values.yaml << EOF
global:
  replicas: 1
  storageEngine: clickhouse
byconity:
  enabled: false
EOF
helm upgrade --install deepflow -n deepflow deepflow/deepflow --version 6.5.012 --create-namespace -f values.yaml
curl -o /usr/bin/deepflow-ctl https://deepflow-ce.oss-cn-beijing.aliyuncs.com/bin/ctl/v6.4.9/linux/$(arch | sed 's|x86_64|amd64|' | sed 's|aarch64|arm64|')/deepflow-ctl
chmod a+x /usr/bin/deepflow-ctl

NODE_PORT=$(kubectl get --namespace deepflow -o jsonpath="{.spec.ports[0].nodePort}" services deepflow-grafana)
NODE_IP=$(kubectl get nodes -o jsonpath="{.items[0].status.addresses[0].address}")
echo -e "Grafana URL: http://$NODE_IP:$NODE_PORT  \nGrafana auth: admin:deepflow"


kubectl delete  deployment.apps/deepflow-byconity-daemon-manager -n deepflow
kubectl delete  deployment.apps/deepflow-byconity-fdbcli -n deepflow
kubectl delete  deployment.apps/deepflow-byconity-resource-manager  -n deepflow
kubectl delete  deployment.apps/deepflow-fdb-operator -n deepflow
kubectl delete  statefulset.apps/deepflow-byconity-server -n deepflow
kubectl delete  statefulset.apps/deepflow-byconity-tso  -n deepflow
kubectl delete  statefulset.apps/deepflow-byconity-vw-vw-default -n deepflow
kubectl delete  statefulset.apps/deepflow-byconity-vw-vw-write -n deepflow
kubectl delete svc -n  deepflow `kubectl  get svc -n deepflow | grep deepflow-byconity | awk '{print $1}' | xargs`
