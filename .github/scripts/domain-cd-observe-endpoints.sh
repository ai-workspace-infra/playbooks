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
poll_seconds="${OBSERVE_POLL_SECONDS:-3}"
curl_timeout_seconds="${OBSERVE_CURL_TIMEOUT_SECONDS:-5}"
# Comma-separated, endpoint-specific success codes in the same order as
# OBSERVE_URLS.  Empty tokens retain the default (2xx/3xx) contract.  This
# keeps an intentional unauthenticated 401/404 from being mistaken for an
# unavailable service without treating arbitrary 4xx responses as healthy.
IFS=',' read -r -a observe_expected_codes <<< "${OBSERVE_EXPECTED_CODES:-}"

read -r -a observe_urls <<< "${OBSERVE_URLS}"
if [[ "${#observe_urls[@]}" -eq 0 ]]; then
  echo "::warning::${DOMAIN}/${DEPLOY_ENV} has no valid observe URL tokens."
  exit 0
fi

# DNS 切换之前, 公网记录可能还指着旧主机(甚至指着一台已经销毁的机器), 那时候
# 直接打域名量到的是别人, 探测结果毫无意义 —— 而且失败得像"新主机没起来"。
# 给定 OBSERVE_RESOLVE_IP 时用 --resolve 把所有待探测域名钉到本次部署的
# 那台机器上。必须一次性覆盖整个 OBSERVE_URLS 集合: console 的 307/302
# 可能跳到 accounts 或其它域名, curl 不应在 DNS 切换前逃逸到公网解析。
resolve_args=()
if [[ -n "${OBSERVE_RESOLVE_IP:-}" ]]; then
  echo "Pinning observed hostnames to ${OBSERVE_RESOLVE_IP} (pre-DNS-cutover safe)."
  declare -A resolve_hosts=()
  for url in "${observe_urls[@]}"; do
    host="${url#*://}"
    host="${host%%/*}"
    host="${host%%:*}"
    if [[ -n "${host}" ]]; then
      resolve_hosts["${host}"]=1
    fi
  done
  for host in "${!resolve_hosts[@]}"; do
    resolve_args+=(--resolve "${host}:443:${OBSERVE_RESOLVE_IP}")
    resolve_args+=(--resolve "${host}:80:${OBSERVE_RESOLVE_IP}")
  done
fi

curl_for() {
  local url="$1"
  # A 3xx from the local Caddy/Next.js stack is a successful readiness signal.
  # Do not follow it here: following can leave the --resolve set when the
  # redirect target is not in the observed URL list.
  local -a args=(-sS -o /dev/null -w '%{http_code}' --max-time "${curl_timeout_seconds}")
  args+=("${resolve_args[@]}")
  curl "${args[@]}" "${url}" 2>/dev/null
}

is_healthy_code() {
  local index="$1"
  local code="$2"
  local expected="${observe_expected_codes[${index}]:-}"

  if [[ -z "${expected}" ]]; then
    [[ "${code}" =~ ^[23] ]]
    return
  fi

  IFS='|' read -r -a accepted_codes <<< "${expected}"
  local accepted
  for accepted in "${accepted_codes[@]}"; do
    [[ "${code}" == "${accepted}" ]] && return 0
  done
  return 1
}

# Doco-CD 是轮询式的, 镜像还要现拉, 所以给一个窗口而不是一次定生死。
#
failures=()
last_codes=()
completed=()
for index in "${!observe_urls[@]}"; do
  last_codes["${index}"]=""
  completed["${index}"]=false
done

probe_dir="$(mktemp -d)"
trap 'rm -rf "${probe_dir}"' EXIT

deadline=$(( $(date +%s) + timeout_seconds ))
while :; do
  probe_pids=()
  for index in "${!observe_urls[@]}"; do
    [[ "${completed[${index}]}" == true ]] && continue
    (
      code="$(curl_for "${observe_urls[${index}]}")" || code="000"
      printf '%s\n' "${code}" > "${probe_dir}/${index}"
    ) &
    probe_pids+=("$!")
  done

  for pid in "${probe_pids[@]}"; do
    wait "${pid}" || true
  done

  pending=false
  for index in "${!observe_urls[@]}"; do
    [[ "${completed[${index}]}" == true ]] && continue
    url="${observe_urls[${index}]}"
    code="$(<"${probe_dir}/${index}")"
    last_codes["${index}"]="${code}"
    if is_healthy_code "${index}" "${code}"; then
      completed["${index}"]=true
      echo "OK   ${url} -> HTTP ${code}"
    else
      pending=true
    fi
  done

  [[ "${pending}" == false ]] && break
  [[ $(date +%s) -lt "${deadline}" ]] || break
  sleep "${poll_seconds}"
done

for index in "${!observe_urls[@]}"; do
  if [[ "${completed[${index}]}" != true ]]; then
    url="${observe_urls[${index}]}"
    last="${last_codes[${index}]}"
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
