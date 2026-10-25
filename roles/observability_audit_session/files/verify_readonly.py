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


def scan_alert_dependencies(get, permissions, grafana_version=""):
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
    all_folder_rules = (global_scope("alert.rules:read", {"*", "folders:*", "folders:uid:*"})
                        and global_scope("folders:read", {"*", "folders:*", "folders:uid:*"}))
    ruler_complete = (all_folder_rules
                      and global_scope("datasources:query", {"*", "datasources:*", "datasources:uid:*"}))
    provisioning_read = bool(permissions.get("alert.rules.provisioning:read") or permissions.get("alert.provisioning:read"))
    # Exact running-version source proof: Grafana 13.2.4's provisioning
    # GetAlertRules returns all org rules when CanReadAllRules holds; that
    # evaluator requires only both all-folder reads, not datasource query.
    provisioning_blanket = grafana_version == "13.2.4" and all_folder_rules
    provisioning_complete = provisioning_read or provisioning_blanket
    complete = any(source["http_status"] == 200 and source["parsed"]
                   and (ruler_complete if source["source"] == "ruler" else provisioning_complete)
                   for source in sources)
    dependencies = any(item["dependency_candidate"] for item in summaries)
    shared_review = any(item["shared_metric_review"] for item in summaries)
    return {"status": "FAIL" if dependencies else "PASS" if complete and not shared_review else "NOT_RUN",
            "scope_completeness_verified": complete, "sources": sources, "rules": summaries,
            "grafana_version": grafana_version,
            "provisioning_completeness_basis": ("explicit_provisioning_read" if provisioning_read
                                                  else "grafana_13.2.4_all_folder_rule_and_folder_read" if provisioning_blanket
                                                  else "NOT_VERIFIED"),
            "read_scope_evidence": {action: permissions.get(action, []) for action in [
                "alert.rules:read", "folders:read", "datasources:query",
                "alert.rules.provisioning:read", "alert.provisioning:read"]},
            "reason": ("OTel container metric/label dependency candidate requires review" if dependencies
                       else "Shared container metrics require actual old/new metric-set comparison" if shared_review
                       else "Full permitted rule scope scanned with no container dependency matches" if complete
                       else "Viewer rule visibility/completeness not established; no administrator fallback")}


