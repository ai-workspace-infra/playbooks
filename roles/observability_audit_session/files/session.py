#!/usr/bin/env python3
"""Bounded Grafana Viewer session; Vault credentials and tokens stay in memory."""
import argparse
import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import urllib.error
import urllib.parse
import urllib.request


class SessionError(Exception):
    """Only a fixed, credential-free error code may cross the session boundary."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Grafana:
    def __init__(self, origin, username, password):
        self.origin = origin
        self.authorization = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        allowed = (
            method == "GET" and (path.startswith("/api/serviceaccounts/search?")
                                  or re.fullmatch(r"/api/serviceaccounts/[1-9][0-9]*(?:/tokens)?", path))
            or method == "POST" and (path == "/api/serviceaccounts"
                                     or re.fullmatch(r"/api/serviceaccounts/[1-9][0-9]*/tokens", path))
            or method == "DELETE" and re.fullmatch(r"/api/serviceaccounts/[1-9][0-9]*/tokens/[1-9][0-9]*", path)
        )
        if not allowed or ".." in path:
            raise SessionError("management_path_not_allowed")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.origin + path, data=data, method=method,
                                         headers={"Authorization": self.authorization,
                                                  "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise SessionError("management_response_too_large")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise SessionError("management_http_" + str(error.code)) from None
        except (ValueError, OSError, urllib.error.URLError):
            raise SessionError("management_transport_or_json_error") from None


def vault_password(path, field):
    """Use Vault's existing runtime identity; capture only into process memory."""
    try:
        result = subprocess.run(["vault", "kv", "get", "-format=json", path],
                                capture_output=True, check=False, timeout=30, text=True)
        if result.returncode != 0:
            raise SessionError("vault_read_failed")
        payload = json.loads(result.stdout)
        password = payload["data"]["data"][field]
        if not isinstance(password, str) or not password:
            raise SessionError("vault_password_field_empty")
        return password
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise SessionError("vault_record_or_runtime_identity_unavailable") from None


