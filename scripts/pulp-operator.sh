helm repo add pulp-operator https://github.com/pulp/pulp-k8s-resources/raw/main/helm-charts/ --force-update
helm repo up
kubectl create ns pulp
kubectl config set-context --current --namespace pulp
helm -n pulp upgrade --install pulp pulp-operator/pulp-operator
