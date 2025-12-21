#!/bin/bash

# 定义要查询的包列表
PACKAGES=(plasma-desktop dolphin konsole chromium sddm)

# 输出依赖关系的文件
DEP_FILE="kde_dependencies.txt"
SRPM_FILE="kde_srpm_list.txt"

# 清空旧文件
> "$DEP_FILE"
> "$SRPM_FILE"

# 递归获取依赖项的函数
get_dependencies() {
    local package="$1"
    echo "查询 $package 的依赖关系..."
    local dependencies=$(dnf repoquery --requires --resolve "$package" 2>/dev/null)

    for dep in $dependencies; do
        # 避免重复写入
        if ! grep -q "^$dep$" "$DEP_FILE"; then
            echo "$dep" | tee -a "$DEP_FILE"
            get_dependencies "$dep"
        fi
    done
}

# 遍历所有初始包
for pkg in "${PACKAGES[@]}"; do
    echo "$pkg" | tee -a "$DEP_FILE"
    get_dependencies "$pkg"
    echo "------------------------------------------------------"
done

# 统计最终的依赖包数量
TOTAL_PACKAGES=$(wc -l < "$DEP_FILE")
echo "总计依赖包数量: $TOTAL_PACKAGES"

# 获取所有包的 SRPM
while read -r pkg; do
    srpm=$(dnf repoquery --source "$pkg" 2>/dev/null)
    if [ -n "$srpm" ]; then
        echo "$srpm" | tee -a "$SRPM_FILE"
    fi
done < "$DEP_FILE"

# 统计 SRPM 数量
TOTAL_SRPM=$(wc -l < "$SRPM_FILE")
echo "总计 SRPM 包数量: $TOTAL_SRPM"

# 下载所有 SRPM 包
dnf download --source $(cat "$SRPM_FILE") --setopt=install_weak_deps=False

echo "依赖包列表已保存到 $DEP_FILE"
echo "SRPM 包列表已保存到 $SRPM_FILE"
echo "所有 SRPM 包下载完成"

