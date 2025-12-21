#!/bin/bash
export CLUSTER_NAME=$1

cat > custom-domain.yaml << EOF 
name: "$CLUSTER_NAME"
type: kubernetes
config:
  controller_ip: 35.72.247.255
  node_port_name_regex: ^(cni|eth|flannel|vxlan.calico|wg|ens|tunl|en[ospx])
EOF

deepflow-ctl domain create -f custom-domain.yaml
deepflow-ctl domain list $CLUSTER_NAME
