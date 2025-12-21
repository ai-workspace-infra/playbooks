
# 新建部署目录，并解压安装包到该目录

mkdir /opt/k8s-deploy && tar -xvpf sealos-amd64-k8s-1.25.16.tar.gz -C /opt/k8s-deploy
cd /opt/k8s-deploy/                        && \
cp sealos helm calicoctl nerdctl /usr/bin/ && \
chmod +x /usr/bin/sealos /usr/bin/helm /usr/bin/calicoctl /usr/bin/nerdctl

# 导入离线镜像
sealos load -i sealos-calico.tar
sealos load -i sealos-helm.tar
sealos load -i sealos-k8s-1.25.16.tar

# 单机部署(单机部署无需ssh密码，root用户本机直接执行即可)
# 根据节点 IP 所在地区自动选择拉取镜像的仓库
REGISTRY_PREFIX=$(dirname "$0")/../playbooks/roles/vhosts/gpu-k8s/files/get_labring_registry.sh
REGISTRY_PREFIX=$("$REGISTRY_PREFIX")
sealos run \
    ${REGISTRY_PREFIX}/kubernetes:v1.25.16  \
    ${REGISTRY_PREFIX}/helm:v3.9.4          \
    ${REGISTRY_PREFIX}/calico:v3.24.1       \
    --single
