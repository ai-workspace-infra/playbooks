#!/bin/bash
set +e
echo "=== prometheus_remote.auth 段(脱敏) ==="
sed -n '/\[sinks.prometheus_remote.auth\]/,+3p' /etc/vector/vector.toml | sed -E 's/(password = ").*/\1<REDACTED>/'
echo
echo "=== 引导文件(脱敏) ==="
sed -E 's/(VECTOR_AUTH_PASSWORD=).*/\1<REDACTED>/' /etc/vector/remote-write-auth.json
echo
echo "=== 长度/前后缀对比 ==="
orig=$(sed -n '/\[sinks.prometheus_remote.auth\]/,+2p' /etc/vector/vector.toml | grep '^password' | sed 's/^password = "//;s/"$//')
boot=$(grep '^VECTOR_AUTH_PASSWORD=' /etc/vector/remote-write-auth.json | sed 's/^VECTOR_AUTH_PASSWORD=//')
echo "orig len=$(printf '%s' "$orig" | wc -c)"
echo "boot len=$(printf '%s' "$boot" | wc -c)"
echo "orig prefix12=[$(printf '%s' "$orig" | cut -c1-12)]"
echo "boot prefix12=[$(printf '%s' "$boot" | cut -c1-12)]"
echo "orig tail3=[$(printf '%s' "$orig" | rev | cut -c1-3 | rev)]"
echo "boot tail3=[$(printf '%s' "$boot" | rev | cut -c1-3 | rev)]"
echo
echo "=== orig 字节尾部 ==="
printf '%s' "$orig" | od -c | tail -3
echo
echo "=== boot 字节尾部 ==="
printf '%s' "$boot" | od -c | tail -3
