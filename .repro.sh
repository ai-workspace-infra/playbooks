#!/bin/bash
set +e
echo "=== 手动复现校验命令 ==="
echo "--- 直接 validate(无环境) ---"
/usr/bin/vector validate --no-environment --config-toml /etc/vector/vector.toml 2>&1 | head -6
echo "rc=$?"
echo
echo "--- systemd-run + EnvironmentFile ---"
/usr/bin/systemd-run --quiet --pipe --wait --collect \
  --property=EnvironmentFile=/etc/vector/remote-write-auth.json \
  /usr/bin/vector validate --no-environment --config-toml /etc/vector/vector.toml 2>&1 | head -8
echo "rc=$?"
echo
echo "=== 当前 vector.toml 的 auth 段 ==="
sed -n '/\[sinks.prometheus_remote.auth\]/,+3p' /etc/vector/vector.toml | sed -E 's/(password = ").*/\1<REDACTED>/'
echo
echo "=== 凭据文件格式 ==="
sed -E 's/(VECTOR_AUTH_PASSWORD=).*/\1<REDACTED>/' /etc/vector/remote-write-auth.json
