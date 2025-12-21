# ================================================
# 🚀 一键 WSL2 桥接 + 静态 IP 设置 + 开机自启注册
# 文件名：start-wsl-bridge.ps1
# 请使用【管理员身份】运行！
# ================================================

# -------------------------------
# 配置区：按需修改
# -------------------------------
$InterfaceAlias = "Ethernet"            # 你的宿主机物理网卡名
$WSLStaticIP = "10.253.0.2"
$WSLGateway = "10.253.0.1"
$WSLInstance = "Ubuntu-22.04"           # wsl -l -v 可查看
$BridgeRepo = "https://github.com/sakai135/wsl2-network-bridge.git"
$BridgeFolder = "$env:USERPROFILE\wsl2-network-bridge"
$TaskName = "StartWSLBridge"
$ScriptPath = "$PSScriptRoot\start-wsl-bridge.ps1"

# -------------------------------
# 权限检查
# -------------------------------
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Warning "❗ 请用【管理员身份】运行此脚本！"
    exit 1
}

# -------------------------------
# 安装 wsl2-network-bridge
# -------------------------------
if (-not (Test-Path $BridgeFolder)) {
    Write-Host "🔧 克隆 wsl2-network-bridge..."
    git clone $BridgeRepo $BridgeFolder
} else {
    Write-Host "✅ 已存在 wsl2-network-bridge 工具目录。"
}

# -------------------------------
# 启动网络桥接
# -------------------------------
Write-Host "🌐 正在启动网络桥接..."
& "$BridgeFolder\bridge.ps1" -InterfaceAlias $InterfaceAlias

# -------------------------------
# 设置 WSL 静态 IP
# -------------------------------
Write-Host "📡 设置 WSL 的静态 IP 为 $WSLStaticIP ..."
wsl -d $WSLInstance -- bash -c "
    sudo ip addr flush dev eth0;
    sudo ip addr add $WSLStaticIP/24 dev eth0;
    sudo ip link set eth0 up;
    sudo ip route add default via $WSLGateway || true;
"

# -------------------------------
# 添加开机自启计划任务
# -------------------------------
Write-Host "🗓️ 正在添加计划任务 [$TaskName]，用于开机自动运行此脚本..."

# 如果任务已存在，先删除
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "🔄 已删除旧任务 $TaskName"
}

# 创建任务
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal

# -------------------------------
# 完成提示
# -------------------------------
Write-Host "`n✅ 一切就绪！"
Write-Host "📌 静态 IP：$WSLStaticIP"
Write-Host "📌 WSL实例：$WSLInstance"
Write-Host "📌 下次登录将自动执行此桥接脚本。"
Write-Host "`n✨ 现在可以在局域网使用以下命令登录："
Write-Host "    ssh <your-username>@$WSLStaticIP"
