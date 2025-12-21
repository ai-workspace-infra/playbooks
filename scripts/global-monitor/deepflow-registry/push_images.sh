#!/bin/bash

set +x

# 设置容器和仓库地址
CONTAINERD_ADDRESS="/run/k3s/containerd/containerd.sock"
LOCAL_REGISTRY="local-registry.onwalk.net:5000"
TARGET_REGISTRY="global-images.onwalk.net/private/deepflow-v6.3"

# 设置输出文件
input_file="all.tag.list"

# 登录到目标 registry
echo "Logging in to $TARGET_REGISTRY..."
sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl login $TARGET_REGISTRY

# 读取 all.tag.list 并处理每个镜像
while IFS= read -r line; do
    # 替换 local-registry 地址为目标地址
    image_tag="${line//$LOCAL_REGISTRY/$TARGET_REGISTRY}"

    # 打标签并推送镜像
    echo "Tagging $line as $image_tag and pushing to $TARGET_REGISTRY"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl pull "$line"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl tag "$line" "$image_tag"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl push "$image_tag"

    # 清理本地镜像
    echo "Cleaning up local image: $line"
    sudo CONTAINERD_ADDRESS=$CONTAINERD_ADDRESS nerdctl rmi "$line"
done < "$input_file"

echo "All images processed and pushed successfully."
