#!/bin/bash

# 检查参数是否为空
check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

check_not_empty "$1" "ak"             && export ak=$1
check_not_empty "$2" "sk"             && export sk=$2
check_not_empty "$3" "domain"         && export domain=$3
check_not_empty "$4" "namespace"      && export namespace=$4
check_not_empty "$5" "secret_name"   && export secret_name=$5
check_not_empty "$6" "redis_password" && export redis_password=$6
check_not_empty "$7" "pg_db_password" && export pg_db_password=$7
check_not_empty "$8" "backend_type"   && export backend_type=$8
                                         export registry=$9

cat > values.yaml << EOF
global:
  imageRegistry: "$registry"
exposureType: ingress
ingress:
  core:
    ingressClassName: "nginx"
    hostname: images.${domain}
    extraTls:
    - hosts:
        - images.${domain}
      secretName: "$secret_name"
externalURL: https://images.${domain}

postgresql:
  enabled: false
redis:
  enabled: false
notary:
  enabled: false
trivy:
  enabled: false

externalDatabase:
  host: postgresql.database.svc.cluster.local
  user: postgres
  port: 5432
  password: "$pg_db_password"
  sslmode: disable
  coreDatabase: harbor_core
  clairDatabase: harbor_clair
  clairUsername: "postgres"
  clairPassword: "$pg_db_password"
  notaryServerDatabase: harbor_notary_server
  notaryServerUsername: "postgres"
  notaryServerPassword: "$pg_db_password"
  notarySignerDatabase: harbor_notary_signer
  notarySignerUsername: "postgres"
  notarySignerPassword: "$pg_db_password"
externalRedis:
  host: redis-master.redis.svc.cluster.local
  port: 6379
  password: "$redis_password"
persistence:
  enabled: true
  imageChartStorage:
    type: $backend_type
    oss:
      accesskeyid: $ak
      accesskeysecret: $sk
      region: "oss-cn-wulanchabu"
      bucket: "harbor-oss"
      endpoint: "oss-cn-wulanchabu.aliyuncs.com"
    s3:
      region: ap-east-1
      bucket: artifact-s3
      accesskey: $ak
      secretkey: $sk
EOF

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update bitnami
kubectl create ns $namespace || true
helm upgrade --install artifact bitnami/harbor --version=16.7.0 -f values.yaml -n $namespace
