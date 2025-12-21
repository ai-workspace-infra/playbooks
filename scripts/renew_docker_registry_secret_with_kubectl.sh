#!/bin/sh

check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

function renew_docker_registry_secret()
{

  # 检查参数是否为空
  check_not_empty "$1" "cluster" && local cluster=$1
  check_not_empty "$2" "namespace" && local namespace=$2
  check_not_empty "$3" "secret" && local secret=$3
  check_not_empty "$4" "username" && local username=$4
  check_not_empty "$5" "password" && local password=$5

  fuze k8s clusters connect $cluster && kubectl config set-context --current --namespace $namespace
  kubectl delete secret $secret -n $namespace || true
  kubectl create secret docker-registry $secret   \
  --docker-server=artifact.onwalk.net             \
  --docker-username=$username                     \
  --docker-password=$password                     \
  --docker-email=manbzuhe2009@qq.com              \
  -n $namespace

  kubectl get secret $secret -n $namespace --output="jsonpath={.data.\.dockerconfigjson}" | base64 --decode || true
}
