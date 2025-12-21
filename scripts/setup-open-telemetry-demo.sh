helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update
kubectl create ns otel || true
kubectl delete secret tls otel-demo-secret -n otel || true 
kubectl create secret tls otel-demo-secret --key=/etc/ssl/onwalk.net.key  --cert=/etc/ssl/onwalk.net.pem -n otel || true
cat > otel-demo-config.yaml << EOF
default:
  image:
    repository: images.onwalk.net/public/opentelemetry/demo
    tag: ""
    pullPolicy: IfNotPresent
components:
  accountingService:
    enabled: true
    initContainers:
      - name: wait-for-kafka
        image: images.onwalk.net/public/base/busybox:latest
  adService:
    enabled: true
  cartService:
    enabled: true
    initContainers:
      - name: wait-for-valkey
        image: images.onwalk.net/public/base/busybox:latest
  checkoutService:
    enabled: true
    initContainers:
      - name: wait-for-kafka
        image: images.onwalk.net/public/base/busybox:latest
  currencyService:
    enabled: true
  emailService:
    enabled: true
  frauddetectionService:
    enabled: true
    initContainers:
      - name: wait-for-kafka
        image: images.onwalk.net/public/base/busybox:latest
  frontend:
    enabled: true
  frontendProxy:
    enabled: true
    ingress:
      enabled: true
      ingressClassName: nginx
      hosts:
        - host: otel-demo.onwalk.net
          paths:
            - path: /
              pathType: Prefix
              port: 8080
            - path: /jaeger/ui/
              pathType: Prefix
              port: 8080
            - path: /grafana/
              pathType: Prefix
              port: 8080
            - path: /loadgen/
              pathType: Prefix
              port: 8080
            - path: /feature/
              pathType: Prefix
              port: 8080
      tls:
        - secretName: otel-demo-secret
          hosts:
            - otel-demo.onwalk.net
  imageprovider:
    enabled: true
  loadgenerator:
    enabled: true
  paymentService:
    enabled: true
  productCatalogService:
    enabled: true
  quoteService:
    enabled: true
  recommendationService:
    enabled: true
  shippingService:
    enabled: true
  flagd:
    enabled: false
    imageOverride:
      repository: "ghcr.io/open-feature/flagd"
      tag: "v0.11.4"
    initContainers:
      - name: init-config
        image: images.onwalk.net/public/base/busybox:latest
  kafka:
    enabled: true
  valkey:
    enabled: true
    imageOverride:
      repository: "images.onwalk.net/public/opentelemetry/valkey"
      tag: "7.2-alpine"
grafana:
  enabled: true
  global:
    imageRegistry: images.onwalk.net/public
prometheus:
  enabled: true
jaeger:
  enabled: true
  allInOne:
    image:
      repository: "images.onwalk.net/public/jaegertracing/all-in-one"
      tag: "1.53.0"
opentelemetry-collector:
  enabled: true
  image:
    repository: "images.onwalk.net/public/opentelemetry/opentelemetry-collector-contrib"
opensearch:
  enabled: false
EOF
helm upgrade --install otel-demo open-telemetry/opentelemetry-demo --version=0.33.3 -n otel -f otel-demo-config.yaml
