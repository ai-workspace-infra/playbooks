API_SERVER_IP=172.30.0.1
# Kubeadm default is 6443
API_SERVER_PORT=6443
helm upgrade --install cilium cilium/cilium --version 1.17.3 \
    --namespace kube-system  \
    --set routingMode=native \
    --set autoDirectNodeRoutes=true \
    --set ipv4NativeRoutingCIDR="10.42.0.0/16" \
    --set ipam.mode=kubernetes \
    --set ipam.operator.clusterPoolIPv4PodCIDRList="10.42.0.0/16" \
    --set kubeProxyReplacement=true \
    --set k8sServiceHost=${API_SERVER_IP} \
    --set k8sServicePort=${API_SERVER_PORT} \
    --set nodePort.enabled=true \
    --set nodePort.directRoutingDevice=wg0 \
    --set envoy.enabled=false              \
    --set operator.skipCRDCreation=false \
    --set operator.replicas=1 \
    --set egressGateway.enabled=true \
    --set egressGateway.installRoutes=true \
    --set bpf.masquerade=true \
    --set enableIPv4Masquerade=true \
    --set masquerade=true

kubectl rollout restart ds cilium -n kube-system
kubectl rollout restart deploy cilium-operator -n kube-system

kubectl label nodes cn-hub.svc.plus egress-node=true
