# XConnect 混合区域节点

XConnect 支持两类节点：Terraform 创建的 AWS 节点从部署产生的 CMDB 获取当前 IP 和 SSH 用户；手工创建的节点由 GitOps 保存公开连接元数据，并从 Vault 读取实际存在的敏感凭据。两类节点进入同一个区域 inventory 和 DNS reconcile 流程。

| Pool | Region | 入口 | 来源与生命周期 |
|---|---|---|---|
| `jp` | `jpn-tky` | `JP-XConnect.svc.plus` | AWS EIP / 持续 |
| `ph` | `ph-mnl` | `PH-XConnect.svc.plus` | 手工 Ubuntu / 持续 |
| `hk` | `hk` | `HK-XConnect.svc.plus` | AWS Spot / 1 小时 |
| `us` | `us-ca` | `US-XConnect.svc.plus` | AWS Spot / 1 小时 |

GitOps 中只保存区域、入口、生命周期、节点标识和公开 SSH 元数据。AWS 节点使用稳定的 CMDB `node_id` 关联当前 Terraform 运行时 IP；PH 使用 `ansible_host` 和 `ansible_user`。所有密码和私钥均不进入 Git。

PH 的密码只保存于 `kv/data/prod/XConnect/PH`：

```json
{
  "SSH_PASSWORD": "<PH SSH password>"
}
```

部署时必须提供 Terraform CMDB 与临时部署私钥，PH 另外需要可读取上述 Vault 路径的 token：

```bash
export XCONNECT_CMDB_FILE=/path/to/cmdb.json
export XCONNECT_DEPLOY_KEY_FILE=/path/to/deploy-key
export VAULT_TOKEN='...'
ansible-playbook -i localhost, ulighthost_xconnect.yml
```

Use `deploy_xconnect_regional.yml` to apply the existing Agent Proxy and
exporter roles after the hybrid inventory has been built. To deploy PH only,
pass `--limit 'localhost,xconnect_pool_ph'`; this keeps the regional rollout
independent from the AWS node lifecycle.

要更新一个区域入口 DNS，指定 `XCONNECT_POOL`。JP 保留 `tky-proxy.svc.plus` 到 `JP-XConnect.svc.plus` 的历史 CNAME。HK 和 US 的 Spot 实例创建后会由 CMDB 提供当前地址；实例回收后必须先从 DNS 与节点注册中移除，避免客户端选择过期节点。
