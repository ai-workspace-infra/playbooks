# 使用方法（WSL 内部）：

chmod +x init-wsl.sh && ./init-wsl.sh

# 📌 检查项

- 确认 IP	ip a show eth0 应该是 10.253.0.2
- 检查 ssh 是否启动	sudo systemctl status ssh
- 检查 systemd 是否开启	ps -p 1 -o comm= 应该是 systemd
- 检查端口监听	`ss -tlnp
