#!/usr/bin/env python3
"""Verify DNS resolution, HTTPS endpoints, PostgreSQL TCP, and Vault health from GitOps declarations.

Replaces /tmp/full_production_probe.py, /tmp/verify_cutover.py, /tmp/check_dns.py,
and /tmp/verify_target_vault_kv.py with dynamic GitOps domain/host loading.
"""

import json
import shutil
import socket
import subprocess
import sys

from common import build_manifest_arg_parser, parse_host_manifest


def probe_paths_for_domain(domain):
    """Map service domain role to health check endpoints or TCP port without hardcoding FQDNs."""
    label = domain.split(".", 1)[0].lower()
    if label.startswith("postgresql"):
        return ("tcp", [5432])
    if label.startswith("vault"):
        return ("https", ["/v1/sys/health"])
    if label.startswith("accounts") or label.startswith("billing"):
        return ("https", ["/healthz", "/readyz"])
    if label.startswith("console") or "xworktech" in domain.lower():
        return ("https", ["/"])
    return ("https", ["/"])


def dig_ips(domain, dns_server=None):
    """Resolve A/CNAME chain via dig +short."""
    cmd = ["dig", "+short", domain]
    if dns_server:
        cmd.append(f"@{dns_server}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def curl_https(domain, path, resolve_ip=None, timeout=5):
    """Probe HTTPS endpoint and return numeric HTTP status code."""
    url = f"https://{domain}{path}"
    cmd = [
        "curl", "-s", "-k", "-o", "/dev/null",
        "-w", "%{http_code}",
        "--connect-timeout", str(timeout),
    ]
    if resolve_ip:
        cmd.extend(["--resolve", f"{domain}:443:{resolve_ip}"])
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    raw = proc.stdout.strip()
    return int(raw) if raw.isdigit() else 0


def check_tcp(host, port, timeout=4):
    """Verify TCP reachability for non-HTTP services such as PostgreSQL."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_vault_status(vault_domain):
    """Run `vault status -format=json` against the declared public Vault domain."""
    if not shutil.which("vault"):
        return {"checked": False, "reason": "vault CLI not installed"}
    env = {"VAULT_ADDR": f"https://{vault_domain}"}
    proc = subprocess.run(
        ["vault", "status", "-format=json"],
        env={**subprocess.os.environ, **env},
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        return {"checked": True, "ok": False, "error": proc.stderr.strip()}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"checked": True, "ok": False, "error": str(exc)}
    ok = bool(payload.get("initialized")) and not bool(payload.get("sealed"))
    return {
        "checked": True,
        "ok": ok,
        "cluster_id": payload.get("cluster_id"),
        "leader_address": payload.get("leader_address"),
        "sealed": payload.get("sealed"),
        "version": payload.get("version"),
    }


def run_probes(target_cfg, mode="auto", dns_server=None, require_pg_dns=False, emit_json=False):
    """Run DNS, HTTP, TCP, and Vault probes derived from target GitOps manifest."""
    target_ip = target_cfg["public_ipv4"]
    aliases = target_cfg["reserved_dns_aliases"]
    service_domains = target_cfg["service_domains"]

    if mode == "auto":
        mode = "live" if target_cfg["public_dns_cutover"] else "pre-cutover"

    all_domains = []
    for dom in list(aliases.keys()) + list(service_domains):
        if dom not in all_domains:
            all_domains.append(dom)

    dns_results = []
    http_results = []
    tcp_results = []
    failures = []

    for dom in all_domains:
        ips = dig_ips(dom, dns_server=dns_server)
        resolved = ips[-1] if ips else "NO_RECORD"
        kind, targets = probe_paths_for_domain(dom)

        dns_ok = True
        if mode == "live":
            if kind == "tcp" and not require_pg_dns:
                dns_ok = True
            else:
                dns_ok = resolved == target_ip
                if not dns_ok:
                    failures.append(f"DNS mismatch for {dom}: got {resolved}, expected {target_ip}")

        dns_results.append({"domain": dom, "resolved": resolved, "chain": ips, "ok": dns_ok})

        resolve_arg = target_ip if mode == "pre-cutover" else None
        if kind == "https":
            for path in targets:
                code = curl_https(dom, path, resolve_ip=resolve_arg)
                ok = code == 200
                if not ok:
                    failures.append(f"HTTPS probe failed for https://{dom}{path}: HTTP {code}")
                http_results.append(
                    {
                        "url": f"https://{dom}{path}",
                        "resolved": resolve_arg or resolved,
                        "http_code": code,
                        "ok": ok,
                    }
                )
        elif kind == "tcp":
            host_to_probe = target_ip if (mode == "pre-cutover" or not require_pg_dns) else dom
            for port in targets:
                ok = check_tcp(host_to_probe, port)
                if not ok:
                    failures.append(f"TCP probe failed for {host_to_probe}:{port}")
                tcp_results.append({"host": host_to_probe, "port": port, "ok": ok})

    vault_domains = [d for d in aliases.keys() if d.split(".", 1)[0].lower() == "vault"]
    vault_result = None
    if mode == "live" and vault_domains:
        vault_result = check_vault_status(vault_domains[0])
        if vault_result.get("checked") and not vault_result.get("ok"):
            failures.append(f"Vault unsealed/initialized check failed: {vault_result}")

    summary = {
        "status": "PASSED" if not failures else "FAILED",
        "mode": mode,
        "target_ip": target_ip,
        "dns_results": dns_results,
        "http_results": http_results,
        "tcp_results": tcp_results,
        "vault": vault_result,
        "failures": failures,
    }

    if emit_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"=== Cutover Probe (mode={mode}, target_ip={target_ip}) ===")
        print("\n[1] DNS Resolution:")
        for row in dns_results:
            flag = "OK" if row["ok"] else "WARN/FAIL"
            print(f"  {row['domain']:<40} | {row['resolved']:<16} | {flag}")

        print("\n[2] HTTPS Endpoint Probes:")
        for row in http_results:
            flag = "SUCCESS" if row["ok"] else "FAILED"
            print(f"  {row['url']:<48} | {row['resolved']:<16} | HTTP {row['http_code']:<4} | {flag}")

        if tcp_results:
            print("\n[3] TCP Port Probes:")
            for row in tcp_results:
                flag = "OPEN" if row["ok"] else "CLOSED"
                print(f"  {row['host']}:{row['port']:<30} | {flag}")

        if vault_result:
            print("\n[4] Public Vault Status:")
            print(f"  {json.dumps(vault_result)}")

        if failures:
            print("\nFailures:")
            for err in failures:
                print(f"  - {err}")
        print(f"\nOverall Probe Status: {summary['status']}")

    return summary


def main():
    parser = build_manifest_arg_parser(
        "Probe DNS, HTTPS, PostgreSQL TCP, and Vault status from GitOps manifest",
        __file__,
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "pre-cutover", "live"],
        default="auto",
        help="Use --resolve pre-cutover check or live public DNS verification (default: auto)",
    )
    parser.add_argument(
        "--dns-server",
        default=None,
        help="Optional DNS resolver IP (e.g. 1.1.1.1) for dig queries",
    )
    parser.add_argument(
        "--require-pg-dns",
        action="store_true",
        help="Also enforce public DNS A/CNAME match for postgresql domains in live mode",
    )
    args = parser.parse_args()

    target_cfg = parse_host_manifest(args.target_manifest)
    summary = run_probes(
        target_cfg,
        mode=args.mode,
        dns_server=args.dns_server,
        require_pg_dns=args.require_pg_dns,
        emit_json=args.json,
    )
    sys.exit(0 if summary["status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
