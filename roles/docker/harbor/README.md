## Docker 镜像版本

| 服务        | 镜像版本                        |
|-------------|---------------------------------|
| Keycloak    | `bitnami/keycloak:26.0`         |
| PostgreSQL  | `postgres:16.0-bookworm`        |
| Nginx       | `nginx:1.27`                    |

# 目录结构
```ii
```bash
playbooks/roles/docker/keycloak
├── defaults/                      # 存放默认变量的目录
│   └── main.yml                   # 默认配置变量
├── files/                         # 存放静态文件的目录
│   └── nginx.conf                 # Nginx 配置文件
├── tasks/                         # 存放任务脚本的目录
│   ├── main.yml                   # 主要任务脚本
│   ├── post-setup.yml             # 部署后设置任务
│   ├── pre-setup.yml              # 部署前设置任务
├── templates/                     # 存放模板文件的目录
│   ├── create_keystore.sh.j2      # 创建 Keystore 和 Truststore 的脚本模板
│   └── docker-compose.yml.j2      # Docker Compose 配置文件模板
└── README.md                      # 项目说明文件
```bash
````````
使用 Ansible 部署前的准备
在运行 Playbook 之前，请确保以下准备工作已完成：

1. 主机准备
操作系统要求：本 Playbook 适用于 Ubuntu 20.04 及以上版本的主机。

主机要求：确保主机上已安装 Docker、Docker Compose 和 Ansible。你可以通过以下命令安装这些工具：

bash
复制代码
# 安装 Docker 和 Docker Compose
sudo apt-get update
sudo apt-get install docker.io docker-compose
主机名称：确保主机名称已正确设置，并且该主机可以访问 DNS 配置的域名。

2. 域名和 SSL 证书
域名：确保你已经为 Keycloak 设置了域名（例如 keycloak.onwalk.net）。在实际部署前，你需要准备一个有效的域名和 SSL 证书。可以使用 Let’s Encrypt 或其他证书颁发机构获取证书。

证书文件：准备好 SSL 证书（如 onwalk.net.pem）和 SSL 密钥文件（如 onwalk.net.key）。这两个文件将用于配置 Keycloak 和 Nginx 服务的 HTTPS 访问。

证书路径应为 /etc/ssl/onwalk.net.pem，密钥路径应为 /etc/ssl/onwalk.net.key。

3. Ansible 配置文件（如果需要）
根据需要，你可以创建一个 inventory.ini 文件来指定部署目标主机：

ini
复制代码
[servers]
your_server_ip_or_hostname ansible_ssh_user=your_user ansible_ssh_private_key_file=your_key

# Ansible Playbook 执行和部署


1. 克隆仓库
首先，克隆该仓库到你的本地机器：

bash
复制代码
git clone https://your_repository_url.git
cd ansible-playbook

2. 测试执行
ansible-playbook -i inventory/k3s-cluster playbooks/deploy-docker-harbor.yml -l cn-hw-node.svc.plus -D -C

2. 执行部署
执行部署任务时，使用以下命令来运行 Ansible Playbook：

ansible-playbook -i inventory.ini playbooks/deploy-docker-keycloak.yml -l cn-gateway.svc.plus -D

此命令将会启动以下步骤：

- 安装并配置 Docker 和 Docker Compose。
- 创建所需的 Keystore 和 Truststore 文件。
- 启动 Keycloak 和 PostgreSQL 容器，Nginx 容器

3. 验证部署
部署完成后，你可以通过以下命令检查 Keycloak 和 PostgreSQL 服务是否正常运行：

docker ps -q -f name=postgres
docker ps -q -f name=keycloak
docker ps -q -f name=nginx

如果服务正常运行，则会显示容器的 ID。

部署后的配置
1. DNS 配置
确保你的域名（如 keycloak.onwalk.net）已正确解析，并且指向部署 Keycloak 的主机。你可以使用 nslookup 或 dig 工具验证 DNS 解析：


## defaults/main.yml encrypt_string

ansible-vault encrypt_string 'xxxxx' --name 'core_secret'

