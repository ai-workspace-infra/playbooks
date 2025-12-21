#!/bin/sh

check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

function backup_docker_registry_secret()
{

  # 检查参数是否为空
  check_not_empty "$1" "cluster" && local cluster=$1
  check_not_empty "$2" "namespace" && local namespace=$2
  check_not_empty "$3" "secret" && local secret=$3

  mkdir -pv ~/Backups/
  kubectl config set-context --current --namespace $namespace
  kubectl get secret $secret -n $namespace -o yaml > ~/Backups/$cluster-$namespace-$secret.yaml
}
