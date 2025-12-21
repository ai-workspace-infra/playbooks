helm repo add harbor https://helm.goharbor.io
helm repo update
kubectl create ns harbor || true
kubectl create secret tls harbor-secret --key=/etc/ssl/onwalk.net.key  --cert=/etc/ssl/onwalk.net.pem -n harbor
cat > harbor-arm-config.yaml << EOF 
expose:
  type: ingress
  tls:
    enabled: true
    certSource: secret
    secret:
      secretName: harbor-secret
      notarySecretName: harbor-secret
  ingress:
    hosts:
      core: harbor.onwalk.net
      notary: artifact-notary.onwalk.net
    className: "nginx"
externalURL: https://harbor.onwalk.net
nginx:
  image:
    repository: images.onwalk.net/public/goharbor/nginx-photon
    tag: v2.12.0
portal:
  image:
    repository: images.onwalk.net/public/goharbor/harbor-portal
    tag: v2.12.0
core:
  image:
    repository: images.onwalk.net/public/goharbor/harbor-core
    tag: v2.12.0
jobservice:
  image:
    repository: images.onwalk.net/public/goharbor/harbor-jobservice
    tag: v2.12.0
registry:
  registry:
    image:
      repository: images.onwalk.net/public/goharbor/registry-photon
      tag: v2.12.0
  controller:
    image:
      repository: images.onwalk.net/public/goharbor/harbor-registryctl
      tag: v2.12.0
trivy:
  enabled: true
  image:
    repository: images.onwalk.net/public/goharbor/trivy-adapter-photon
    tag: v2.12.0
database:
  type: internal
  internal:
    image:
      repository: images.onwalk.net/public/goharbor/harbor-db
      tag: v2.12.0
redis:
  type: internal
  internal:
    image:
      repository: images.onwalk.net/public/goharbor/redis-photon
      tag: v2.12.0
exporter:
  image:
    repository: images.onwalk.net/public/goharbor/harbor-exporter
EOF
helm upgrade --install harbor harbor/harbor -f harbor-arm-config.yaml -n harbor
