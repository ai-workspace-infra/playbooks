kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.1.0/standard-install.yaml

helm repo add kong https://charts.konghq.com
helm repo update
cat > kong-values.yaml <<EOF
kong:
  secretVolumes:
    - onwalk-tls
  env:
    ssl_cert: /etc/secrets/onwalk-tls/tls.crt
    ssl_cert_key: /etc/secrets/onwalk-tls/tls.key
EOF

kubectl create ns kong || true
kubectl create secret tls onwalk-tls --cert=/etc/ssl/onwalk.net.pem --key=/etc/ssl/onwalk.net.key -n kong
helm upgrade --install kong kong/ingress -n kong --create-namespace -f kong-values.yaml

kubectl patch svc kong-gateway-proxy -n kong \
  --type='merge' \
  -p '{
    "spec": {
      "type": "NodePort",
      "ports": [
        {
          "port": 80,
          "targetPort": 8000,
          "protocol": "TCP",
          "name": "http",
          "nodePort": 80
        },
        {
          "port": 443,
          "targetPort": 8443,
          "protocol": "TCP",
          "name": "https",
          "nodePort": 443
        }
      ]
    }
  }'

 kubectl patch svc kong-gateway-proxy -n kong \
  --type='merge' \
  -p '{
    "spec": {
      "externalIPs": [
        "47.120.61.35"
      ]
    }
  }'

 kubectl patch deployment kong-gateway -n kong \
  --type='merge' \
  -p '{
    "spec": {
      "template": {
        "spec": {
          "nodeName": "icp-aliyun.svc.plus"
        }
      }
    }
  }'

echo "
---
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
 name: kong
 annotations:
   konghq.com/gatewayclass-unmanaged: 'true'

spec:
 controllerName: konghq.com/kic-gateway-controller
" | kubectl apply -f -

kubectl label nodes icp-aliyun.svc.plus ingress-node=true