def summarize_targets(code, payload, cluster, environment, expected_nodes, now=None):
    """Reduce target metadata immediately; never retain URLs, labels or errors."""
    now = time.time() if now is None else now
    data = payload.get("data") if isinstance(payload, dict) else None
    targets = data.get("activeTargets") if isinstance(data, dict) else None
    if code != 200 or not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(targets, list):
        return {"status": "NOT_RUN", "http_status": code,
                "reason": "VM active-target capability unavailable or invalid; empty targets not assumed"}
    jobs = ["node-exporter", "process-exporter", "kube-state-metrics", "kubelet", "cadvisor"]
    scoped = [target for target in targets if isinstance(target, dict) and isinstance(target.get("labels"), dict)
              and target["labels"].get("cluster") == cluster and target["labels"].get("environment") == environment
              and target["labels"].get("job") in jobs]
    malformed = sum(not isinstance(target, dict) or not isinstance(target.get("labels"), dict) for target in targets)
    summaries = {}
    for job in jobs:
        selected = []
        for target in scoped:
            labels = target["labels"]
            if labels.get("cluster") == cluster and labels.get("environment") == environment and labels.get("job") == job:
                selected.append(target)
        counts = {}
        healthy = fresh = 0
        for target in selected:
            node = target["labels"].get("node", "")
            counts[node] = counts.get(node, 0) + 1
            healthy += target.get("health") == "up" and target.get("lastError", "") == ""
            try:
                stamp = datetime.datetime.fromisoformat(target["lastScrape"].replace("Z", "+00:00"))
                age = now - stamp.timestamp()
                fresh += 0 <= age <= 180
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        expected_count = 1 if job == "kube-state-metrics" else len(expected_nodes)
        nodes_ok = job == "kube-state-metrics" or set(counts) == expected_nodes
        duplicates = sum(max(0, count - 1) for count in counts.values())
        ok = len(selected) == expected_count and healthy == expected_count and fresh == expected_count and nodes_ok and not duplicates
        summaries[job] = {"status": "PASS" if ok else "FAIL", "active_count": len(selected),
                          "expected_count": expected_count, "healthy_count": healthy, "fresh_count": fresh,
                          "duplicate_node_target_count": duplicates,
                          "missing_node_count": 0 if job == "kube-state-metrics" else len(expected_nodes - set(counts)),
                          "unexpected_node_count": 0 if job == "kube-state-metrics" else len(set(counts) - expected_nodes)}
    return {"status": "PASS" if not malformed and all(v["status"] == "PASS" for v in summaries.values()) else "FAIL",
            "http_status": code, "jobs": summaries, "malformed_target_count": malformed,
            "scope": "Exact caller cluster/environment and externally verified node set; target content discarded"}


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
    conflicting_sa_uid = (isinstance(user_uid, str) and user_uid.startswith("service-account:")
                          and user_uid != expected_uid)
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
    checks["alert_rule_dependencies"] = scan_alert_dependencies(
        get, permissions if valid_permissions else {}, checks["endpoint_health"].get("version", ""))
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
    metrics = {"ksm_node": ("kube_node_info", "kube-state-metrics"),
               "ksm_pod": ("kube_pod_info", "kube-state-metrics"),
               "node_cpu": ("node_cpu_seconds_total", "node-exporter"),
               "node_memory": ("node_memory_MemAvailable_bytes", "node-exporter"),
               "process_groups": ("namedprocess_namegroup_num_procs", "process-exporter"),
               "process_cpu": ("namedprocess_namegroup_cpu_seconds_total", "process-exporter"),
               "cadvisor_cpu": ("container_cpu_usage_seconds_total", "cadvisor"),
               "cadvisor_memory": ("container_memory_working_set_bytes", "cadvisor"),
               "cadvisor_rss": ("container_memory_rss", "cadvisor")}
    expected_nodes = set(getattr(args, "expected_node", []) or [])
    if not expected_nodes:
        checks["external_node_inventory"] = {"status": "NOT_RUN", "reason": "Separately verified expected-node input is required"}
        return save()
    checks["external_node_inventory"] = {"status": "PASS", "expected_node_count": len(expected_nodes),
                                          "provenance": "Explicit caller input; not inferred from KSM"}

    def selector(metric, job):
        application = ',container!="",container!="POD"' if job == "cadvisor" else ""
        return metric + "{" + scope + ',job=' + json.dumps(job) + application + "}"
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

    code, payload = get(proxy + "targets?state=active")
    checks["active_targets"] = summarize_targets(code, payload, args.cluster, args.environment, expected_nodes)

    code, payload = query(selector("kube_node_info", "kube-state-metrics"))
    result = payload.get("data", {}).get("result", [])
    observed_nodes = {row.get("metric", {}).get("node") for row in result if row.get("metric", {}).get("node")}
    checks["node_inventory"] = {"status": "PASS" if code == 200 and observed_nodes == expected_nodes else "FAIL",
                                 "http_status": code, "node_count": len(observed_nodes),
                                 "expected_node_count": len(expected_nodes),
                                 "missing_node_count": len(expected_nodes - observed_nodes),
                                 "unexpected_node_count": len(observed_nodes - expected_nodes)}

    for key, (metric, job) in metrics.items():
        expr = selector(metric, job)
        code, payload = query("time() - max by(node)(timestamp(" + expr + "))")
        result = payload.get("data", {}).get("result", [])
        per_node = {row.get("metric", {}).get("node"): float(row["value"][1])
                    for row in result if row.get("metric", {}).get("node")} if code == 200 else {}
        valid = (expected_nodes and expected_nodes == set(per_node)
                 and all(0 <= per_node[node] <= 180 for node in expected_nodes))
        checks[key] = {"status": "PASS" if valid else "FAIL", "http_status": code, "canonical_job": job,
                       "worst_age_seconds": max(per_node.values()) if per_node else None,
                       "missing_node_count": len(expected_nodes - set(per_node)),
                       "unexpected_node_count": len(set(per_node) - expected_nodes)}
    for job in ["kube-state-metrics", "node-exporter", "process-exporter", "kubelet", "cadvisor"]:
        code, payload = query("min(up{" + scope + ',job=' + json.dumps(job) + "})")
        result = payload.get("data", {}).get("result", [])
        value = float(result[0]["value"][1]) if code == 200 and result else None
        checks["scrape_" + job] = {"status": "PASS" if value == 1 else "FAIL", "http_status": code, "value": value}
        age_code, age_payload = query("time() - min(timestamp(up{" + scope + ',job=' + json.dumps(job) + "}))")
        age_rows = age_payload.get("data", {}).get("result", [])
        age = float(age_rows[0]["value"][1]) if age_code == 200 and age_rows else None
        checks["scrape_freshness_" + job] = {"status": "PASS" if age is not None and 0 <= age <= 180 else "FAIL",
                                              "http_status": age_code, "worst_age_seconds": age}
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
    # A recent-sample predicate excludes old OTel samples still within VM lookback.
    # CPU's cpu dimension is intentional: separate per-CPU series are not duplicates.
    application = ',namespace!="",pod!="",container!="",container!="POD"'
    for key, metric in [("memory", "container_memory_working_set_bytes"),
                        ("cpu", "container_cpu_usage_seconds_total"), ("rss", "container_memory_rss")]:
        raw = metric + "{" + scope + application + "}"
        recent = raw + " and (time() - timestamp(" + raw + ") <= 45)"
        grouping = "node,namespace,pod,container" + (",cpu" if key == "cpu" else "")
        code, payload = query("count by(" + grouping + ")(" + recent + ") > 1")
        rows = payload.get("data", {}).get("result", [])
        checks["duplicate_container_" + key] = {"status": "PASS" if code == 200 and not rows else "FAIL",
                                                "http_status": code, "duplicate_count": len(rows), "sample_max_age_seconds": 45}
        noncanonical = metric + "{" + scope + ',job!="cadvisor"}'
        code, payload = query(noncanonical + " and (time() - timestamp(" + noncanonical + ") <= 45)")
        rows = payload.get("data", {}).get("result", [])
        checks["noncanonical_container_" + key] = {"status": "PASS" if code == 200 and not rows else "FAIL",
                                                   "http_status": code, "noncanonical_series_count": len(rows),
                                                   "sample_max_age_seconds": 45}
    for key, metric in [("process_scrape_errors", "namedprocess_scrape_errors"),
                        ("process_partial_errors", "namedprocess_scrape_partial_errors"),
                        ("process_procread_errors", "namedprocess_scrape_procread_errors"),
                        ("process_positive_process_count", "namedprocess_namegroup_num_procs")]:
        process = selector(metric, "process-exporter")
        code, payload = query("sum by(node)(" + process + " and (time() - timestamp(" + process + ") <= 45))")
        rows = payload.get("data", {}).get("result", [])
        values = {row.get("metric", {}).get("node"): float(row["value"][1]) for row in rows} if code == 200 else {}
        covered = set(values) == expected_nodes
        positive = key == "process_positive_process_count"
        valid = covered and all(value > 0 if positive else value == 0 for value in values.values())
        partial = key in ["process_partial_errors", "process_procread_errors"]
        checks[key] = {"status": "PASS" if valid else "NOT_RUN" if partial else "FAIL",
                       "http_status": code, "reporting_node_count": len(values),
                       "missing_node_count": len(expected_nodes - set(values)),
                       "unexpected_node_count": len(set(values) - expected_nodes),
                       "total": sum(values.values()) if values else None,
                       "reason": ("Nonroot proc visibility limitation or missing evidence; full process coverage unverified"
                                  if partial and not valid else "Per-node process evidence; no permission expansion")}
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
    for key, (metric, job) in {"ksm": ("kube_node_info", "kube-state-metrics"),
                              "node": ("node_cpu_seconds_total", "node-exporter"),
                              "process": ("namedprocess_namegroup_num_procs", "process-exporter"),
                              "cadvisor": ("container_memory_working_set_bytes", "cadvisor")}.items():
        # Retain source sample timestamps; count(metric) over a range can hide
        # a stopped producer behind the backend's lookback interval.
        code, payload = query("max by(node)(timestamp(" + selector(metric, job) + "))", True)
        result = payload.get("data", {}).get("result", [])
        per_node = {}
        for series in result if code == 200 else []:
            timestamps = sorted({float(point[1]) for point in series.get("values", [])})
            node = series.get("metric", {}).get("node")
            per_node[node] = {"gap": max([b - a for a, b in zip(timestamps, timestamps[1:])] or [1801]),
                              "covered": timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0}
        gap = max([per_node.get(node, {"gap": 1801})["gap"] for node in expected_nodes])
        covered = min([per_node.get(node, {"covered": 0})["covered"] for node in expected_nodes])
        node_set_matches = set(per_node) == expected_nodes
        checks["continuity_" + key] = {"status": "PASS" if code == 200 and node_set_matches and covered >= 1740 and gap <= 180 else "FAIL",
                                       "canonical_job": job, "series_count": len(result),
                                       "expected_node_count": len(expected_nodes),
                                       "missing_node_count": len(expected_nodes - set(per_node)),
                                       "unexpected_node_count": len(set(per_node) - expected_nodes),
                                       "covered_seconds": covered, "max_source_sample_gap_seconds": gap}
    checks["pvc_statistics"] = {"status": "NOT_RUN", "reason": "Optional: requires volume-plugin support and a real PVC; not inferred as healthy zero"}
    return save()



