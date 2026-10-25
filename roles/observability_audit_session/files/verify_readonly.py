#!/usr/bin/env python3
"""Bounded GET-only StarsL/exporter checks, using an explicit Viewer identity."""
import datetime
import hashlib
import json
import re
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


def scan_alert_dependencies(get, permissions):
    """Read only rule configuration; never retain expression/annotation text."""
    endpoints = [("ruler", "/api/ruler/grafana/api/v1/rules"),
                 ("provisioning", "/api/v1/provisioning/alert-rules")]
    sources, summaries = [], []
    for source, path in endpoints:
        code, payload = get(path)
        parsed, rules = True, []
        try:
            if code != 200:
                raise ValueError("unavailable")
            if source == "provisioning":
                if not isinstance(payload, list):
                    raise ValueError("unexpected_schema")
                rules = payload
            else:
                if not isinstance(payload, dict) or "error" in payload or "message" in payload:
                    raise ValueError("unexpected_schema")
                for groups in payload.values():
                    if not isinstance(groups, list):
                        raise ValueError("unexpected_groups")
                    for group in groups:
                        if not isinstance(group, dict) or not isinstance(group.get("rules"), list):
                            raise ValueError("unexpected_rules")
                        rules.extend(group["rules"])
            for item in rules:
                if not isinstance(item, dict):
                    raise ValueError("unexpected_rule")
                rule = item.get("grafana_alert", item)
                uid = rule.get("uid")
                if not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", uid):
                    raise ValueError("missing_rule_uid")
                expressions = []
                if isinstance(item.get("expr"), str) and item["expr"].strip():
                    expressions.append(item["expr"])
                for entry in rule.get("data", []):
                    model = entry.get("model", {})
                    for key in ["expr", "expression", "query"]:
                        value = model.get(key)
                        if isinstance(value, str) and value.strip():
                            expressions.append(value)
                if not expressions:
                    raise ValueError("rule_expression_unavailable")
                joined = "\n".join(expressions)
                metrics = sorted(set(re.findall(r"\bcontainer_[A-Za-z0-9_:]+", joined)))
                labels = sorted(set(re.findall(r"\bk8s_(?:namespace_name|pod_name|container_name|node_name)\b", joined)))
                if re.search(r'\bjob\s*=~?\s*"[^"\n]*otel[^"\n]*"', joined):
                    labels.append("job_otel_selector")
                possible_unique = [metric for metric in metrics if re.match(
                    r"container_(?:cpu_(?:time|utilization)|filesystem_|memory_(?:available|rss_bytes|page_faults|major_page_faults))", metric)]
                dynamic = bool(re.search(r'__name__\s*=~\s*"[^"\n]*container_', joined))
                summaries.append({"source": source, "rule_uid": uid,
                                  "expr_sha256": sorted({hashlib.sha256(expr.encode()).hexdigest() for expr in expressions}),
                                  "metric_matches": metrics, "label_matches": labels,
                                  "dependency_candidate": bool(possible_unique or dynamic or (metrics and labels)),
                                  "shared_metric_review": bool(metrics and not possible_unique and not labels)})
        except (ValueError, KeyError, TypeError, AttributeError):
            parsed = False
        sources.append({"source": source, "http_status": code, "parsed": parsed,
                        "visible_rule_count": len(rules) if parsed else None})
    def global_scope(action, allowed):
        return any(scope in allowed for scope in permissions.get(action, []))
    ruler_complete = (global_scope("alert.rules:read", {"*", "folders:*", "folders:uid:*"})
                      and global_scope("folders:read", {"*", "folders:*", "folders:uid:*"})
                      and global_scope("datasources:query", {"*", "datasources:*", "datasources:uid:*"}))
    provisioning_complete = bool(permissions.get("alert.rules.provisioning:read") or permissions.get("alert.provisioning:read"))
    complete = any(source["http_status"] == 200 and source["parsed"]
                   and (ruler_complete if source["source"] == "ruler" else provisioning_complete)
                   for source in sources)
    dependencies = any(item["dependency_candidate"] for item in summaries)
    shared_review = any(item["shared_metric_review"] for item in summaries)
    return {"status": "FAIL" if dependencies else "PASS" if complete and not shared_review else "NOT_RUN",
            "scope_completeness_verified": complete, "sources": sources, "rules": summaries,
            "reason": ("OTel container metric/label dependency candidate requires review" if dependencies
                       else "Shared container metrics require actual old/new metric-set comparison" if shared_review
                       else "Full permitted rule scope scanned with no container dependency matches" if complete
                       else "Viewer rule visibility/completeness not established; no administrator fallback")}


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
    expected_id = expected_identity["id"]
    expected_uid = "service-account:" + str(expected_id)
    user_uid = user.get("uid", "")
    numeric_id_matches = type(user.get("id")) is int and user.get("id") == expected_id
    service_account_uid_matches = (type(user.get("id")) is int and user.get("id") == 0
                                   and user_uid == expected_uid)
    conflicting_sa_uid = isinstance(user_uid, str) and user_uid.startswith("service-account:") and not service_account_uid_matches
    login_matches = bool(expected_identity.get("login")) and user.get("login") == expected_identity["login"]
    name_matches = bool(expected_identity.get("name")) and user.get("name") == expected_identity["name"]
    org_matches = user.get("orgId") == expected_identity["orgId"]
    viewer = (code == 200 and type(expected_id) is int and expected_id > 0
              and user.get("isGrafanaAdmin") is False
              and (numeric_id_matches or service_account_uid_matches) and not conflicting_sa_uid
              and login_matches and name_matches and org_matches
              and expected_identity["role"] == "Viewer")
    checks["viewer_identity"] = {"status": "PASS" if viewer else "FAIL",
                                  "http_status": code, "service_account_id": user.get("id"),
                                  "numeric_id_matches": numeric_id_matches,
                                  "service_account_uid_matches": service_account_uid_matches,
                                  "login_matches": login_matches, "name_matches": name_matches, "org_matches": org_matches,
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
    checks["alert_rule_dependencies"] = scan_alert_dependencies(get, permissions if valid_permissions else {})
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



