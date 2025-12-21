#!/bin/bash

Splunk_HEC_URL=$1
Splunk_HEC_TOKEN=$2

helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart
helm repo update

cat > vaules.yaml << EOF
clusterName: Demo
splunkPlatform:
  endpoint: $Splunk_HEC_URL 
  token: $Splunk_HEC_TOKEN
  index: harbor
  insecureSkipVerify: true
EOF

helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector -f vaules.yaml

curl -k "${Splunk_HEC_URL}"     -H "Authorization: Splunk ${Splunk_HEC_TOKEN}" -d '{"event": "Hello, world!", "sourcetype": "manual"}'
