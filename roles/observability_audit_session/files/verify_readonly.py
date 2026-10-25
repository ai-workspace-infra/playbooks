#!/usr/bin/env python3
"""Bounded GET-only StarsL/exporter checks, using an explicit Viewer identity."""
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

EXPECTED_UIDS = {
    "StarsL-JOB-node", "agent-ai-observability", "ai-aggregator-overview", "begqoward2epsf",
    "blackbox-exporter-overview", "cmdb-resource-overview", "homepage-navigation",
    "k6-performance-capacity-overview", "postgres-exporter-overview", "process-exporter-with-tree",
    "serverless-fullstack-architecture", "victoria-logs-overview", "victoria-traces-overview",
    "xconnect-comprehensive", "xconnect-user-connection-diagnostics", "xhttp-edge-reliability",
    "web-saas-k3s-v5-overview", "StarsL_cn_K8S",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def verify(args, token, expected_identity):
    """Run only bounded GET calls; return a sanitized in-memory report."""
    checks = {}
    report = {"schema_version": "starsl-readonly-acceptance/v1", "cluster": args.cluster,
              "environment": args.environment, "collected_at": datetime.datetime.now(
                  datetime.timezone.utc).isoformat(), "checks": checks,
              "security_boundary": "GET-only; no admin credentials, Secrets, Pod env or writes",
              "rollout_source": "Must be correlated with the separate approved rollout receipt"}
    opener = urllib.request.build_opener(NoRedirect())

    def get(path, authenticated=True):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(args.base_url + path, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=15) as response:
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    return response.status, {"error": "response_bound_exceeded"}
                try:
                    return response.status, json.loads(data)
                except ValueError:
                    return response.status, {"error": "non_json_response"}
        except urllib.error.HTTPError as error:
            error.close()
            return error.code, {"error": "http_error"}
        except (OSError, urllib.error.URLError):
            return 0, {"error": "transport_error"}

    def save():
        statuses = [value["status"] for value in checks.values()]
        report["overall_status"] = ("FAIL" if "FAIL" in statuses
                                    else "NOT_RUN" if "NOT_RUN" in statuses else "PASS")
        return report

    code, body = get("/api/health", False)
    checks["endpoint_health"] = {"status": "PASS" if code == 200 and body.get("database") == "ok" else "FAIL",
                                  "http_status": code, "version": body.get("version")}
    code, _ = get("/api/datasources", False)
    checks["unauthenticated_boundary"] = {"status": "PASS" if code in [401, 403] else "FAIL", "http_status": code}
    if not token:
        checks["viewer_identity"] = {"status": "NOT_RUN", "reason": "Dedicated query identity not provided"}
        checks["live_queries"] = {"status": "NOT_RUN", "reason": "Do not substitute administrator credentials"}
        return save()
    code, user = get("/api/user")
    viewer = (code == 200 and not user.get("isGrafanaAdmin", True)
              and user.get("id") == expected_identity["id"]
              and user.get("orgId") == expected_identity["orgId"]
              and expected_identity["role"] == "Viewer")
    checks["viewer_identity"] = {"status": "PASS" if viewer else "FAIL",
                                  "http_status": code, "service_account_id": user.get("id"),
                                  "reason": "Token identity matches the separately verified Viewer service account"}
    if not viewer:
        checks["live_queries"] = {"status": "NOT_RUN", "reason": "Strict Viewer role not verified"}
        return save()
    code, permissions = get("/api/access-control/user/permissions")
    valid_permissions = (isinstance(permissions, dict) and "error" not in permissions
                         and all(isinstance(scopes, list) for scopes in permissions.values()))
    forbidden = [action for action, scopes in permissions.items()
                 if scopes and (action.endswith(":write") or action.endswith(":create")
                                or action.endswith(":delete") or action.endswith(":admin"))] if isinstance(permissions, dict) else []
    checks["permission_introspection"] = {
        "status": ("FAIL" if forbidden or not valid_permissions else "PASS") if code == 200 else "NOT_RUN",
        "http_status": code, "forbidden_actions": forbidden,
        "reason": "Permission introspection only; no production write/denial operation attempted"}
    if forbidden or (code == 200 and not valid_permissions):
        return save()
    code, search = get("/api/search?type=dash-db&limit=1000")
    uids = {item.get("uid") for item in search if item.get("type") == "dash-db"} if isinstance(search, list) else set()
    checks["dashboard_count"] = {"status": "PASS" if code == 200 and EXPECTED_UIDS <= uids else "FAIL",
                                  "http_status": code, "unique_count": len(uids),
                                  "missing_uids": sorted(EXPECTED_UIDS - uids)}
    code, payload = get("/api/dashboards/uid/StarsL_cn_K8S")
    dashboard = payload.get("dashboard", {})
    variables = [item.get("name") for item in dashboard.get("templating", {}).get("list", [])]
    checks["starsl_dashboard_model"] = {"status": "PASS" if code == 200 and variables[:3] == ["DS_METRICS", "cluster", "environment"] else "FAIL",
                                        "http_status": code, "variables": variables}
    scope = 'cluster=' + json.dumps(args.cluster) + ',environment=' + json.dumps(args.environment)
    metrics = {"ksm_node": "kube_node_info", "ksm_pod": "kube_pod_info",
               "node_cpu": "node_cpu_seconds_total", "node_memory": "node_memory_MemAvailable_bytes",
               "process_groups": "namedprocess_namegroup_num_procs", "cadvisor_cpu": "container_cpu_usage_seconds_total",
               "cadvisor_memory": "container_memory_working_set_bytes"}
    proxy = "/api/datasources/proxy/uid/" + args.datasource_uid + "/api/v1/"

    def query(expression, range_query=False):
        params = {"query": expression}
        if range_query:
            end = time.time()
            params.update(start=str(end - 1800), end=str(end), step="60")
        code, payload = get(proxy + ("query_range" if range_query else "query") + "?" + urllib.parse.urlencode(params))
        if code == 200 and payload.get("status") != "success":
            return 0, {"error": "prometheus_query_not_successful"}
        return code, payload

    code, payload = query("kube_node_info{" + scope + "}")
    result = payload.get("data", {}).get("result", [])
    expected_nodes = {row.get("metric", {}).get("node") for row in result if row.get("metric", {}).get("node")}
    checks["node_inventory"] = {"status": "PASS" if code == 200 and expected_nodes else "FAIL",
                                 "http_status": code, "node_count": len(expected_nodes)}

    for key, metric in metrics.items():
        expr = metric + "{" + scope + "}"
        code, payload = query("time() - max by(node)(timestamp(" + expr + "))")
        result = payload.get("data", {}).get("result", [])
        per_node = {row.get("metric", {}).get("node"): float(row["value"][1])
                    for row in result if row.get("metric", {}).get("node")} if code == 200 else {}
        valid = (expected_nodes and expected_nodes <= set(per_node)
                 and all(0 <= per_node[node] <= 180 for node in expected_nodes))
        checks[key] = {"status": "PASS" if valid else "FAIL", "http_status": code,
                       "worst_age_seconds": max(per_node.values()) if per_node else None,
                       "missing_node_count": len(expected_nodes - set(per_node))}
    for job in ["kube-state-metrics", "node-exporter", "process-exporter", "kubelet", "cadvisor"]:
        code, payload = query("min(up{" + scope + ',job=' + json.dumps(job) + "})")
        result = payload.get("data", {}).get("result", [])
        value = float(result[0]["value"][1]) if code == 200 and result else None
        checks["scrape_" + job] = {"status": "PASS" if value == 1 else "FAIL", "http_status": code, "value": value}
        if job != "kube-state-metrics":
            node_code, node_payload = query("up{" + scope + ',job=' + json.dumps(job) + "} == 1")
            nodes = {row.get("metric", {}).get("node") for row in node_payload.get("data", {}).get("result", [])}
            checks["nodes_" + job] = {"status": "PASS" if node_code == 200 and expected_nodes and nodes == expected_nodes else "FAIL",
                                       "expected_count": len(expected_nodes), "reporting_count": len(nodes),
                                       "missing_count": len(expected_nodes - nodes), "unexpected_count": len(nodes - expected_nodes)}
    code, payload = query('kube_node_info{cluster="__nonexistent_obs_probe__",environment=' + json.dumps(args.environment) + "}")
    result = payload.get("data", {}).get("result", [])
    checks["nonexistent_cluster"] = {"status": "PASS" if code == 200 and not result else "FAIL", "http_status": code}
    code, payload = query("kube_node_info{cluster=" + json.dumps(args.cluster) + ',environment="__invalid_env__"}')
    result = payload.get("data", {}).get("result", [])
    checks["nonexistent_environment"] = {"status": "PASS" if code == 200 and not result else "FAIL", "http_status": code}
    code, payload = query("count by(node,namespace,pod,container)(container_memory_working_set_bytes{" + scope + ',container!=""}) > 1')
    result = payload.get("data", {}).get("result", [])
    checks["duplicate_container_memory"] = {"status": "PASS" if code == 200 and not result else "FAIL", "duplicate_count": len(result)}
    code, payload = query('container_memory_working_set_bytes{' + scope + ',job!="cadvisor"}')
    result = payload.get("data", {}).get("result", [])
    checks["noncanonical_container_memory"] = {"status": "PASS" if code == 200 and not result else "FAIL",
                                                "noncanonical_series_count": len(result)}
    code, payload = query("max(namedprocess_scrape_errors{" + scope + '})')
    result = payload.get("data", {}).get("result", [])
    value = float(result[0]["value"][1]) if code == 200 and result else None
    checks["process_scrape_errors"] = {"status": "PASS" if value == 0 else "FAIL", "value": value}
    code, overview = get("/api/dashboards/uid/web-saas-k3s-v5-overview")
    candidates = [target.get("expr") for panel in overview.get("dashboard", {}).get("panels", [])
                  for target in panel.get("targets", []) if target.get("expr")][:3]
    overview_results = []
    for expression in candidates:
        for variable, selected in [("cluster", args.cluster), ("environment", args.environment)]:
            for form in ["${" + variable + ":regex}", "${" + variable + "}", "$" + variable]:
                expression = expression.replace(form, selected)
        query_code, query_payload = query(expression)
        overview_results.append(query_code == 200 and bool(query_payload.get("data", {}).get("result", [])))
    checks["original_k3s_overview"] = {"status": "PASS" if code == 200 and len(overview_results) == 3 and all(overview_results) else "FAIL",
                                       "query_count": len(overview_results), "passing_queries": sum(overview_results)}
    for key, metric in {"ksm": "kube_node_info", "node": "node_cpu_seconds_total", "process": "namedprocess_namegroup_num_procs", "cadvisor": "container_memory_working_set_bytes"}.items():
        # Retain source sample timestamps; count(metric) over a range can hide
        # a stopped producer behind the backend's lookback interval.
        code, payload = query("max by(node)(timestamp(" + metric + "{" + scope + "}))", True)
        result = payload.get("data", {}).get("result", [])
        coverages, gaps = [], []
        for series in result if code == 200 else []:
            timestamps = sorted({float(point[1]) for point in series.get("values", [])})
            gaps.append(max([b - a for a, b in zip(timestamps, timestamps[1:])] or [1801]))
            coverages.append(timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0)
        gap = max(gaps or [1801])
        covered = min(coverages or [0])
        checks["continuity_" + key] = {"status": "PASS" if covered >= 1740 and gap <= 180 else "FAIL",
                                       "series_count": len(result), "covered_seconds": covered, "max_source_sample_gap_seconds": gap}
    checks["pvc_statistics"] = {"status": "NOT_RUN", "reason": "Optional: requires volume-plugin support and a real PVC; not inferred as healthy zero"}
    return save()



