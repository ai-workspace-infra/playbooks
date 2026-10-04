# Grafana OIDC 本地测试

```bash
python3 -m venv /tmp/grafana-test-venv
/tmp/grafana-test-venv/bin/python -m pip install -r tests/requirements-iam.txt
PATH="/tmp/grafana-test-venv/bin:$PATH" /tmp/grafana-test-venv/bin/python tests/test_grafana_oidc_runtime.py
```

测试从 role 中读取真实 assert/template task，只在临时目录渲染，无 Docker、
无 become、无远端 inventory。覆盖关闭、开启、4 个必填字段缺失、HTTP issuer、
HTTP 外部 URL、OAuth 端点、PKCE、角色严格模式、禁止注册、文件权限和 diff 脱敏。

真实环境仍须验证 root 所有者、预建用户、真实 claims 对应的角色表达式、
三种角色权限、缺角色/未预建用户拒绝、应急管理员和回滚。
完整操作与记录模板见 `platform-ops-toolkit/docs/howto/iam-integration-testing.md`。
