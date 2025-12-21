
#!/bin/sh

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

function verify_docker_registry_secret()
{

  # 检查参数是否为空
  check_not_empty "$1" "cluster" && local cluster=$1
  check_not_empty "$2" "namespace" && local namespace=$2
  check_not_empty "$3" "secret" && local secret=$3

  kubectl config set-context --current --namespace $namespace
  kubectl get secret $secret -n $namespace --output="jsonpath={.data.\.dockerconfigjson}" | base64 --decode || true

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: image-pull-test
  namespace: $namespace
spec:
  containers:
  - name: image-pull-test
    image: private.artifact.onwalk.net/nginx:stable-alpine
    imagePullPolicy: Always
    resources:
      limits:
        cpu: 200m
        memory: 200Mi
      requests:
        cpu: 100m
        memory: 100Mi
  imagePullSecrets:
  - name: $secret
EOF

# 等待 pod 运行成功

sleep 5

#  确认pod 镜像拉取成功
kubectl get pods image-pull-test -n $namespace
if [[ $? == 0 ]]; then
  echo -e "${GREEN}image-pull-test is PASS ${NC}"
else
  echo -e "${RED}image-pull-test is Faild${NC}"
fi

echo -e "clean up image-pull-test pod$"
kubectl delete pods image-pull-test -n $namespace || true

}
