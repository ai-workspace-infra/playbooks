#!/usr/bin/env python3
"""Safely suspend Flux and scale source cluster workloads to zero, then verify target cutover.

Replaces /tmp/suspend_and_scale_down_source.py and /tmp/test_real_traffic_on_target.py.
Requires explicit --confirm to mutate the source cluster; defaults to read-only inspection.
"""

import json
import sys

from common import (
    KUBE_RESOLVE_PREFIX,
    HostConnection,
    build_manifest_arg_parser,
    log,
    parse_host_manifest,
)
from probe_cutover import run_probes

DEFAULT_WORKLOAD_NAMESPACES = [
    "web-saas-prod",
    "vault-prod",
    "postgresql-prod",
    "accounts-migration",
    "ingress-prod",
    "external-secrets",
    "observability-prod",
]


def suspend_flux_cmd():
    """Return shell snippet suspending all Flux Kustomizations and HelmReleases."""
    return (
        f"{KUBE_RESOLVE_PREFIX}; "
        "for kind in kustomization helmrelease; do "
        "  $KUBE_CMD get $kind -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name --no-headers 2>/dev/null | "
        "  while read -r ns name; do "
        "    [ -n \"$name\" ] && $KUBE_CMD patch $kind \"$name\" -n \"$ns\" --type=merge -p '{\"spec\":{\"suspend\":true}}' || true; "
        "  done; "
        "done"
    )


def scale_zero_cmd(namespaces):
    """Return shell snippet scaling Deployments and StatefulSets in namespaces to zero."""
    ns_list = " ".join(namespaces)
    return (
        f"{KUBE_RESOLVE_PREFIX}; "
        f"for ns in {ns_list}; do "
        "  $KUBE_CMD scale deployment --all -n \"$ns\" --replicas=0 2>/dev/null || true; "
        "  $KUBE_CMD scale statefulset --all -n \"$ns\" --replicas=0 2>/dev/null || true; "
        "done; "
        "sleep 5; "
        "$KUBE_CMD get pods -A"
    )


def check_source_pods_cmd(namespaces):
    """Return shell snippet listing any remaining pods in workload namespaces."""
    ns_list = " ".join(namespaces)
    return (
        f"{KUBE_RESOLVE_PREFIX}; "
        f"for ns in {ns_list}; do "
        "  $KUBE_CMD get pods -n \"$ns\" --no-headers 2>/dev/null || true; "
        "done"
    )


def isolate_source(
    source_cfg,
    target_cfg,
    namespaces,
    confirm=False,
    skip_preflight_probe=False,
    emit_json=False,
):
    """Suspend source Flux, scale source workloads to 0 when confirmed, and verify target."""
    if not confirm:
        with HostConnection(source_cfg) as src_conn:
            pods_out, _, _ = src_conn.run(check_source_pods_cmd(namespaces))
        probe_res = run_probes(target_cfg, mode="live", emit_json=False) if not emit_json else None
        summary = {
            "status": "DRY_RUN",
            "source_host": source_cfg["public_ipv4"],
            "target_host": target_cfg["public_ipv4"],
            "namespaces": namespaces,
            "remaining_source_pods": pods_out.splitlines() if pods_out else [],
            "message": "Pass --confirm to suspend Flux and scale source workloads to 0.",
        }
        if emit_json:
            print(json.dumps(summary, indent=2))
        else:
            log(f"Dry-run on source {source_cfg['public_ipv4']}: {len(summary['remaining_source_pods'])} workload pod(s) active.")
            log("Pass --confirm to execute Flux suspend + scale-to-zero.")
        return summary

    if not skip_preflight_probe:
        log("Running live pre-isolation probe to confirm target serves public DNS before scaling down source...")
        pre_probe = run_probes(target_cfg, mode="live", emit_json=False)
        if pre_probe["status"] != "PASSED":
            raise RuntimeError(
                "Aborting source isolation: live target probe failed before source scale-down."
            )

    with HostConnection(source_cfg) as src_conn:
        log(f"Suspending Flux Kustomizations & HelmReleases on {source_cfg['public_ipv4']}...")
        flux_out, _, _ = src_conn.run(suspend_flux_cmd(), timeout=120)

        log(f"Scaling down workloads to 0 replicas across {len(namespaces)} namespaces...")
        scale_out, _, _ = src_conn.run(scale_zero_cmd(namespaces), timeout=120)

        remaining_out, _, _ = src_conn.run(check_source_pods_cmd(namespaces))

    log("Verifying all public traffic and Vault continue passing with source workloads stopped...")
    post_probe = run_probes(target_cfg, mode="live", emit_json=False)

    summary = {
        "status": post_probe["status"],
        "source_host": source_cfg["public_ipv4"],
        "target_host": target_cfg["public_ipv4"],
        "namespaces_scaled_zero": namespaces,
        "remaining_workload_pods": remaining_out.splitlines() if remaining_out else [],
        "flux_suspend_lines": len(flux_out.splitlines()) if flux_out else 0,
        "post_isolation_probe": post_probe,
        "all_pods_snapshot": scale_out.splitlines(),
    }
    if emit_json:
        print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = build_manifest_arg_parser(
        "Safely suspend Flux and scale down source cluster workloads, then probe live target",
        __file__,
    )
    parser.add_argument(
        "--namespaces",
        default=",".join(DEFAULT_WORKLOAD_NAMESPACES),
        help="Comma-separated workload namespaces on source cluster to scale to zero",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually suspend Flux and scale source cluster workloads to zero (otherwise dry-run)",
    )
    parser.add_argument(
        "--skip-preflight-probe",
        action="store_true",
        help="Skip live DNS/HTTPS check before scaling down source cluster",
    )
    args = parser.parse_args()

    source_cfg = parse_host_manifest(args.source_manifest)
    target_cfg = parse_host_manifest(args.target_manifest)
    namespaces = [n.strip() for n in args.namespaces.split(",") if n.strip()]

    res = isolate_source(
        source_cfg,
        target_cfg,
        namespaces,
        confirm=args.confirm,
        skip_preflight_probe=args.skip_preflight_probe,
        emit_json=args.json,
    )
    sys.exit(0 if res["status"] in ("PASSED", "DRY_RUN") else 1)


if __name__ == "__main__":
    main()
