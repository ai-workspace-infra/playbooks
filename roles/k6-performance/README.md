# Role: `k6-performance`

使用本机 Docker (`grafana/k6:1.7.1`) + 临时 `xray` 客户端，对边缘代理节点（如 `jp-xconnect-01.svc.plus`）发起真实 VLESS+XHTTP+TLS 阶梯加压测试，并将指标推送至 `observability.svc.plus` 以在 [k6-performance-capacity-overview](https://observability.svc.plus/d/k6-performance-capacity-overview/k6-performance-capacity-stress-test-dashboard) 面板实时可视化，同时生成本地 Markdown 实测报告。

## 快速执行

```bash
export K6_PROMETHEUS_RW_SERVER_URL="https://observability.svc.plus/ingest/metrics/api/v1/write"
export K6_PROMETHEUS_RW_USERNAME="<rw_user>"
export K6_PROMETHEUS_RW_PASSWORD="<rw_password>"

ansible-playbook run_k6_proxy_performance.yml \
  -e k6_perf_target_host=jp-xconnect-01.svc.plus \
  -e k6_perf_p95_threshold_ms=1500 \
  -e k6_perf_duration=5m \
  -e k6_perf_max_vus=500
```

## 核心变量

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `k6_perf_target_host` | `jp-xconnect-01.svc.plus` | 目标代理节点域名 |
| `k6_perf_xray_uuid` | `01a11a7c-f469-75e9-86f1-77a3c7317151` | 临时 Xray 客户端使用的 VLESS UUID |
| `k6_perf_profile` | `proxy_capacity` | 压测 profile (`smoke`, `capacity`, `proxy_capacity`) |
| `k6_perf_p95_threshold_ms` | `1500` | P95 延迟判定阈值 (ms) |
| `k6_perf_duration` | `profile` | 压测总时长 (`profile`, `60s`, `5m`, `10m`, `30m`) |
| `k6_perf_max_vus` | `profile` | 最大并发 VU (`profile`, `1`, `5`, `20`, `100`, `500`, `1000`) |
| `k6_perf_report_path` | `/tmp/k6-proxy-perf/k6-proxy-capacity-report.md` | 实测报告输出路径 |
