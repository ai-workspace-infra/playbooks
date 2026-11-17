# XConnect Edge Agent Role

Unified Ansible role managing the complete lifecycle of XConnect Edge Agent nodes.

## Component Architecture & Dependencies

```
[ Downstream Client ]
         │
         ▼  (Port 80/443 TLS)
┌────────────────────────────────────────────────────────┐
│  Ingress Engine (Mutually Exclusive - Exactly One)     │
│   • fluxgate (Default, Cloudflare Pingora-based)       │
│   • caddy                                              │
│   • haproxy                                            │
└──────────┬─────────────────────────────┬───────────────┘
           │ (h2c / /dev/shm/xray.sock)  │ (HTTP /metrics)
           ▼                             ▼
┌──────────────────────┐      ┌─────────────────────────┐
│     Xray Core        │◄─────┤      xray-exporter      │
│  • XHTTP: :10086 API │      │  • :8080 (XHTTP scrape) │
│  • TCP:   :10087 API │      │  • :8081 (TCP scrape)   │
└──────────┬───────────┘      └────────────┬────────────┘
           │                               │ (snapshots)
           ▼                               ▼
┌──────────────────────┐      ┌─────────────────────────┐
│  xconnect-edge-agent │      │      vector agent       │
│  • accounts.svc.plus │      │  • 127.0.0.1:8686       │
│  • dynamic routing   │      │  • billing snapshots    │
└──────────────────────┘      └─────────────────────────┘
```

### 1. Ingress Engine Mutual Exclusivity
- The role enforces that exactly **one** ingress engine runs on the host to terminate TLS on ports 80 and 443.
- Choices: `fluxgate` (default) | `caddy` | `haproxy`.
- The competing services are automatically stopped, disabled, and validated prior to starting the chosen ingress proxy.

### 2. Protocol Engine & Node Management
- **`xray`**: Runs custom-built Xray Core terminating XHTTP on `/dev/shm/xray.sock` and TCP on `*:1443`.
- **`xconnect-edge-agent`**: Synchronizes client account definitions, routing rules, and pool health with `https://accounts.svc.plus`.

### 3. Observability & Billing Collection
- **`xray-exporter`**: Periodically scrapes Xray Core traffic statistics, exposing Prometheus metrics on `:8080` (XHTTP) and `:8081` (TCP).
- **`vector`**: Runs as a lightweight local metrics and billing collector, buffering traffic snapshots via `127.0.0.1:8686` and forwarding to `https://observability.svc.plus` and `https://billing.svc.plus`.

### 4. 1C1G Low-Spec Baseline
- Nodes with **1 vCPU** or **<= 1.5 GB RAM** (1C1G class) strictly default to **process mode** (`systemd`).
- Container runtimes (K3s/Docker) impose unnecessary memory footprints on 1C1G nodes and are precluded in this baseline.

## Variables

| Variable | Default | Description |
|---|---|---|
| `xconnect_edge_proxy_engine` | `fluxgate` | Ingress engine: `fluxgate`, `caddy`, or `haproxy` |
| `xconnect_edge_runtime` | `systemd` | Runtime mode: `systemd` (process) or `container` |
| `xconnect_edge_xray_enabled` | `true` | Enable Xray Core and XConnect Agent |
| `xconnect_edge_exporter_enabled` | `true` | Enable Xray Exporter (:8080, :8081) |
| `xconnect_edge_vector_billing_enabled`| `true` | Enable Vector billing snapshot ingestion |
| `xconnect_edge_observability_enabled` | `true` | Enable Vector observability agent |
