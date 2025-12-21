#!/bin/bash
for interface in `ip -br link | awk '/^(ens|cni)/ {print $1}'`
do
    status=$(ip link show "$interface" | grep -q "state UP" && echo -n "UP" || echo -n "DOWN")
    ip_addr=$(ip -br addr show "$interface" | awk '{print $3}')
    default_gw=$(ip route | grep default | awk '{print $3}')

    echo "$interface    $status    $ip_addr    $default_gw"

done
