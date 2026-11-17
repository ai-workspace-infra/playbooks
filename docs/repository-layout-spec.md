# playbooks 仓库目录与角色分层规范

本规范定义 `playbooks` 仓库的目录结构与 Role 归属规则，是 `config-as-code-Spec` 的补充。
合并门禁由 `tests/test_repository_layout.py` 与 `.github/workflows/repository-layout-contract.yml` 强制执行。

## 1. 顶层目录职责

| 路径 | 职责 |
| --- | --- |
| `*.yml` / `*.yaml` | **通用入口 playbook**，一个文件 = 一个可执行场景 |
| `inventory/` | 动态清单：`terraform_cmdb.py`；仓库不保存静态生产主机清单 |
| `group_vars/` `host_vars/` | 全局与主机变量 |
| `roles/` | Ansible Role，按交付形态分层（见 §2） |
| `skills/` | 仓库协作约定（`config-as-code-Spec`、`release-branch-policy`） |
| `docs/` | 设计文档、迁移记录、任务、用例、runbook（所有文档类 .md 归此） |
| `scripts/` | 非 Ansible 的旁路/一次性脚本 |
| `tests/` | 契约测试（unittest/pytest） |
| `archive/` | **非通用**或**已退役**的 playbook、role 与物料（见 §3） |
| `contracts/` | 数据供给契约与 fixture（`database-provision-v1` 等） |
| `tasks/` `templates/` `files/` `vars/` | 顶层共享任务/模板/文件/变量（仅通用、被多个入口复用者） |
| `examples/` | 变量/环境示例 |
| `dashboards/` `deepflow/` | 面板 JSON、DeepFlow 专用 playbook 等专用物料 |

## 2. Role 分层（roles/ 下仅允许四个命名空间）

- `roles/vhosts/` — **宿主机原生交付**：systemd 服务、on-host 配置渲染、内核/网络调优、OS 基线、SSH、迁移。
- `roles/docker/` — **Docker Compose 交付**：渲染 `docker-compose.yaml.j2` 并拉起单机服务栈。
- `roles/k8s_helm/` — **Kubernetes / Helm 交付**：Helm release 投放到集群。
- `roles/saas/` — **云平台/API 接口类角色**：Cloudflare DNS、GitHub ruleset、Grafana/Guance 面板等对第三方 API 的操作。

规则：

1. `roles/` 下**不允许**出现上述四个命名空间之外的 role 目录。新增 role 必须归类到其中一处。
   - 兼容别名（符号链接）例外：`roles/vhost -> vhosts` 为历史入口保留，指向真实命名空间，不算未分层 role。
2. 归属判定按**交付形态**，不按业务域：
   - 与宿主机强耦合、systemd 托管 → `vhosts/`
   - 单机 Docker Compose → `docker/`
   - Pod 内工作负载（Helm）→ `k8s_helm/`
   - 对云/第三方 API 的纯接口操作 → `saas/`
3. 每个执行 role 必须带 `README.md`（职责、输入、前置、验收、回退），见 `roles/README.md`。

## 3. 归档规则（archive/）

**非通用**（绑定具体环境/业务域/一次性）或**退役**的 playbook 与 role 归入 `archive/`，不留在根目录：

| 归档位置 | 内容 |
| --- | --- |
| `archive/prod-v2/` | prod-v2、doco、uat 类入口 playbook 及其 `templates/`、`files/`、`tasks/` 专属物料 |
| `archive/web-saas/` | web-saas 业务入口 playbook |
| `archive/selfhost/` | selfhost 控制面/数据入口 playbook |
| `archive/legacy/` | 已退役或无法通过 `--syntax-check` 的历史入口 playbook |
| `archive/roles/web-saas/` | `web_saas_*` role |
| `archive/roles/selfhost/` | `selfhost_*` role |
| `archive/roles/legacy/` | 其它非通用/退役 role |

判定标准：

- 文件名含 `prod-v2`、`configure-prod-v2`、`web-saas`、`web_saas`、`doco`、`uat`、`selfhost` 等**环境/业务域强绑定**前缀的入口，默认非通用。
- 无法通过 `ansible-playbook --syntax-check` 的入口，退役到 `archive/legacy/`。
- 归档后必须同步更新：`scripts/`、`tests/`、`.github/workflows/`、`docs/` 与 README 中的路径引用。

`roles_path`（`ansible.cfg`）已把归档 role 根纳入搜索路径，归档 playbook 内的裸 role 名仍可解析。

## 4. 文档归属

- 所有**文档类** `.md` 归入 `docs/`。
- 例外（允许与代码同目录）：`README.md`、`AGENTS.md`、`SKILL.md`，以及各 role/脚本子目录自身的 `README.md`。
- 根目录只保留 `README.md`、`AGENTS.md`、`LICENSE`。

## 5. 门禁

- `tests/test_repository_layout.py`：校验 §2（roles 四命名空间）、§4（根目录无游离文档）。
- `.github/workflows/repository-layout-contract.yml`：在 PR 触及 `roles/**`、根 `*.yml`、`docs/**` 或本规范时运行。
