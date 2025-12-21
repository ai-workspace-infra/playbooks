#!/bin/bash

# 检查参数是否为空
check_not_empty() {
  if [[ -z $1 ]]; then
    echo "Error: $2 is empty. Please provide a value."
    exit 1
  fi
}

helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

# 使用 Helm 部署 Argo CD
#helm upgrade --install argocd argo/argo-cd -n argocd --create-namespace

cat <<EOF > values.yaml
global:
  domain: argocd.onwalk.net
server:
  service:
    type: NodePort
    nodePortHttp: 80
    nodePortHttps: 443
    servicePortHttp: 80
    servicePortHttps: 443
    servicePortHttpName: http
    servicePortHttpsName: https
  ingress:
    enabled: false
    ingressClassName: "nginx"
    hostname: argocd.onwalk.net
    annotations:
      nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
      nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    tls: true
repoServer:
  extraContainers:
    - name: helmfile
      image: ghcr.io/helmfile/helmfile:v0.157.0
      # Entrypoint should be Argo CD lightweight CMP server i.e. argocd-cmp-server
      command: ["/var/run/argocd/argocd-cmp-server"]
      env:
        - name: HELM_CACHE_HOME
          value: /tmp/helm/cache
        - name: HELM_CONFIG_HOME
          value: /tmp/helm/config
        - name: HELMFILE_CACHE_HOME
          value: /tmp/helmfile/cache
        - name: HELMFILE_TEMPDIR
          value: /tmp/helmfile/tmp
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
      volumeMounts:
        - mountPath: /var/run/argocd
          name: var-files
        - mountPath: /home/argocd/cmp-server/plugins
          name: plugins
        # Register helmfile plugin into sidecar
        - mountPath: /home/argocd/cmp-server/config/plugin.yaml
          subPath: helmfile.yaml
          name: argocd-cmp-cm
        # Starting with v2.4, do NOT mount the same tmp volume as the repo-server container. The filesystem separation helps mitigate path traversal attacks.
        - mountPath: /tmp
          name: helmfile-tmp
  volumes:
    - name: argocd-cmp-cm
      configMap:
        name: argocd-cmp-cm
    - name: helmfile-tmp
      emptyDir: {}
configs:
  cmp:
    create: true
    plugins:
      helmfile:
        allowConcurrency: true
        discover:
          fileName: helmfile.yaml
        generate:
          command:
            - bash
            - "-c"
            - |
              if [[ -v ENV_NAME ]]; then
                helmfile -n "$ARGOCD_APP_NAMESPACE" -e $ENV_NAME template --include-crds -q
              elif [[ -v ARGOCD_ENV_ENV_NAME ]]; then
                helmfile -n "$ARGOCD_APP_NAMESPACE" -e "$ARGOCD_ENV_ENV_NAME" template --include-crds -q
              else
                helmfile -n "$ARGOCD_APP_NAMESPACE" template --include-crds -q
              fi
        lockRepo: false
EOF

helm upgrade --install argocd argo/argo-cd -n argocd -f values.yaml

# 等待 Argo CD 完全启动
echo "Waiting for Argo CD to be ready..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=180s

echo "Argo CD deployment and configuration complete."
