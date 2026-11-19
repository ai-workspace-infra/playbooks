#!/usr/bin/env python3
"""Generate a Markdown capacity report from k6 --summary-export JSON."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "Usage: generate_report.py <summary.json> <report.md> <target_host> <p95_threshold_ms> [testid]",
            file=sys.stderr,
        )
        return 2

    summary_path = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    target_host = sys.argv[3]
    p95_threshold = float(sys.argv[4])
    test_id = sys.argv[5] if len(sys.argv) > 5 else "local-k6-run"

    if not summary_path.exists():
        print(f"Summary JSON not found: {summary_path}", file=sys.stderr)
        return 1

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = data.get("metrics", {})

    def metric_vals(name: str) -> dict:
        entry = metrics.get(name, {})
        return entry.get("values", entry)

    reqs = metric_vals("http_reqs")
    total_reqs = int(reqs.get("count", 0))
    rps = float(reqs.get("rate", 0.0))

    failed = metric_vals("http_req_failed")
    fail_rate = float(failed.get("value", failed.get("rate", 0.0))) * 100.0
    availability = max(0.0, 100.0 - fail_rate)

    dur = metric_vals("http_req_duration")
    lat_min = float(dur.get("min", 0.0))
    lat_avg = float(dur.get("avg", 0.0))
    lat_med = float(dur.get("med", 0.0))
    lat_p90 = float(dur.get("p(90)", 0.0))
    lat_p95 = float(dur.get("p(95)", 0.0))
    lat_max = float(dur.get("max", 0.0))

    vus_max = int(metric_vals("vus_max").get("max", 0))
    passed_p95 = lat_p95 <= p95_threshold
    passed_err = fail_rate < 1.0
    verdict = "PASS" if (passed_p95 and passed_err) else "THRESHOLD EXCEEDED"

    est_users_low = vus_max * 3
    est_users_high = vus_max * 5
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    report = f"""# K6 Proxy Capacity Stress Test Report

- **Target Node**: `{target_host}`
- **Test ID**: `{test_id}`
- **Generated At**: {generated_at}
- **Overall Verdict**: **{verdict}** (Target: P95 <= {p95_threshold:.0f} ms, Error Rate < 1%)
- **Grafana Dashboard**: https://observability.svc.plus/d/k6-performance-capacity-overview/k6-performance-capacity-stress-test-dashboard

---

## 1. Core Performance Summary

| Metric | Measured Value | Threshold / Target | Status |
| :--- | :---: | :---: | :---: |
| **Max Concurrent VUs** | `{vus_max}` VUs | — | — |
| **Total Requests** | `{total_reqs}` | — | — |
| **Throughput (QPS)** | `{rps:.2f}` req/s | — | — |
| **Availability (Success Rate)** | `{availability:.2f}%` | `>= 99.00%` | {"PASS" if passed_err else "FAIL"} |
| **Error Rate** | `{fail_rate:.2f}%` | `< 1.00%` | {"PASS" if passed_err else "FAIL"} |
| **Latency P95** | `{lat_p95:.2f}` ms | `<= {p95_threshold:.0f}` ms | {"PASS" if passed_p95 else "FAIL"} |

---

## 2. Latency Distribution (ms)

| Min | Avg | P50 (Median) | P90 | P95 | Max |
| :---: | :---: | :---: | :---: | :---: | :---: |
| {lat_min:.2f} | {lat_avg:.2f} | {lat_med:.2f} | {lat_p90:.2f} | **{lat_p95:.2f}** | {lat_max:.2f} |

---

## 3. Capacity Assessment

- Under continuous synthetic traffic (`1s` think-time per VU), `{target_host}` sustained **`{vus_max}` concurrent VUs** at **{rps:.2f} req/s** with **P95 = {lat_p95:.2f} ms** and **{availability:.2f}%** availability.
- **Estimated Real-World Concurrent Online Users**: **{est_users_low} ~ {est_users_high} users** (assuming typical 3x–5x think-time / idle multiplexing factor compared to continuous 1s loop VUs).
"""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
