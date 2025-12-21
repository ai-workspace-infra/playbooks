#!/bin/bash

# 设置 containerd 地址
CONTAINERD_ADDRESS="/run/k3s/containerd/containerd.sock"

# 列出所有镜像并删除没有标签的镜像
sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl --namespace k8s.io images -a | grep "<none>" | awk '{print $3}' | while read image_id; do
    echo "Deleting image: $image_id"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl --namespace k8s.io rmi "$image_id"
done

echo "Cleanup complete."
