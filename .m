fix(vector): 校验时注入 static-file 凭据环境

static-file 模式下 vector.toml 只含 ${VECTOR_AUTH_USER} / ${VECTOR_AUTH_PASSWORD}
占位符, 真值来自 systemd 的 EnvironmentFile。原来的 vector validate 不带环境,
被 "Missing environment variable" 判为失败并触发配置回滚 —— 校验方式与运行
条件不一致。

改为在校验前用 set -a 读同一份 /etc/vector/remote-write-auth.json, 保证验证的
就是实际会运行的那份组合。