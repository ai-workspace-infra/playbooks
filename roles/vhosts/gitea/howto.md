# Gitea & act_runner TLDR

## 批量迁移 GitHub 组织仓库

我已经为你编写了一个 `migrate_repos.py` 脚本，位于 `files/migrate_repos.py` 中。
你可以使用它来批量将 GitHub 组织内的所有仓库迁移（或 Mirror）到你的本地 Gitea 实例。

**使用方法：**

```bash
./files/migrate_repos.py \
    --github-token "<你的_GITHUB_TOKEN>" \
    --gitea-token "<你的_GITEA_API_TOKEN>" \
    --orgs "ai-workspace-infra, ai-workspace-lab, ai-workspace-services"
```
*(注意：生成的 Gitea Token 必须具备 `write:organization` 和 `write:repository` 权限。)*

## 开启与注册 Gitea Actions (act_runner)

要让你的 Gitea 支持 CI/CD，你需要开启 Actions 并在服务器上部署 `act_runner`。

### 1. 确认 Gitea Actions 已开启
我在后台已经通过 Ansible 修改了 Gitea 的 `app.ini` 配置文件并重启了服务：
```ini
[actions]
ENABLED = true
```
目前你的 Gitea 实例已经支持 Actions！你可以在“站点管理 -> Actions”中看到。

### 2. 在服务器上部署 act_runner
请登录到你的宿主机（例如 `install.svc.plus`）上执行以下步骤：

**2.1 下载安装**
```bash
wget -O /usr/local/bin/act_runner https://dl.gitea.com/act_runner/0.2.10/act_runner-0.2.10-linux-amd64
chmod +x /usr/local/bin/act_runner
```

**2.2 在网页端获取注册 Token**
- 登录 Gitea 后，进入 **“站点管理 (Site Administration)”** -> **“Actions”** -> **“Runners”**。
- 点击右上角的 **“Create new Runner”**，复制弹窗中的 **Registration Token**。

**2.3 注册 Runner**
```bash
act_runner register \
  --instance https://gitea.svc.plus \
  --token <你的_REGISTRATION_TOKEN> \
  --no-interactive
```

**2.4 守护进程化（Systemd）**
为了让它在后台持续运行开机自启：
```bash
act_runner daemon & 
# 或者更正规地注册为系统服务：
# act_runner generate-daemon > /etc/systemd/system/act_runner.service
# systemctl daemon-reload && systemctl enable --now act_runner
```
*(提示：如果要让 runner 使用容器执行任务，请确保服务器上已经安装好了 Docker)*

完成以上步骤后，你的 Gitea 仓库就可以通过在仓库根目录新建 `.gitea/workflows/` 下编写 YAML 文件来触发类似 GitHub Actions 的 CI/CD 自动化任务了！
