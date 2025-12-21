helm repo add cilium https://helm.cilium.io && helm repo update
helm repo up

#helm upgrade --install cilium-preflight cilium/cilium --version 1.17.3   --namespace=kube-system   --set preflight.enabled=true   --set agent=false   --set operator.enabled=false

cat <<EOF >cilium-egress-values.yaml
# cilium-values.yaml
routingMode: native
k8sServiceHost: 10.253.253.1
k8sServicePort: 6443
ipv4NativeRoutingCIDR: "10.42.0.0/16"
ipam:
  mode: kubernetes
  operator:
    clusterPoolIPv4PodCIDRList: "10.42.0.0/16"
egressGateway:
  enabled: true
  installRoutes: true
enableIPv4Masquerade: true
autoDirectNodeRoutes: true
nodePort:
  enabled: true
  directRoutingDevice: wg0
bpf:
  masquerade: true
kubeProxyReplacement: true
endpointRoutes:
  enabled: true
cni:
  exclusive: true
envoy:
  enabled: false
l7Proxy: true
proxy:
  enabled: false
hubble:
  enabled: false

# 必须保留的 Operator（用于 CRD 处理与 egress gateway 控制）
operator:
  enabled: true
  skipCRDCreation: false
  replicas: 1
  resources:
    requests:
      cpu: 20m
      memory: 30Mi
    limits:
      cpu: 100m
      memory: 128Mi

# 主 Agent 资源限制（可根据机器微调）
resources:
  requests:
    cpu: 20m
    memory: 50Mi
  limits:
    cpu: 100m
    memory: 128Mi
EOF

helm upgrade --install cilium cilium/cilium -n kube-system --set installCRDs=true -f cilium-egress-values.yaml --wait
kubectl label node $(hostname) egress-gateway=true --overwrite
echo "✅ Cilium 安装完成"

cat >> NodeConfig-cn-hub.yaml << EOF
apiVersion: cilium.io/v2alpha1
kind: CiliumNodeConfig
metadata:
  name: config-for-cn-hub
spec:
  nodeSelector:
    matchLabels:
      kubernetes.io/hostname: cn-hub.svc.plus
  defaults:
    directRoutingDevice: "eth0"
EOF

#kubectl apply -f NodeConfig-cn-hub.yaml -n kube-system
