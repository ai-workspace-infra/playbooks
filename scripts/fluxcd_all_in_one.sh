helm repo add stable https://charts.onwalk.net
helm repo update
kubectl create namespace gitops-system || true
helm upgrade --install fluxcd stable/flux2 --version 2.12.1 -n gitops-system -f fluxcd-values.yaml

cat > cluster-config.yaml << EOF
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: GitRepository
metadata:
  name: stable
  namespace: gitops-system
spec:
  interval: 1m0s
  ref:
    branch: main
  url: https://github.com/svc-design/gitops.git
---
apiVersion: kustomize.toolkit.fluxcd.io/v1beta2
kind: Kustomization
metadata:
  name: cluster
  namespace: gitops-system
spec:
  interval: 1m0s
  sourceRef:
    kind: GitRepository
    name: stable
  path: ./clusters/k3s-local
  prune: true
EOF
