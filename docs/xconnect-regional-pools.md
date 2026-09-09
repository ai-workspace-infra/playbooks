# XConnect 区域节点 Pool

区域节点采用“GitOps 非敏感声明 + Vault 连接信息 + Ansible 执行”的模型。云厂商不是调度逻辑的一部分；当前 provider/product 只记录在节点声明中，更换节点或供应商不改变区域入口契约。

| Pool | Region | 稳定入口 |
|---|---|---|
| `jp` | `jpn-tky` | `JP-XConnect.svc.plus` |
| `us` | `us-ca` | `US-XConnect.svc.plus` |
| `hk` | `hk` | `HK-XConnect.svc.plus` |
| `ph` | `ph-mnl` | `PH-XConnect.svc.plus` |

canonical 命名使用 `ph` / `ph-mnl`；历史输入 `hp` 由 GitOps alias 归一化。菲律宾当前首节点是 `ph-surfercloud-01`，其 `provider: surfercloud`、`product: ulighthost` 是可替换的声明数据。

## 职责边界

- GitOps：pool、region、FQDN、node ID、权重、角色及可选 provider/product。
- Vault：公网 IP、SSH 用户与密码等连接信息。
- Playbooks：建立临时 inventory、验证节点、安装升级以及 DNS reconcile。
- `xconnect-edge-agent`：heartbeat、健康状态、生命周期和区域内节点选择。
- Terraform：只管理有稳定原生 provider 的资源；当前手工创建的节点不纳入 Terraform 生命周期。

## 验证节点

```bash
export VAULT_TOKEN='...'
ansible-playbook -i localhost, xconnect_regional_nodes.yml
```

兼容入口 `ulighthost_xconnect.yml` 会导入同一个 provider-neutral playbook。

Vault 默认路径由 GitOps `spec.connection.vault_secret` 声明。结构如下，值仅写入 Vault：

```json
{
  "nodes": {
    "jp-01": {"public_ipv4": "<IP>", "ansible_user": "<USER>", "ansible_password": "<PASSWORD>"},
    "us-01": {"public_ipv4": "<IP>", "ansible_user": "<USER>", "ansible_password": "<PASSWORD>"},
    "hk-01": {"public_ipv4": "<IP>", "ansible_user": "<USER>", "ansible_password": "<PASSWORD>"},
    "ph-surfercloud-01": {"public_ipv4": "<IP>", "ansible_user": "<USER>", "ansible_password": "<PASSWORD>"}
  }
}
```

## Reconcile 一个区域入口

```bash
export XCONNECT_POOL=ph
export VAULT_TOKEN='...'
export CLOUDFLARE_DNS_API_TOKEN='...'
ansible-playbook -i localhost, reconcile_xconnect_entrypoint.yml
```

该操作会修改所选区域的 DNS。合并 Git 或运行节点验证 playbook 本身不会切换 DNS。
