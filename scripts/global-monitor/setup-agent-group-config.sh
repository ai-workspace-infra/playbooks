deepflow-ctl agent-group create vm-group
deepflow-ctl agent-group list vm-group

cat > agent-group-config.yaml << EOF
vtap_group_id: g-3lSjoT4zjY
tap_interface_regex: ^(tap.*|cali.*|veth.*|eth.*|en[ospx].*|lxc.*|lo|docker.*|br.*|wg.*)$
EOF
deepflow-ctl agent-group-config create -f agent-group-config.yaml
