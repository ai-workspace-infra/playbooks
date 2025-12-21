#!/bin/bash

set -e

CHART_DIR="update-server"
mkdir -p "$CHART_DIR/templates"

# Chart.yaml
cat > "$CHART_DIR/Chart.yaml" <<EOF
apiVersion: v2
name: update-server
description: Simple nginx-based update server (hostPath edition)
version: 0.1.0
EOF

# values.yaml
cat > "$CHART_DIR/values.yaml" <<EOF
image:
  repository: nginx
  tag: stable

domain: artifact.onwalk.net
pathPrefix: /

storage:
  mountPath: /usr/share/nginx/html
  hostPath: /mnt/data/update-server
EOF

# deployment.yaml
cat > "$CHART_DIR/templates/deployment.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: update-server
  labels:
    app: update-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: update-server
  template:
    metadata:
      labels:
        app: update-server
    spec:
      containers:
        - name: nginx
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: 80
          volumeMounts:
            - name: update-volume
              mountPath: {{ .Values.storage.mountPath }}
            - name: nginx-conf
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: nginx.conf
      volumes:
        - name: update-volume
          hostPath:
            path: {{ .Values.storage.hostPath }}
            type: DirectoryOrCreate
        - name: nginx-conf
          configMap:
            name: update-nginx-config
EOF

# service.yaml
cat > "$CHART_DIR/templates/service.yaml" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: update-server
spec:
  selector:
    app: update-server
  ports:
    - port: 80
      targetPort: 80
EOF

# configmap.yaml
cat > "$CHART_DIR/templates/configmap.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: update-nginx-config
data:
  nginx.conf: |
    server {
      listen 80;
      server_name localhost;
      root {{ .Values.storage.mountPath }};
      index index.html;
      autoindex on;
      location / {
        autoindex_exact_size off;
        autoindex_localtime on;
        try_files \$uri \$uri/ =404;
      }
    }
EOF

# route.yaml
cat > "$CHART_DIR/templates/route.yaml" <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: update-route
  namespace: default
spec:
  parentRefs:
    - name: example-gateway
      namespace: kong
      group: gateway.networking.k8s.io
      kind: Gateway
  hostnames:
    - {{ .Values.domain }}
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: {{ .Values.pathPrefix }}
      backendRefs:
        - name: update-server
          port: 80
EOF

echo "✅ update-server Helm Chart 初始化完成！"
echo "➡️ 使用方法："
echo "   helm install update-server ./update-server"
echo "   或"
echo "   helm upgrade --install update-server ./update-server"
