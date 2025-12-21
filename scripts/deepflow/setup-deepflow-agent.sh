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
