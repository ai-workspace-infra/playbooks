curl 'http://10.47.40.250:8123/?query=SELECT+version()'
curl 'http://admin:27ff0399-0d3a-4bd8-919d-17c2181e6fb9@10.47.40.250:8123/?query=SELECT+DISTINCT+cluster+FROM+system.clusters'

helm repo add signoz https://charts.signoz.io
helm repo update signoz
cat << EOF > values-custom.yaml
clickhouse:
  enabled: true
EOF
helm upgrade --install signoz -n signoz signoz/signoz --create-namespace -f values-custom.yaml
