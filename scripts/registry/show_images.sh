#!/bin/bash

# 设置协议和 registry 地址（https:// 或 http://）
PROTOCOL="https://"
REGISTRY="local-registry.onwalk.net:5000"

# 获取仓库列表
repos=$(curl -s -X GET "$PROTOCOL$REGISTRY/v2/_catalog" | jq -r '.repositories[]')

# 要隐藏的仓库列表
hidden_repos=("")

# 创建或清空输出文件
output_file="all.tag.list"
> "$output_file"

# 遍历每个仓库，获取对应的标签列表
for repo in $repos; do
    # 如果是隐藏的仓库，跳过
    if [[ " ${hidden_repos[@]} " =~ " ${repo} " ]]; then
        continue
    fi

    # 获取标签列表
    tags=$(curl -s -X GET "$PROTOCOL$REGISTRY/v2/$repo/tags/list" | jq -r '.tags[]')

    # 如果仓库有标签，则按格式输出到文件
    if [ -n "$tags" ]; then
        for tag in $tags; do
            # 输出格式：local-registry.onwalk.net:5000/repository:tag
            echo "$REGISTRY/$repo:$tag" >> "$output_file"
        done
    fi
done

# 排序并去重
sort -u "$output_file" -o "$output_file"
