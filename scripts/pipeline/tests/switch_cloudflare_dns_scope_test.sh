#!/usr/bin/env bash
set -euo pipefail

dns_script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/switch-cloudflare-dns-records.sh"

# Agent Proxy production cutover must publish only its dedicated selfhost
# endpoint; the shared agent-proxy.svc.plus endpoint remains unmanaged here.
grep -Fq 'TARGET_DOMAINS:-}" = "agent-proxy"' "${dns_script}"
grep -Fq '"cloudflare_dns_source_hosts": ["agent_proxy"]' "${dns_script}"
grep -Fq '"cloudflare_dns_static_records": []' "${dns_script}"
grep -Fq '"cloudflare_dns_alias_records\":${DNS_ALIAS_RECORDS_JSON}' "${dns_script}"

echo "switch_cloudflare_dns_scope_test: PASS"
