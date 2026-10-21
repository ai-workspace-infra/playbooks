#!/usr/bin/env bash
set -euo pipefail

# Network-only Sandbox check: no API key, payment, mutation, or production call.
[[ "${V4_STRIPE_SANDBOX_ONLY:-}" == "1" ]] || { echo 'set V4_STRIPE_SANDBOX_ONLY=1 only for an isolated Sandbox candidate' >&2; exit 2; }
container="${V4_ACCOUNTS_CONTAINER:-web-saas-accounts}"
http_code="$(docker exec "$container" sh -lc 'command -v curl >/dev/null && curl --connect-timeout 8 --max-time 15 -sS -o /dev/null -w "%{http_code}" https://api.stripe.com/v1/prices' 2>/dev/null)" || {
  echo 'FAIL: Accounts container cannot resolve, connect, or complete TLS to Stripe API' >&2
  exit 1
}
[[ "$http_code" =~ ^[1-5][0-9][0-9]$ ]] || { echo 'FAIL: no valid HTTP response from Stripe API' >&2; exit 1; }
echo "PASS: Stripe API DNS/TCP/TLS/HTTP reachable in Sandbox candidate (HTTP ${http_code})"
