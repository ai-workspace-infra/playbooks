#git clone https://github.com/aliyun/alibabacloud-microservice-demo.git
kubectl create ns microservice-demo || true
kubectl delete secret tls otel-demo-secret -n microservice-demo || true 
kubectl create secret tls otel-demo-secret --key=/etc/ssl/onwalk.net.key  --cert=/etc/ssl/onwalk.net.pem -n microservice-demo || true
cat > microservice-demo-config.yaml << EOF
image:
  prefix: images.onwalk.net/public/microservice-demo/ 
  version: 1.0.0-SNAPSHOT
EOF
helm package alibabacloud-microservice-demo/helm-chart/
helm upgrade --install microservice-demo /root/microservice-demo-0.1.0.tgz -n microservice-demo -f microservice-demo-config.yaml
