
#!/bin/bash

# 获取操作系统信息
get_os_info() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_NAME=$NAME
        OS_VERSION=$VERSION_ID
    elif type lsb_release >/dev/null 2>&1; then
        OS_NAME=$(lsb_release -si)
        OS_VERSION=$(lsb_release -sr)
    else
        OS_NAME=$(uname -s)
        OS_VERSION=$(uname -r)
    fi
    echo "当前操作系统: $OS_NAME $OS_VERSION"
}

# 检查 DNS 解析
check_dns() {
    echo "检查 DNS 解析配置..."
    dns_config=$(grep "nameserver" /etc/resolv.conf)
    if [[ -n "$dns_config" && "$dns_config" != *"127.0.0.1"* ]]; then
        echo "✅ DNS 解析配置正确"
    else
        echo "❌ DNS 解析配置错误，未设置或包含127.0.0.1"
        operations+="\n1. 编辑 /etc/resolv.conf，配置有效的 nameserver，如 114.114.114.114"
    fi
}

# 检查主机名配置
check_hostname() {
    echo "检查主机名配置..."
    hostname=$(hostname)
    if [[ "$hostname" != *"local"* && "$hostname" != *"_"* && ${#hostname} -le 64 ]]; then
        echo "✅ 主机名配置正确：$hostname"
    else
        echo "❌ 主机名配置不符合要求：$hostname"
        operations+="\n2. 修改主机名为合法值，使用 hostnamectl set-hostname 命令"
    fi

    # 检查 /etc/hosts 是否包含主机名解析
    hosts_file=$(cat /etc/hosts)
    if [[ "$hosts_file" == *"$hostname"* ]]; then
        echo "✅ /etc/hosts 中包含主机名解析"
    else
        echo "❌ /etc/hosts 中未找到主机名解析"
        operations+="\n3. 修改 /etc/hosts，添加主机名解析"
    fi
}

# 检查数据盘挂载
check_disk_mount() {
    echo "检查数据盘挂载..."
    lsblk_output=$(lsblk)
    df_output=$(df -hT)
    # 检查是否挂载 /mnt 目录
    if [[ "$df_output" == *"/mnt"* ]]; then
        echo "✅ 数据盘已挂载到 /mnt"
        # 打印 /mnt 的大小
        mnt_size=$(df -h | grep '/mnt' | awk '{print $2}')
        echo "当前 /mnt 大小: $mnt_size"
    else
        echo "❌ 数据盘未挂载到 /mnt"
        operations+="\n4. 挂载数据盘到 /mnt"
    fi

    # 检查 /etc/fstab 中是否包含自动挂载配置
    fstab_config=$(grep "/mnt" /etc/fstab)
    if [[ -n "$fstab_config" ]]; then
        echo "✅ /etc/fstab 中包含数据盘自动挂载配置"
    else
        echo "❌ /etc/fstab 中未找到数据盘自动挂载配置"
        operations+="\n5. 在 /etc/fstab 中添加自动挂载配置"
    fi
}

# 检查免密登录配置
check_ssh_key() {
    echo "检查免密登录配置..."
    ssh_config_dir="/root/.ssh"
    if [[ -d "$ssh_config_dir" && -f "$ssh_config_dir/authorized_keys" ]]; then
        echo "✅ 已配置免密登录"
    else
        echo "❌ 未配置免密登录"
        operations+="\n6. 配置免密登录：使用 ssh-keygen 和 ssh-copy-id 配置公钥免密登录"
    fi
}

# 检查 swap 状态
check_swap() {
    echo "检查 swap 缓存..."
    swap_status=$(swapon --show)
    if [[ -z "$swap_status" ]]; then
        echo "✅ swap 已关闭"
    else
        echo "❌ swap 未关闭"
        operations+="\n7. 关闭 swap：执行 swapoff -a 并删除 /etc/fstab 中的 swap 条目"
    fi
}

# 检查防火墙状态
check_firewall() {
    echo "检查防火墙状态..."
    if [[ "$OS_NAME" == "CentOS" || "$OS_NAME" == "RedHat" ]]; then
        firewalld_status=$(systemctl is-active firewalld)
        if [[ "$firewalld_status" == "inactive" ]]; then
            echo "✅ 防火墙已关闭"
        else
            echo "❌ 防火墙未关闭"
            operations+="\n8. 停止防火墙并禁用：执行 systemctl stop firewalld 和 systemctl disable firewalld"
        fi
    else
        ufw_status=$(ufw status | grep "Status" | awk '{print $2}')
        if [[ "$ufw_status" == "inactive" ]]; then
            echo "✅ 防火墙已关闭"
        else
            echo "❌ 防火墙未关闭"
            operations+="\n8. 停止防火墙并禁用：执行 ufw disable"
        fi
    fi
}

# 检查 SELinux 或 AppArmor 状态
check_security() {
    echo "检查 SELinux 或 AppArmor 状态..."
    if [[ "$OS_NAME" == "CentOS" || "$OS_NAME" == "RedHat" ]]; then
        selinux_status=$(getenforce)
        if [[ "$selinux_status" == "Disabled" ]]; then
            echo "✅ SELinux 已禁用"
        else
            echo "❌ SELinux 未禁用"
            operations+="\n9. 禁用 SELinux：执行 setenforce 0 并修改 /etc/selinux/config"
        fi
    elif [[ "$OS_NAME" == "Ubuntu" ]]; then
        apparmor_status=$(systemctl is-active apparmor)
        if [[ "$apparmor_status" == "inactive" ]]; then
            echo "✅ AppArmor 已禁用"
        else
            echo "❌ AppArmor 未禁用"
            operations+="\n9. 禁用 AppArmor：执行 systemctl stop apparmor 并禁用 systemctl disable apparmor"
        fi
    else
        echo "❌ 无法识别 SELinux 或 AppArmor 状态"
        operations+="\n9. SELinux 或 AppArmor 状态检查适用于 CentOS/RedHat 或 Ubuntu 系统"
    fi
}

# 检查 IPV4 流量转发
check_ip_forward() {
    echo "检查 IPV4 流量转发..."
    ipv4_forward_status=$(sysctl net.ipv4.ip_forward | grep -o "net.ipv4.ip_forward = 1")
    if [[ -n "$ipv4_forward_status" ]]; then
        echo "✅ IPV4 流量转发已开启"
    else
        echo "❌ IPV4 流量转发未开启"
        operations+="\n10. 开启 IPV4 流量转发：执行 echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf && sysctl -p"
    fi
    # 打印 /etc/sysctl.conf 中 ip_forward 配置
    ip_forward_config=$(grep "net.ipv4.ip_forward" /etc/sysctl.conf)
    echo "当前 /etc/sysctl.conf 中的 IPV4 流量转发配置：$ip_forward_config"
}

# 检查操作系统连接数限制
check_conn_limit() {
    echo "检查操作系统级别连接数限制..."

    # 获取 ulimit 输出
    ulimit_output=$(ulimit -a)

    # 获取 nofile 和 nproc 配置的值
    nofile_limit=$(ulimit -n)
    nproc_limit=$(ulimit -u)

    # 检查 nofile 和 nproc 是否为 1048576
    if [[ "$nofile_limit" -eq 1048576 && "$nproc_limit" -eq 1048576 ]]; then
        echo "✅ 系统连接数限制配置正确: nofile = $nofile_limit, nproc = $nproc_limit"
    else
        echo "❌ 系统连接数限制配置错误"
        echo "   当前 nofile = $nofile_limit, nproc = $nproc_limit"
        operations+="\n11. 修改连接数限制：编辑 /etc/security/limits.conf 文件并配置 nofile 和 nproc 为 1048576"
    fi

    # 检查 /etc/security/limits.conf 文件中的 root 连接数限制配置
    limits_config=$(grep -E "root\s+soft\s+nofile\s+1048576|root\s+hard\s+nofile\s+1048576|root\s+soft\s+nproc\s+1048576|root\s+hard\s+nproc\s+1048576" /etc/security/limits.conf)
    if [[ -z "$limits_config" ]]; then
        echo "❌ /etc/security/limits.conf 中未设置正确的连接数限制"
        operations+="\n12. 请检查 /etc/security/limits.conf 中是否配置了以下项：\nroot soft nofile 1048576\nroot hard nofile 1048576\nroot soft nproc 1048576\nroot hard nproc 1048576"
    else
        echo "✅ /etc/security/limits.conf 中的关键配置项："
        echo "$limits_config"
    fi
}

# 统一列出检查结果
operations=""
get_os_info
check_dns
check_hostname
check_disk_mount
check_ssh_key
check_swap
check_firewall
check_security
check_ip_forward
check_conn_limit

echo -e "\n检查完成。"

if [[ -n "$operations" ]]; then
  echo -e "未通过的检查项及建议操作：$operations"
else
  echo "所有检查项通过！"
fi
