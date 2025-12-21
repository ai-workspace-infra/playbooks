helm repo add gitea https://dl.gitea.com/charts
helm repo update
kubectl create ns gitea || true
cat > gitea-values.yaml <<EOF
gitea:
  admin:
    username: admin
    password: 'xxxx'
    email: "gitea@local.domain"
    passwordMode: keepUpdated
# 关闭 cloud 风格的数据库和 Redis 集群
redis-cluster:
  enabled: false

postgresql-ha:
  enabled: false

# 启用单实例 Redis 和 PostgreSQL
redis:
  enabled: true
  architecture: standalone
  global:
    redis:
      password: changeme
  master:
    count: 1

postgresql:
  enabled: true
  global:
    postgresql:
      auth:
        username: gitea
        password: gitea
        database: gitea
      service:
        ports:
          postgresql: 5432
  primary:
    persistence:
      size: 5Gi

# 使用本地默认 StorageClass，如 K3s 的 local-path
persistence:
  enabled: true
  storageClass: ""  # "" 表示使用默认 storageClass
  size: 10Gi

# 启用 NodePort，适配无云 LoadBalancer 的集群
service:
  http:
    type: NodePort
    nodePort: 31080
  ssh:
    type: NodePort
    nodePort: 31022

# 可选：关闭 Ingress
ingress:
  enabled: false

# 设置资源限制
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

# 设置 Gitea 管理员账户
gitea:
  admin:
    username: giteaadmin
    password: changeme
    email: gitea@example.com
    passwordMode: keepUpdated

# 启用 Actions 功能
actions:
  enabled: false
EOF
helm upgrade --install gitea ./gitea -n gitea -f gitea-values.yaml
