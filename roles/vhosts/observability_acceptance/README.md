# Observability closure acceptance role (`observability_acceptance`)

Owner: Playbooks (`OBS-05`, `ai-workspace-infra/playbooks#767`, parent `ai-workspace-infra/gitops#557`).

This controller-side role executes `scripts/verify_observability_closure.py` to perform
strictly read-only, zero-side-effect verification of the Observability data closure across:

1. **Endpoint Health & Version Contract**:
   - `/api/health` (`HTTP 200`, `database: ok`, Grafana version/commit)
   - `/login` (`HTTP 200`)
   - Unauthenticated boundary (`/api/datasources` must reject anonymous requests with `HTTP 401`/`403`)
   - Required Datasource UIDs (`victoriametrics`, `victorialogs`, `victoriatraces`, `cmdb_postgres`)
2. **17 Dashboards Baseline & Anti-Masking Gate**:
   - 16 imported dashboards (`source-grafana-inventory`) + `web-saas-k3s-v5-overview`
   - Rejects forbidden `or vector(0)` query fallbacks that mask missing telemetry
   - Freshness gates: Metrics `<= 180s` (3 scrape cycles), Logs `<= 300s` (5 minutes), CMDB `<= 3600s`
3. **Negative Isolation Gate**:
   - Queries with `resource_id="__nonexistent_obs_probe__"` and `environment="__invalid_env__"` must return empty sets (`0` series/rows); any non-empty fallback result fails the gate
4. **30-Minute Continuity & Controlled Recovery Gate**:
   - Verifies continuous sampling across a 30-minute window (`1800s`) without gaps `> 180s`
   - Unapproved or unexecuted stop/recovery drills strictly emit `NOT_RUN` (never `PASS`)
5. **Sanitized Evidence Bundle**:
   - Emits `evidence.json`, `report.zh.md`, and `manifest.json` with IPv4/credential redaction and release/rollback guardrails (retired host `35.221.181.68` is strictly blocked as a rollback target)
