
sudo mkdir -pv /opt/rancher/k3s
curl -sfL https://rancher-mirror.rancher.cn/k3s/k3s-install.sh \
	| INSTALL_K3S_MIRROR=cn             \
	INSTALL_K3S_SKIP_SELINUX_RPM=true   \
	INSTALL_K3S_VERSION="v1.30.8+k3s1"  \
	sh -s -                             \
	--data-dir=/opt/rancher/k3s         \
	--kube-apiserver-arg service-node-port-range=0-50000          \
	--system-default-registry "registry.cn-hangzhou.aliyuncs.com" \
	--disable=traefik,servicelb
#curl -sfL https://get.k3s.io | sh -s - --disable=traefik,servicelb                                   \
#        --data-dir=/opt/rancher/k3s                                                                  \
#        --kube-apiserver-arg service-node-port-range=0-50000

sudo mkdir -pv ~/.kube/
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config

sudo snap install helm --classic


mkdir -pv /opt/rancher/k3s
curl -sfL https://get.k3s.io | sh -s - --disable=traefik,servicelb                                   \
	--data-dir=/opt/rancher/k3s                              \
        --kube-apiserver-arg service-node-port-range=0-50000     \
        --bind-address=0.0.0.0              \
        --tls-san=172.31.20.79              \
        --advertise-address=172.31.20.79    \
        --node-ip=172.31.20.79              \
        --node-external-ip 35.75.12.83      \
        --cluster-cidr 10.46.0.0/16         \
        --service-cidr 10.47.0.0/16

bash setup-k3s-agent.sh 172.23.238.167 


mkdir -pv /opt/rancher/k3s
curl -sfL https://get.k3s.io | sh -s - --disable=flannel,kube-proxy,traefik,servicelb --flannel-backend=none --disable-network-policy --kube-apiserver-arg=service-node-port-range=0-50000 --flannel-iface=br0
