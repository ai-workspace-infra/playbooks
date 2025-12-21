#!/bin/sh

check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

function run() {
  check_not_empty "$1" "cluster" && local cluster=$1
  check_not_empty "$2" "namespace" && local namespace=$2

  kubectl config set-context --current --namespace $namespace

  for secret in $(kubectl get secrets -n $namespace -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '\.dockerconfigjson$'); do
    echo "$cluster $namespace $secret"
  done
}

function print_base64_data() {
  local namespace=$1
  local secret=$2
  local cluster=$3
  echo "$cluster $namespace $secret"
  kubectl get secret $secret -n $namespace --output="jsonpath={.data.\.dockerconfigjson}" | base64 --decode || true
}

cluster="$1"
namespace="$2"

run "$cluster" "$namespace"
print_base64_data "$namespace" "$secret" "$cluster"
