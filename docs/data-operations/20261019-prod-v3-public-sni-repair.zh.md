# V3 公共 SNI 证书恢复与发布验收

验证时间：2026-10-19 19:56（Asia/Shanghai）。范围仅 `web-saas-prod-v3`。

内部 V3 别名正常，但公共 SNI 直连 V3 返回 TLS internal error。原因是恢复代际
Caddyfile 缺少公共站点，容器未挂载 Vault 证书目录。

## 固定发布与归属

- 源/目标代际：不执行数据迁移；仅更新 prod-v3 入口。
- Release：`prod-v3-public-sni-2026.10.19-r1`。
- GitOps SHA：`87622bb82ff148063c5b6405182eeec8110b1e1f`（PR #472）。
- Playbooks SHA：`8b0c864cd05bfaad5b503f7a3b6679a5de407caa`（PR #693）。
- Caddy/回退镜像 digest：`sha256:9895385033fea2bdb445841b45f37e6cd58b9e071a16e4c147e483abb74a0acf`，镜像未更换。
- SQL checksum、DB 备份恢复、数据指纹：本次不适用，未执行数据库写入/迁移。
- 只读确认原有 checkpoint：`2026101902 / dirty=false`。
- 发布方式：SSH → Playbooks → GitOps → Doco-CD。GitHub Actions 仅执行 PR 检查。

## 执行与证据

控制端只读 `kv/CICD/domains/svc.plus`，运行时解码五个 `tls_*_pem_b64` 字段。
秘密只通过权限 0600 的临时 extra-vars 文件传入，调用结束清理该文件。
复用 `docker/caddy_certificate_restore` 校验证书、私钥和有效期，原子发布不可变版本。
证书覆盖 `*.svc.plus` 和 `svc.plus`，到期时间为北京时间 2027-01-06 07:55:40。

`web_saas_public_ingress` Role 校验 IaC 主机与实际 hostname、三个公共 SAN，保留
已有内部站点，幂等添加公共站点。Accounts 设置 `X-Forwarded-Host: svc.plus`；
Billing 仅开放现有只读健康探针，其余请求保持 403。

配置在无网络的相同 Caddy 镜像中验证，再备份并发布。第二次执行证书版本、公共
站点块和最终 Caddyfile 均未改变；仅产生并清理临时证书校验目录。

GitOps 将 `/etc/xcontrol/tls` 只读挂载到容器相同路径。Doco-CD 已对账到上述 tag/SHA。
只有 Caddy 容器更换；Accounts、Billing、Console、PostgreSQL 的容器 ID、启动时间、
镜像 digest 保持一致。五个容器 restart count 均为 0。发布选择过程重建了 Doco-CD 控制器。

最终 Caddyfile SHA-256：`49240326812e61f5af2c169ad69de4412a0a1bf01e9da41cd0ca9bbfaba94448`。
旧文件保存在主机 `/etc/xcontrol/web-saas-recovery/Caddyfile.666505.2026-10-19@11:52:35~`。

| 直连 V3 的公共 SNI | 结果 | CA 校验 |
| --- | --- | --- |
| console.svc.plus/panel | 307 | 通过 |
| accounts.svc.plus/readyz | 200，database=ok，status=ready | 通过 |
| billing.svc.plus/readyz | 200，database=ok，status=ready | 通过 |

使用 `curl --resolve <公共域名>:443:<IaC目标IP>`，未跳过证书验证。三个 V3 内部
别名同样通过（307/200/200）。当时三个公共 CNAME 仍指向 V2，本次未修改 DNS。
PostgreSQL 别名保持预留隧道 TLS 用途，无新增数据库公网监听。

## 后续代际复用与回退

每代从 GitOps `compose/web-saas/ingress/prod.yaml` 读取共享公共域名与证书引用。
通过 `web-saas-public-ingress.yml` 传入目标代际、单个 IaC inventory 目标、受保护
证书运行时变量，完成恢复及配置校验；随后选择挂载证书目录的不可变 GitOps tag，
最后执行公共 SNI 和内部别名验收。Caddy Cloudflare 模块可选，公共站点直接使用 Vault PEM。

若回退到上一 GitOps tag（r2），必须先通过受控 Playbook 恢复上述旧 Caddyfile，
再切换旧 tag，因为旧 Compose 没有证书挂载；不能只切旧 tag 而保留引用新路径的配置。
保留证书版本、配置备份和独立数据盘，不自动回退数据库。
