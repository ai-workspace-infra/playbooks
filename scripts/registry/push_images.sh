#!/bin/bash

set +x

# 设置容器和仓库地址
CONTAINERD_ADDRESS="/run/k3s/containerd/containerd.sock"
LOCAL_REGISTRY="local-registry.onwalk.net:5000"
TARGET_REGISTRY="images.onwalk.net/private/deepflow-v6.5"

# 设置输出文件
input_file="all.tag.list"

# 登录到目标 registry
echo "Logging in to $TARGET_REGISTRY..."
sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl login $TARGET_REGISTRY

# 读取 all.tag.list 并处理每个镜像
while IFS= read -r line; do
    # 如果行为空，跳过
    if [ -z "$line" ]; then
        continue
    fi

    # 替换 local-registry 地址为目标地址, 也删除 :5000 端口
    target_tag="${line//$LOCAL_REGISTRY/$TARGET_REGISTRY}"

    # 打标签并推送镜像
    echo "Tagging and Pushing $line -> $target_tag"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl pull "$line"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl tag "$line" "$target_tag"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl push "$target_tag"

    # 清理本地镜像
    echo "Cleaning up local image: $line"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl rmi "$line"
    echo "Cleaning up local image: $target_tag"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl rmi "$target_tag"
done < "$input_file"

