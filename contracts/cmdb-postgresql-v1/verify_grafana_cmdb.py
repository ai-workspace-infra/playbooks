#!/usr/bin/env python3
"""Verify every CMDB dashboard query via Grafana using its runtime identity."""
import argparse
import base64
import json
import os
import re
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--grafana-container", required=True)
    parser.add_argument("--datasource-uid", required=True)
    parser.add_argument("--dashboard-uid", required=True)
    args = parser.parse_args()
    if not args.endpoint.startswith("http://127.0.0.1:"):
        parser.error("direct UAT validation must use Grafana loopback")
    result = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", args.grafana_container], check=True, capture_output=True, text=True)
    environment = dict(entry.split("=", 1) for entry in json.loads(result.stdout) if "=" in entry)
    user = os.environ.get("GRAFANA_USER", environment.get("GF_SECURITY_ADMIN_USER", "admin"))
    password = os.environ.get("GRAFANA_PASSWORD", environment.get("GF_SECURITY_ADMIN_PASSWORD"))
    if not password:
        raise RuntimeError("Grafana runtime authentication is unavailable")
    authorization = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def api(path, body=None):
        request = urllib.request.Request(args.endpoint + path, data=json.dumps(body).encode() if body else None,
                                         headers={"Authorization": authorization, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)

    datasource = api("/api/datasources/uid/" + args.datasource_uid)
    if datasource.get("user") != "cmdb_reader" or datasource.get("database", datasource.get("jsonData", {}).get("database")) != "cmdb":
        raise RuntimeError("unexpected datasource database or reader identity")
    health = api("/api/datasources/uid/" + args.datasource_uid + "/health")
    if health.get("status") != "OK":
        raise RuntimeError("datasource health failed")
    dashboard = api("/api/dashboards/uid/" + args.dashboard_uid)["dashboard"]
    results = []
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            sql = target.get("rawSql")
            if not sql:
                continue
            if not re.match(r"^\s*SELECT\b", sql, re.I) or ";" in sql.rstrip().rstrip(";"):
                raise RuntimeError("dashboard contains an unexpected SQL statement")
            if target.get("datasource", panel.get("datasource", {})).get("uid") != args.datasource_uid:
                raise RuntimeError("dashboard targets a different datasource")
            query = dict(target, datasource={"uid": args.datasource_uid, "type": "postgres"}, datasourceId=datasource["id"], intervalMs=60000, maxDataPoints=1000)
            response = api("/api/ds/query", {"queries": [query], "from": "now-1h", "to": "now"})
            ref = response["results"][query["refId"]]
            if ref.get("error") or ref.get("status", 200) >= 400 or not ref.get("frames"):
                raise RuntimeError("CMDB dashboard query failed")
            values = ref["frames"][0].get("data", {}).get("values", [])
            receipt = {"panel": panel["id"], "status": "ok", "rows": max((len(value) for value in values), default=0)}
            if panel.get("type") == "stat" and values:
                receipt["value"] = values[0][0] if values[0] else None
            results.append(receipt)
    if not results:
        raise RuntimeError("no CMDB queries verified")
    print(json.dumps({"datasource_health": "OK", "reader": datasource["user"], "dashboard": dashboard["uid"], "queries": results}, indent=2))


if __name__ == "__main__":
    main()
