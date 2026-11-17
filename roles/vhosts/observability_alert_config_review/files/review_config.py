#!/usr/bin/env python3
"""Authorized system-management configuration review: three GET routes only."""
import argparse
import base64
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

_path = Path(__file__).resolve().parents[2] / "observability_audit_session/files/session.py"
_spec = importlib.util.spec_from_file_location("observability_vault_runtime", _path)
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)


class ConfigClient:
    """No datasource/telemetry API, arbitrary route, redirects or write methods."""
    def __init__(self, origin, username, password):
        self.origin = origin
        self.authorization = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        self.opener = urllib.request.build_opener(runtime.NoRedirect())

    def request(self, method, path):
        if method != "GET" or path not in ["/api/user", "/api/org", "/api/v1/provisioning/alert-rules"]:
            raise runtime.SessionError("management_configuration_route_not_allowed")
        request = urllib.request.Request(self.origin + path, method="GET", headers={
            "Authorization": self.authorization, "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise runtime.SessionError("configuration_response_exceeds_bound")
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            error.close()
            raise runtime.SessionError("configuration_http_" + str(error.code)) from None
        except (ValueError, OSError, urllib.error.URLError):
            raise runtime.SessionError("configuration_transport_or_json_error") from None


def validate(args):
    for origin in [args.base_url, os.environ.get("VAULT_ADDR", "")]:
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", origin):
            raise runtime.SessionError("explicit_https_origins_required")
    if os.environ.get("VAULT_SKIP_VERIFY", "").lower() in ["true", "1"]:
        raise runtime.SessionError("vault_tls_verification_required")
    if (not re.fullmatch(r"[A-Za-z0-9_-]+/(?:sit|uat|prod)/[A-Za-z0-9_./-]+", args.vault_path)
            or ".." in args.vault_path.split("/") or args.vault_path.split("/")[1] != args.environment):
        raise runtime.SessionError("explicit_matching_environment_vault_path_required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.password_field):
        raise runtime.SessionError("explicit_password_field_required")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", args.admin_username):
        raise runtime.SessionError("explicit_management_username_required")
    if type(args.expected_org_id) is not int or args.expected_org_id <= 0:
        raise runtime.SessionError("explicit_expected_org_id_required")
    if not re.fullmatch(r"[A-Za-z0-9_./:#-]{1,300}", args.management_evidence_ref):
        raise runtime.SessionError("preverified_management_identity_and_scope_evidence_required")


def summarize_rules(rules):
    if not isinstance(rules, list):
        raise runtime.SessionError("rule_list_schema_not_verified")
    summaries, unresolved = [], False
    for rule in rules:
        if not isinstance(rule, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(rule.get("uid", ""))):
            raise runtime.SessionError("rule_identity_schema_not_verified")
        expressions = []
        data = rule.get("data")
        if not isinstance(data, list):
            unresolved = True
            data = []
        for query in data:
            model = query.get("model", {}) if isinstance(query, dict) else {}
            for key in ["expr", "expression", "query"]:
                value = model.get(key) if isinstance(model, dict) else None
                if isinstance(value, str) and value.strip():
                    expressions.append(value)
        if not expressions:
            unresolved = True
        joined = "\n".join(expressions)
        metrics = sorted(set(re.findall(r"\bcontainer_[A-Za-z0-9_:]+", joined)))
        labels = sorted(set(re.findall(r"\bk8s_(?:namespace_name|pod_name|container_name|node_name)\b", joined)))
        if re.search(r'\bjob\s*=~?\s*"[^"\n]*otel[^"\n]*"', joined):
            labels.append("job_otel_selector")
        possible_unique = [metric for metric in metrics if re.match(
            r"container_(?:cpu_(?:time|utilization)|filesystem_|memory_(?:available|rss_bytes|page_faults|major_page_faults))", metric)]
        dynamic = bool(re.search(r'__name__\s*=~\s*"[^"\n]*container', joined))
        summaries.append({"rule_uid": rule["uid"],
                          "expr_sha256": sorted({hashlib.sha256(expr.encode()).hexdigest() for expr in expressions}),
                          "metric_matches": metrics, "label_matches": labels,
                          "dependency_candidate": bool(possible_unique or dynamic or (metrics and labels)),
                          "shared_metric_review": bool(metrics and not possible_unique and not labels)})
    dependencies = any(item["dependency_candidate"] for item in summaries)
    shared_review = any(item["shared_metric_review"] for item in summaries)
    return {"status": "FAIL" if dependencies else "NOT_RUN" if unresolved or shared_review else "PASS",
            "rule_count": len(rules), "rules": summaries,
            "reason": ("OTel container dependency candidate blocks the planned change" if dependencies
                       else "Rule schema or shared-container metric compatibility needs further review" if unresolved or shared_review
                       else "No OTel container dependency candidates in this management-reviewed organization configuration")}


def review(args, credential_reader=runtime.vault_password, client_factory=ConfigClient):
    validate(args)
    report = {"schema_version": "observability-management-config-review/v1",
              "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "actor_class": "system_management_configuration_review",
              "identity_scope_basis": "Actual management self-introspection must prove exact login/org/server-admin; caller authorization recorded separately",
              "management_identity_evidence_ref": args.management_evidence_ref,
              "target_origin": args.base_url, "environment": args.environment,
              "expected_org_id": args.expected_org_id,
              "is_viewer_scope_attestation": False, "is_business_data_acceptance": False,
              "mutation_performed": False, "runtime_credentials_persisted": False,
              "management_identity": {"status": "NOT_RUN"},
              "organization": {"status": "NOT_RUN"}, "rule_compatibility": {"status": "NOT_RUN"}}
    client, sensitive = None, []
    try:
        password = credential_reader(args.vault_path, args.password_field)
        sensitive.append(password)
        client = client_factory(args.base_url, args.admin_username, password)
        sensitive.append(client.authorization)
        password = None
        identity = client.request("GET", "/api/user")
        login_matches = isinstance(identity, dict) and identity.get("login") == args.admin_username
        org_matches = (isinstance(identity, dict) and type(identity.get("orgId")) is int
                       and identity.get("orgId") == args.expected_org_id)
        server_admin = isinstance(identity, dict) and identity.get("isGrafanaAdmin") is True
        verified = login_matches and org_matches and server_admin
        report["management_identity"] = {"status": "PASS" if verified else "FAIL",
                                          "login_matches": login_matches, "org_matches": org_matches,
                                          "server_admin_verified": server_admin}
        if not verified:
            raise runtime.SessionError("management_identity_and_full_org_scope_not_verified")
        org = client.request("GET", "/api/org")
        if not isinstance(org, dict) or type(org.get("id")) is not int or org["id"] != args.expected_org_id:
            raise runtime.SessionError("management_organization_does_not_match_expected")
        report["organization"] = {"status": "PASS", "org_id": org["id"]}
        rules = client.request("GET", "/api/v1/provisioning/alert-rules")
        report["rule_compatibility"] = summarize_rules(rules)
        report["configuration_scope"] = "Actual verified server-administrator identity, current expected organization only"
    except runtime.SessionError as error:
        report["error_code"] = error.args[0]
        report["overall_status"] = "FAIL"
    except Exception:
        report["error_code"] = "configuration_review_exception"
        report["overall_status"] = "FAIL"
    finally:
        if client is not None:
            client.authorization = None
    if "overall_status" not in report:
        report["overall_status"] = report["rule_compatibility"]["status"]
    def scrub(value):
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            for secret in sensitive:
                if secret:
                    value = value.replace(secret, "<redacted>")
        return value
    return scrub(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--vault-path", required=True)
    parser.add_argument("--password-field", required=True)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--expected-org-id", type=int, required=True)
    parser.add_argument("--management-evidence-ref", required=True)
    parser.add_argument("--environment", required=True, choices=["sit", "uat", "prod"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        parser.error("An absolute sanitized evidence output path is required")
    try:
        report = review(args)
    except runtime.SessionError as error:
        report = {"overall_status": "FAIL", "preflight_error_code": error.args[0]}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    output.chmod(0o600)
    print(json.dumps({"overall_status": report["overall_status"], "evidence_written": True}))
    return 0 if report["overall_status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
