#!/bin/bash
set -x
export domain=$1
export secret=$2
export namespace=$3
export mysql_db_password=$4

cat > values.yaml << EOF

controller:
  agentListenerServiceType: "NodePort"
  agentListenerNodePort: 50000
  admin:
    username: 'admin'
    password: "jenkins"
  jenkinsUrlProtocol: "https"
  jenkinsHome: "/var/jenkins_home"
  jenkinsUrl: https://jenkins.$domain
  ingress:
    enabled: true
    annotations:
      kubernetes.io/tls-acme: "false"
    ingressClassName: nginx
    hostName: jenkins.$domain
    path: '/'
    tls:
      - secretName: $secret
        hosts:
          - jenkins.$domain
  installLatestPlugins: true
  installPlugins:
    - git:5.2.1
    - github:1.38.0
    - github-pullrequest:0.7.0
    - locale:314.v22ce953dfe9e
    - database-mysql:1.4
    - database:191.vd5981b_97a_5fa_
    - credentials:1337.v60b_d7b_c7b_c9f
    - credentials-binding:642.v737c34dea_6c2  # 更新版本以满足依赖关系
    - configuration-as-code:1775.v810dc950b_514  # 更新版本以满足依赖关系
    - gitlab-plugin:1.7.16
    - kubernetes:4029.v5712230ccb_f8
    - docker-plugin:1.6
    - docker-workflow:572.v950f58993843
    - docker-commons:439.va_3cb_0a_6a_fb_29
    - pipeline-stage-view:2.33
    - workflow-job:1385.vb_58b_86ea_fff1
    - workflow-cps:3883.vb_3ff2a_e3eea_f
    - workflow-aggregator:596.v8c21c963d92d
  JCasC:
    enabled: true
    defaultConfig: true
    configScripts:
      database: |
        unclassified:
          globalDatabaseConfiguration:
            database:
              mysql:
                hostname: mysql.database.svc.cluster.local
                username: "root"
                database: "jenkins"
                password: $mysql_db_password
                properties: "?useSSL=false"
                validationQuery: "SELECT 1"
agent:
  enabled: true
  replicas: 3
  numExecutors: 1
  jenkinsUrl: https://jenkins.$domain
  image:
    repository: "jenkins/inbound-agent"
    tag: "latest"
  customJenkinsLabels: []

persistence:
  enabled: true
  storageClass: "local-path"
  size: "10Gi"
networkPolicy:
  enabled: false
additionalConfig: {}
EOF

helm repo add jenkins https://charts.jenkins.io
helm repo update
helm upgrade --install jenkins jenkins/jenkins -n $namespace --create-namespace -f values.yaml