def validate(args):
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", args.base_url):
        raise SessionError("explicit_https_grafana_origin_required")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", os.environ.get("VAULT_ADDR", "")):
        raise SessionError("explicit_https_vault_origin_required")
    if os.environ.get("VAULT_SKIP_VERIFY", "").lower() in ["true", "1"]:
        raise SessionError("vault_tls_verification_required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/(?:sit|uat|prod)/[A-Za-z0-9_./-]+", args.vault_path):
        raise SessionError("explicit_environment_scoped_vault_path_required")
    if ".." in args.vault_path.split("/") or args.vault_path.split("/")[1] != args.environment:
        raise SessionError("vault_environment_mismatch")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.password_field):
        raise SessionError("explicit_vault_password_field_required")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", args.admin_username):
        raise SessionError("explicit_management_username_required")
    if not re.fullmatch(r"observability-audit-[a-z0-9-]{1,40}", args.service_account_name):
        raise SessionError("dedicated_observability_audit_name_required")
    if not 1800 <= args.token_ttl <= 3600:
        raise SessionError("token_ttl_must_be_1800_to_3600_seconds")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.cluster):
        raise SessionError("explicit_cluster_required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.datasource_uid):
        raise SessionError("invalid_datasource_uid")


def default_verify(args, token, identity):
    path = Path(__file__).with_name("verify_readonly.py")
    spec = importlib.util.spec_from_file_location("observability_session_verifier", path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier.verify(args, token, identity)


def run(args, credential_reader=vault_password, client_factory=Grafana, verifier=default_verify):
    validate(args)
    report = {"schema_version": "observability-audit-session/v1",
              "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "environment": args.environment, "cluster": args.cluster, "target_origin": args.base_url,
              "management": {"status": "NOT_RUN", "role": "Viewer", "created_service_account": False},
              "audit": {"overall_status": "NOT_RUN"},
              "cleanup": {"status": "NOT_RUN", "service_account_deleted": False,
                          "token_ttl_requested_seconds": args.token_ttl},
              "production_write_denial_test": "NOT_RUN: permission introspection only; no destructive probe"}
    sa_id, token_id, token_name, client, token = None, None, None, None, None
    sensitive = []
    phase = "management"
    try:
        password = credential_reader(args.vault_path, args.password_field)
        sensitive.append(password)
        client = client_factory(args.base_url, args.admin_username, password)
        sensitive.append(client.authorization)
        password = None
        found = client.request("GET", "/api/serviceaccounts/search?" + urllib.parse.urlencode({
            "query": args.service_account_name, "perpage": 1000, "page": 1}))
        if found.get("totalCount", 0) > 1000:
            raise SessionError("service_account_search_incomplete")
        matches = [item for item in found.get("serviceAccounts", []) if item.get("name") == args.service_account_name]
        if len(matches) > 1:
            raise SessionError("duplicate_exact_service_account_name")
        if matches:
            sa_id = matches[0]["id"]
        else:
            created = client.request("POST", "/api/serviceaccounts", {
                "name": args.service_account_name, "role": "Viewer", "isDisabled": False})
            sa_id = created["id"]
            report["management"]["created_service_account"] = True
        if not isinstance(sa_id, int) or sa_id <= 0:
            raise SessionError("invalid_service_account_id")
        identity = client.request("GET", "/api/serviceaccounts/" + str(sa_id))
        if (identity.get("name") != args.service_account_name or identity.get("role") != "Viewer"
                or identity.get("isDisabled") is not False or not identity.get("orgId")):
            raise SessionError("existing_service_account_not_exact_enabled_viewer")
        report["management"].update(service_account_id=sa_id, org_id=identity["orgId"])
        token_name = args.service_account_name + "-" + secrets.token_hex(8)
        minted = client.request("POST", f"/api/serviceaccounts/{sa_id}/tokens", {
            "name": token_name, "secondsToLive": args.token_ttl})
        candidate_id = minted.get("id")
        if not isinstance(candidate_id, int) or candidate_id <= 0:
            raise SessionError("invalid_minted_token_id")
        token_id = candidate_id
        token = minted.get("key")
        if not isinstance(token, str) or not token:
            raise SessionError("minted_token_key_missing")
        sensitive.append(token)
        tokens = client.request("GET", f"/api/serviceaccounts/{sa_id}/tokens")
        match = next((item for item in tokens if item.get("id") == token_id and item.get("name") == token_name), None)
        remaining = match.get("secondsUntilExpiration", 0) if match else 0
        if not match or not match.get("expiration") or not args.token_ttl - 30 <= remaining <= args.token_ttl + 5:
            raise SessionError("finite_token_ttl_not_verified")
        expiration = datetime.datetime.fromisoformat(str(match["expiration"]).replace("Z", "+00:00"))
        if expiration.tzinfo is None:
            raise SessionError("token_expiration_timezone_missing")
        expires_in = (expiration - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if not args.token_ttl - 60 <= expires_in <= args.token_ttl + 60:
            raise SessionError("token_expiration_inconsistent_with_bounded_ttl")
        report["management"].update(status="PASS", token_id=token_id,
                                    token_seconds_remaining=remaining, token_expiration=expiration.isoformat())
        phase = "audit"
        report["audit"] = verifier(args, token, identity)
    except SessionError as error:
        report[phase].update(status="FAIL", overall_status="FAIL", error_code=error.args[0])
    except Exception:
        # Neither remote error bodies nor exception text can enter evidence.
        report[phase].update(status="FAIL", overall_status="FAIL", error_code="session_or_verifier_exception")
    finally:
        if client is not None and sa_id is not None and token_name is not None:
            try:
                # If the token POST timed out after server-side creation, find
                # only this invocation's random exact name before revocation.
                if token_id is None:
                    tokens = client.request("GET", f"/api/serviceaccounts/{sa_id}/tokens")
                    matching = [item for item in tokens if item.get("name") == token_name]
                    if len(matching) > 1:
                        raise SessionError("cleanup_token_identity_ambiguous")
                    token_id = matching[0]["id"] if matching else None
                if token_id is not None:
                    if not isinstance(token_id, int) or token_id <= 0:
                        raise SessionError("cleanup_token_id_invalid")
                    client.request("DELETE", f"/api/serviceaccounts/{sa_id}/tokens/{token_id}")
                    remaining = client.request("GET", f"/api/serviceaccounts/{sa_id}/tokens")
                    if any(item.get("id") == token_id for item in remaining):
                        raise SessionError("token_still_listed_after_revoke")
                report["cleanup"].update(status="PASS", token_revoked=token_id is not None)
            except Exception:
                report["cleanup"].update(status="FAIL", error_code="token_revocation_unconfirmed",
                                          manual_action_required=True)
        else:
            report["cleanup"].update(status="PASS", token_revoked=False, reason="No token mint attempted")
        token = None
        if client is not None:
            client.authorization = None
    statuses = [report["management"]["status"], report["audit"].get("overall_status"), report["cleanup"]["status"]]
    report["overall_status"] = "FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS"
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
    parser.add_argument("--service-account-name", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--environment", required=True, choices=["sit", "uat", "prod"])
    parser.add_argument("--datasource-uid", default="victoriametrics")
    parser.add_argument("--token-ttl", type=int, default=3600)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        parser.error("An absolute sanitized evidence output path is required")
    try:
        report = run(args)
    except SessionError as error:
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
