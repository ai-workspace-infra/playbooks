#!/bin/bash
set -e

echo "🔍 检查 Cilium 运行环境依赖项..."

# 自动挂载 bpffs
auto_mount_bpffs() {
    echo "👉 自动挂载 bpffs..."
    sudo mount bpffs /sys/fs/bpf -t bpf
    echo "✅ bpffs 已挂载"
}

# 自动加载内核模块
auto_load_modules() {
    echo "👉 自动加载内核模块..."
    for mod in "${REQUIRED_MODULES[@]}"; do
        if ! lsmod | grep -q "$mod"; then
            sudo modprobe "$mod"
            echo "✅ $mod 已加载"
        fi
    done
}

# 自动启用内核配置项
auto_enable_kernel_config() {
    echo "👉 自动启用内核配置项..."
    local CONFIG_FILE="/boot/config-$(uname -r)"

    for cfg in "${REQUIRED_CONFIGS[@]}"; do
        if ! grep -q "${cfg}=y" "$CONFIG_FILE" && ! grep -q "${cfg}=m" "$CONFIG_FILE"; then
            echo "❌ $cfg 未启用，正在启用..."
            # 更新配置文件（需要手动修改，或者重新编译内核）
            echo "请手动启用内核配置：$cfg"
        else
            echo "✅ $cfg 已启用"
        fi
    done
}

# 检查 bpffs 是否挂载
check_bpffs() {
    echo -n "🔸 检查 bpffs 是否挂载 (/sys/fs/bpf)... "
    if mount | grep -q '/sys/fs/bpf type bpf'; then
        echo "✅ 已挂载"
    else
        echo "❌ 未挂载"
        if [ "$AUTOFIX" = "true" ]; then
            auto_mount_bpffs
        fi
    fi
}

# 检查内核模块
check_kernel_modules() {
    REQUIRED_MODULES=(
        "vxlan" "geneve" "ip_set" "xt_set" "xt_comment"
        "xt_mark" "xt_socket" "xt_tproxy" "xt_conntrack"
        "xfrm_user" "xfrm_algo" "xfrm_ipcomp" "ipcomp"
        "net_cls" "net_cls_act" "net_sch_ingress"
        "net_sch_fq" "crypto_user"
    )
    echo "🔸 检查内核模块加载状态："
    for mod in "${REQUIRED_MODULES[@]}"; do
        if lsmod | grep -q "$mod"; then
            echo "✅ $mod 已加载"
        else
            echo "❌ $mod 未加载（可尝试：modprobe $mod）"
            if [ "$AUTOFIX" = "true" ]; then
                auto_load_modules
            fi
        fi
    done
}

# 检查内核配置项是否开启（通过 /boot/config-$(uname -r) 或 /proc/config.gz）
check_kernel_config() {
    echo "🔸 检查内核配置项："
    CONFIG_FILE=""
    if [ -f "/boot/config-$(uname -r)" ]; then
        CONFIG_FILE="/boot/config-$(uname -r)"
    elif [ -f "/proc/config.gz" ]; then
        zcat /proc/config.gz > /tmp/kernel_config_check
        CONFIG_FILE="/tmp/kernel_config_check"
    else
        echo "⚠️  无法找到内核配置文件，跳过配置检查"
        return
    fi

    REQUIRED_CONFIGS=(
        "CONFIG_BPF"
        "CONFIG_BPF_SYSCALL"
        "CONFIG_NET_CLS_BPF"
        "CONFIG_BPF_JIT"
        "CONFIG_NET_CLS_ACT"
        "CONFIG_NET_SCH_INGRESS"
        "CONFIG_CRYPTO_SHA1"
        "CONFIG_CRYPTO_USER_API_HASH"
        "CONFIG_CGROUPS"
        "CONFIG_CGROUP_BPF"
        "CONFIG_PERF_EVENTS"
        "CONFIG_VXLAN"
        "CONFIG_FIB_RULES"
        "CONFIG_NET_SCH_FQ"
    )

    for cfg in "${REQUIRED_CONFIGS[@]}"; do
        if grep -q "${cfg}=y" "$CONFIG_FILE" || grep -q "${cfg}=m" "$CONFIG_FILE"; then
            echo "✅ $cfg 已启用"
        else
            echo "❌ $cfg 未启用"
            if [ "$AUTOFIX" = "true" ]; then
                auto_enable_kernel_config
            fi
        fi
    done

    [ -f /tmp/kernel_config_check ] && rm /tmp/kernel_config_check
}

# 主执行流程
AUTOFIX="false"
while getopts "f" opt; do
    case $opt in
        f)
            AUTOFIX="true"
            echo "👉 自动修复已启用！"
            ;;
        *)
            echo "用法: $0 [-f] 启用自动修复"
            exit 1
            ;;
    esac
done

check_bpffs
check_kernel_modules
check_kernel_config

echo "✅ 检查完成：请根据上方提示补全内核模块、参数或挂载配置。"

