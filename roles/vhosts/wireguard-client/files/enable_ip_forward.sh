#!/bin/sh
cat >> /etc/sysctl.conf << EOF
net.ipv4.ip_forward = 1
EOF
sysctl -p
