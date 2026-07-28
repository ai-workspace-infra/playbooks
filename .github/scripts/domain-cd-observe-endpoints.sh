#!/usr/bin/env bash
set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${DEPLOY_ENV:?DEPLOY_ENV is required}"

# Pull-only 的代价: confirm-pull-only 只证明 gitops 里写对了 tag, 证明不了
# Doco-CD 真的 reconcile 过。少了这一步, 域 CD 会在容器根本没起来的时候
# 报绿 —— 2026-07-28 run 30339933681 就是: guard 通过、job success, 而
# accounts-uat 是 502、console-uat 直接 TLS internal error(Caddy 没有该
# SNI 的证书)。本仓 workflow 头部写的是 "validate and observe delivery",
# observe 这半边此前是空的。
#
# 这里只做只读观测, 不改 gitops、不碰主机, 不越 pull-only 边界。

if [[ -z "${OBSERVE_URLS:-}" ]]; then
  echo "::warning::${DOMAIN}/${DEPLOY_ENV} has no observe_urls wired — delivery cannot be observed, only requested. Not a pass, just unverifiable."
  exit 0
fi

timeout_seconds="${OBSERVE_TIMEOUT_SECONDS:-300}"
poll_seconds="${OBSERVE_POLL_SECONDS:-15}"

# Doco-CD 是轮询式的, 镜像还要现拉, 所以给一个窗口而不是一次定生死。
#
# 窗口按 URL 各算各的, 不能所有 URL 共用一个 deadline: 共用的话第一个
# URL 卡满整个窗口后, 后面的 URL 一次都探不到就出循环, 报出来的是空状态
# 而不是真实结果 —— 那等于把"没测"伪装成"测过了"。
failures=()

for url in ${OBSERVE_URLS}; do
  ok=false
  last=""
  deadline=$(( $(date +%s) + timeout_seconds ))
  # 至少探一次, 循环条件用 do-while 语义, 避免 deadline 边界上零次探测。
  while :; do
    # 分开取 http_code 与 curl 自身退出码: TLS 握手失败时 http_code 是 000,
    # 与"连上了但 502"是两种完全不同的故障, 合并成一个数字会把它们抹平。
    code="$(curl -sS -o /dev/null -w '%{http_code}' -L --max-time 15 "${url}" 2>/dev/null)" || code="000"
    if [[ "${code}" =~ ^[23] ]]; then
      ok=true
      last="${code}"
      break
    fi
    last="${code}"
    [[ $(date +%s) -lt $deadline ]] || break
    sleep "${poll_seconds}"
  done

  if [[ "${ok}" == true ]]; then
    echo "OK   ${url} -> HTTP ${last}"
  else
    if [[ "${last}" == "000" ]]; then
      detail="no HTTP response (TLS handshake or connection failed)"
    else
      detail="HTTP ${last}"
    fi
    failures+=("${url}: ${detail}")
    echo "::error::${url} never became healthy within ${timeout_seconds}s — ${detail}"
  fi
done

if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "::error::${DOMAIN}/${DEPLOY_ENV}: gitops pins the requested tag, but the running service did not come up. Doco-CD either has not reconciled or the containers are failing." >&2
  printf '  - %s\n' "${failures[@]}" >&2
  exit 1
fi

echo "Observed: every ${DOMAIN}/${DEPLOY_ENV} endpoint is serving."
