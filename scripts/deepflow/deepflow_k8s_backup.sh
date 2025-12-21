#!/bin/bash

NAMESPACE="deepflow"
VERSION_PREFIX="v6.3"
TIMESTAMP=$(date +"%Y%m%d-%H")
BACKUP_FILE="backup_images_${VERSION_PREFIX}-${TIMESTAMP}.json"

# 备份 deepflow 命名空间的 Kubernetes 资源镜像信息
backup_images() {
    echo "🔄 开始备份 deepflow 命名空间的 Kubernetes 资源镜像信息..."

    kubectl get deployments,statefulsets,daemonsets,cronjobs -n "$NAMESPACE" -o json | jq '
    {
        version: "'${VERSION_PREFIX}-${TIMESTAMP}'",
        items: [
            .items[] | select(.spec != null) | {
                kind: .kind,
                name: .metadata.name,
                containers: (
                    if .kind == "CronJob" then
                        [.spec.jobTemplate.spec.template.spec.containers[]? | {name: .name, image: .image}]
                    else
                        [.spec.template.spec.containers[]? | {name: .name, image: .image}]
                    end
                )
            }
        ]
    }' > "$BACKUP_FILE"

    if [[ -f "$BACKUP_FILE" ]]; then
        echo "✅ 备份成功！文件路径: $BACKUP_FILE"
        echo "📋 备份内容预览（前10行）："
        head -n 10 "$BACKUP_FILE"
    else
        echo "❌ 备份失败，请检查 Kubernetes 访问权限！"
        exit 1
    fi
}

# 校验当前 Kubernetes 资源是否与备份文件一致
check_images() {
    if [[ ! -f "$1" ]]; then
        echo "❌ 错误: 备份文件 $1 不存在！请先运行备份。"
        exit 1
    fi

    echo "🔍 正在校验当前 Kubernetes 资源与备份文件是否一致..."

    CURRENT_IMAGES=$(kubectl get deployments,statefulsets,daemonsets,cronjobs -n "$NAMESPACE" -o json | jq '
    {
        items: [
            .items[] | select(.spec != null) | {
                kind: .kind,
                name: .metadata.name,
                containers: (
                    if .kind == "CronJob" then
                        [.spec.jobTemplate.spec.template.spec.containers[]? | {name: .name, image: .image}]
                    else
                        [.spec.template.spec.containers[]? | {name: .name, image: .image}]
                    end
                )
            }
        ]
    }')

    BACKUP_IMAGES=$(cat "$1")

    MATCH_COUNT=0
    MISMATCH_COUNT=0

    echo "$BACKUP_IMAGES" | jq -c '.items[]' | while read -r backup_item; do
        kind=$(echo "$backup_item" | jq -r '.kind')
        name=$(echo "$backup_item" | jq -r '.name')

        echo "📌 检查 $kind/$name ..."

        backup_containers=$(echo "$backup_item" | jq -c '.containers[]?')
        current_containers=$(echo "$CURRENT_IMAGES" | jq -c --arg name "$name" '.items[] | select(.name == $name) | .containers[]?')

        for backup_container in $backup_containers; do
            container_name=$(echo "$backup_container" | jq -r '.name')
            backup_image=$(echo "$backup_container" | jq -r '.image')

            current_image=$(echo "$current_containers" | jq -r --arg container_name "$container_name" 'select(.name == $container_name) | .image')

            if [[ "$backup_image" == "$current_image" ]]; then
                echo "   ✅ $container_name 镜像匹配: $backup_image"
                ((MATCH_COUNT++))
            else
                echo "   ❌ $container_name 镜像不匹配: 期望 $backup_image，当前 $current_image"
                ((MISMATCH_COUNT++))
            fi
        done
    done

    echo "📊 校验结果: ✅ 匹配 $MATCH_COUNT 项, ❌ 不匹配 $MISMATCH_COUNT 项"

    if [[ $MISMATCH_COUNT -eq 0 ]]; then
        echo "✅ 校验通过！当前运行的镜像版本与备份一致。"
    else
        echo "❌ 校验失败！请检查上方输出。"
    fi
}

# 恢复 deepflow 命名空间的 Kubernetes 资源镜像
restore_images() {
    if [[ ! -f "$1" ]]; then
        echo "❌ 错误: 备份文件 $1 不存在！请先运行备份。"
        exit 1
    fi

    echo "🔄 开始恢复 deepflow 命名空间的 Kubernetes 资源镜像..."

    cat "$1" | jq -c '.items[]' | while read -r item; do
        kind=$(echo "$item" | jq -r '.kind')
        name=$(echo "$item" | jq -r '.name')

        echo "📌 处理 $kind/$name ..."

        containers=$(echo "$item" | jq -c '.containers[]?')
        for container in $containers; do
            container_name=$(echo "$container" | jq -r '.name')
            image=$(echo "$container" | jq -r '.image')

            echo "   🔄 更新容器: $container_name -> 镜像: $image"
            kubectl set image -n "$NAMESPACE" "$kind/$name" "$container_name=$image" --record
            if [[ $? -eq 0 ]]; then
                echo "   ✅ 更新成功！"
            else
                echo "   ❌ 更新失败！请检查日志。"
            fi
        done
    done

    echo "✅ 恢复完成！"
}

# 解析命令参数
case "$1" in
    backup)
        backup_images
        ;;
    check)
        if [[ -z "$2" ]]; then
            echo "❌ 错误: 需要提供备份文件路径！示例: $0 check backup_images_v6.3-20250309-17.json"
            exit 1
        fi
        check_images "$2"
        ;;
    restore)
        if [[ -z "$2" ]]; then
            echo "❌ 错误: 需要提供备份文件路径！示例: $0 restore backup_images_v6.3-20250309-17.json"
            exit 1
        fi
        restore_images "$2"
        ;;
    *)
        echo "📌 用法: $0 {backup|check <备份文件>|restore <备份文件>}"
        exit 1
        ;;
esac
