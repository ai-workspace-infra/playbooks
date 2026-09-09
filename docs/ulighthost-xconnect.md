# ULightHost 固定出口节点

ULightHost 节点采用手工创建、Vault 保存连接信息、Ansible 动态连接的方式，不纳入 Terraform/IAC 生命周期。

四个区域 pool 与上层入口：

| Pool | Region | 上层入口 |
|---|---|---|
| `jp` | `jpn-tky` | `JP-XConnect.svc.plus` |
| `us` | `us-ca` | `US-XConnect.svc.plus` |
| `hk` | `hk` | `HK-XConnect.svc.plus` |
| `ph` | `ph-mnl` | `PH-XConnect.svc.plus` |

菲律宾当前首节点为 `ph-surfercloud-01`，provider/product 声明为
`surfercloud` / `ulighthost`；SurferCloud ULightHost 仍采用手工 provisioning。
canonical 命名使用 `ph` / `ph-mnl`，历史输入 `hp` 在 pool 选择时兼容归一化到
`ph` / `ph-mnl`。

JP DNS 采用两层关系：`JP-XConnect.svc.plus` 以 A 记录直接绑定 `jp-01` 的 EIP，作为与 `agent-proxy-selfhost-prod-jp.svc.plus` 平级的东京入口；`tky-proxy.svc.plus` 仅保留为 `CNAME JP-XConnect.svc.plus` 的历史兼容别名。

使用受控 Vault 与 Cloudflare 凭证后，可同时写入这两条记录：

```bash
export XCONNECT_POOL=jp
export VAULT_TOKEN='...'
export CLOUDFLARE_DNS_API_TOKEN='...'
ansible-playbook -i localhost, reconcile_xconnect_entrypoint.yml
```

入口绑定规则：

- pool 只有一个节点时，入口域名直接绑定该节点。
- pool 增加节点后，入口域名绑定 `scheduler_node`；其余节点标记为 `pool_worker`，由调度节点转发/调度。
- 节点扩容只增加 pool 的 `nodes` 和 Vault 凭证，不改变区域入口域名。

GitOps 中使用 `entrypoint.mode: auto` 表达上述规则；当前四个 pool 都是单节点 `standalone` 状态，后续扩容时将原节点改为 `scheduler`，新增节点标记为 `pool_worker`。

GitOps 负责约束入口目标与 worker 集合；调度实现由 `git@github.com:ai-workspace-xstream/xconnect-edge-agent.git` 提供，并且只在多节点时接入 `ulighthost_scheduler` 组，避免在只有一个节点时引入额外代理层。

ULightHost 由控制台手工创建，采用标准 `1C1G / 40GB / 30Mbps / 200GB` 套餐，标价 ¥34/月/台。每个区域的所有节点都登记在对应 pool 下，调度器按健康状态和权重选择 pool 内节点；扩容时只需在 GitOps pool 增加节点声明，并在 Vault 增加同名凭证。创建后只将连接凭证写入 Vault；不要把以下字段写入 Git：

```json
{
  "nodes": {
    "jp-01": {"public_ipv4": "<IP>", "ansible_user": "root", "ansible_password": "<password>"},
    "us-01": {"public_ipv4": "<IP>", "ansible_user": "root", "ansible_password": "<password>"},
    "hk-01": {"public_ipv4": "<IP>", "ansible_user": "root", "ansible_password": "<password>"},
    "ph-surfercloud-01": {"public_ipv4": "<IP>", "ansible_user": "root", "ansible_password": "<password>"}
  }
}
```

默认写入路径为 `kv/prod/ulighthost-xconnect`。连接入口见 [ulighthost_xconnect.yml](../ulighthost_xconnect.yml)。它从 GitOps 读取节点的 Region、pool、入口和角色，从 Vault 读取 IP、用户名、密码，只在内存中生成临时 inventory，随后可以在第二个 play 中继续加入节点配置 role。
