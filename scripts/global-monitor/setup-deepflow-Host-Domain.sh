unset DOMAIN_NAME
DOMAIN_NAME="vm-group"  # FIXME: domain name

cat << EOF | deepflow-ctl domain create -f -
name: $DOMAIN_NAME
type: agent_sync
EOF

