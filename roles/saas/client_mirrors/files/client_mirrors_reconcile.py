#!/usr/bin/env python3
"""Pure-Python controller reconciler and verifier for roles/saas/client_mirrors.

Reads a JSON payload from stdin (invoked by Ansible with no_log=true) and emits a
sanitized JSON summary on stdout without ever printing credential values.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from xml.sax.saxutils import escape as xml_escape

import yaml

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
ALICLOUD_CDN_ENDPOINT = "https://cdn.aliyuncs.com/"
ALICLOUD_RAM_ENDPOINT = "https://ram.aliyuncs.com/"

ALLOWED_R2_LOCATIONS = frozenset({"apac", "eeur", "enam", "weur", "wnam", "oc"})
ALLOWED_MIN_TLS = frozenset({"1.0", "1.1", "1.2", "1.3"})
ALLOWED_OSS_ACL = frozenset({"public-read", "private"})
ALLOWED_CDN_TYPES = frozenset({"download", "web", "video"})
ALLOWED_CDN_SCOPES = frozenset({"overseas", "domestic", "global"})
ALLOWED_METHODS = frozenset({"GET", "HEAD", "PUT", "POST", "DELETE"})

SENSITIVE_KEY_NAMES = frozenset(
    {
        "access_key_id",
        "access_key_secret",
        "access_token",
        "api_key",
        "api_token",
        "apikey",
        "authorization",
        "password",
        "private_key",
        "secret",
        "secret_access_key",
        "token",
    }
)

SECRET_VALUE_PATTERNS = (
    ("vault-token", re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{20,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("access-key", re.compile(r"\b(?:AKIA|ASIA|LTAI)[A-Za-z0-9]{12,}\b")),
)


def _scan_forbidden_keys(obj, path="$"):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if ks.lower() in SENSITIVE_KEY_NAMES:
                hits.append(f"{path}.{ks}")
            hits.extend(_scan_forbidden_keys(v, f"{path}.{ks}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            hits.extend(_scan_forbidden_keys(item, f"{path}[{idx}]"))
    return hits


def _require_str(mapping, key, ctx):
    val = mapping.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{ctx}.{key} must be a non-empty string")
    return val.strip()


def _strip_kv_mount(path_value):
    cleaned = str(path_value or "").strip()
    return cleaned.removeprefix("kv/").lstrip("/") if cleaned else ""


def _require_origins(origins, ctx):
    if not isinstance(origins, list) or not origins:
        raise ValueError(f"{ctx} must be a non-empty list")
    cleaned = [str(o or "").strip() for o in origins]
    for o in cleaned:
        if not o.startswith("https://"):
            raise ValueError(f"{ctx} entries must use https://, got {o!r}")
    return cleaned


def _require_methods(methods, ctx):
    if not isinstance(methods, list) or not methods:
        raise ValueError(f"{ctx} must be a non-empty list")
    cleaned = [str(m).strip().upper() for m in methods]
    bad = set(cleaned) - ALLOWED_METHODS
    if bad:
        raise ValueError(f"{ctx} has unsupported method(s): {sorted(bad)}")
    return cleaned


def load_and_validate_manifest(manifest_path):
    path = Path(manifest_path)
    raw = path.read_text(encoding="utf-8")
    pattern_hits = sorted(name for name, pat in SECRET_VALUE_PATTERNS if pat.search(raw))
    if pattern_hits:
        raise ValueError(f"{path}: credential-shaped content detected: {', '.join(pattern_hits)}")

    doc = yaml.safe_load(raw)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: root document must be a YAML mapping")
    if doc.get("apiVersion") != "gitops.svc.plus/v1alpha1" or doc.get("kind") != "ClientMirrorStack":
        raise ValueError(f"{path}: expected apiVersion=gitops.svc.plus/v1alpha1 kind=ClientMirrorStack")

    key_hits = _scan_forbidden_keys(doc)
    if key_hits:
        raise ValueError(f"{path}: forbidden secret-like YAML key(s): {', '.join(key_hits)}")

    spec = doc.get("spec") or {}
    glob = spec.get("global") or {}
    zone_name = _require_str(glob, "zone_name", "spec.global")

    products = spec.get("products") or []
    if not isinstance(products, list) or not products:
        raise ValueError("spec.products must be a non-empty list")

    admin_sources = spec.get("admin_vault_sources") or {}
    cf_admin_kv = str(admin_sources.get("cloudflare") or "kv/prod/serverless/cloudflare").removeprefix("kv/").lstrip("/")
    ali_admin_kv = str(admin_sources.get("alicloud") or "kv/prod/platform/alicloud").removeprefix("kv/").lstrip("/")

    r2_raw = spec.get("cloudflare_r2") or {}
    r2_location = _require_str(r2_raw, "location", "spec.cloudflare_r2").lower()
    if r2_location not in ALLOWED_R2_LOCATIONS:
        raise ValueError(f"cloudflare_r2.location must be one of {sorted(ALLOWED_R2_LOCATIONS)}")
    min_tls = str(r2_raw.get("min_tls", "1.2")).strip()
    if min_tls not in ALLOWED_MIN_TLS:
        raise ValueError(f"cloudflare_r2.min_tls must be one of {sorted(ALLOWED_MIN_TLS)}")

    r2 = {
        "bucket_name": _require_str(r2_raw, "bucket_name", "spec.cloudflare_r2"),
        "custom_domain": _require_str(r2_raw, "custom_domain", "spec.cloudflare_r2"),
        "location": r2_location,
        "min_tls": min_tls,
        "cors_allowed_origins": _require_origins(
            r2_raw.get("cors_allowed_origins"), "spec.cloudflare_r2.cors_allowed_origins"
        ),
        "cors_allowed_methods": _require_methods(
            r2_raw.get("cors_allowed_methods"), "spec.cloudflare_r2.cors_allowed_methods"
        ),
        "cors_max_age_seconds": int(r2_raw.get("cors_max_age_seconds", 3600)),
        "token_name": _require_str(r2_raw, "token_name", "spec.cloudflare_r2"),
        "vault_kv_path": _require_str(r2_raw, "vault_kv_path", "spec.cloudflare_r2").removeprefix("kv/").lstrip("/"),
    }

    ali_raw = spec.get("aliyun_oss_cdn") or {}
    oss_region = _require_str(ali_raw, "oss_region", "spec.aliyun_oss_cdn")
    if oss_region.startswith("oss-"):
        raise ValueError("aliyun_oss_cdn.oss_region must omit 'oss-' prefix (e.g. cn-hongkong)")
    oss_acl = _require_str(ali_raw, "oss_acl", "spec.aliyun_oss_cdn")
    if oss_acl not in ALLOWED_OSS_ACL:
        raise ValueError(f"aliyun_oss_cdn.oss_acl must be one of {sorted(ALLOWED_OSS_ACL)}")
    cdn_type = _require_str(ali_raw, "cdn_type", "spec.aliyun_oss_cdn")
    if cdn_type not in ALLOWED_CDN_TYPES:
        raise ValueError(f"aliyun_oss_cdn.cdn_type must be one of {sorted(ALLOWED_CDN_TYPES)}")
    cdn_scope = _require_str(ali_raw, "cdn_scope", "spec.aliyun_oss_cdn")
    if cdn_scope not in ALLOWED_CDN_SCOPES:
        raise ValueError(f"aliyun_oss_cdn.cdn_scope must be one of {sorted(ALLOWED_CDN_SCOPES)}")

    ali = {
        "bucket_name": _require_str(ali_raw, "bucket_name", "spec.aliyun_oss_cdn"),
        "oss_region": oss_region,
        "oss_acl": oss_acl,
        "cdn_domain": _require_str(ali_raw, "cdn_domain", "spec.aliyun_oss_cdn"),
        "cdn_type": cdn_type,
        "cdn_scope": cdn_scope,
        "cdn_rate_limit": _require_str(ali_raw, "cdn_rate_limit", "spec.aliyun_oss_cdn"),
        "cdn_rate_limit_after": _require_str(ali_raw, "cdn_rate_limit_after", "spec.aliyun_oss_cdn"),
        "cdn_cas_cert_id": str(ali_raw.get("cdn_cas_cert_id", "") or "").strip(),
        "cdn_tls_vault_path": _strip_kv_mount(ali_raw.get("cdn_tls_vault_path", "")),
        "cors_allowed_origins": _require_origins(
            ali_raw.get("cors_allowed_origins"), "spec.aliyun_oss_cdn.cors_allowed_origins"
        ),
        "cors_allowed_methods": _require_methods(
            ali_raw.get("cors_allowed_methods"), "spec.aliyun_oss_cdn.cors_allowed_methods"
        ),
        "cors_max_age_seconds": int(ali_raw.get("cors_max_age_seconds", 3600)),
        "ram_user_name": _require_str(ali_raw, "ram_user_name", "spec.aliyun_oss_cdn"),
        "ram_policy_name": _require_str(ali_raw, "ram_policy_name", "spec.aliyun_oss_cdn"),
        "vault_kv_path": _require_str(ali_raw, "vault_kv_path", "spec.aliyun_oss_cdn").removeprefix("kv/").lstrip("/"),
    }

    return {
        "zone_name": zone_name,
        "products": [p["slug"] for p in products],
        "admin_sources": {"cloudflare": cf_admin_kv, "alicloud": ali_admin_kv},
        "r2": r2,
        "ali": ali,
    }


def _scrub(text, secrets):
    cleaned = str(text)
    for sec in secrets:
        if sec and len(sec) >= 6:
            cleaned = cleaned.replace(sec, "[REDACTED]")
    return cleaned


class _APIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _http_json(url, *, method="GET", headers=None, payload=None, opener=urllib.request.urlopen):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = {"Accept": "application/json"}
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with opener(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise _APIError(exc.code, f"{method} {url} HTTP {exc.code}: {body}") from exc


def vault_kv_read(*, vault_addr, vault_token, vault_mount, path, opener=urllib.request.urlopen):
    url = f"{vault_addr.rstrip('/')}/v1/{vault_mount}/data/{path.lstrip('/')}"
    try:
        _, parsed = _http_json(url, headers={"X-Vault-Token": vault_token}, opener=opener)
    except _APIError as exc:
        if exc.status == 404:
            return {}
        raise
    return dict(((parsed.get("data") or {}).get("data")) or {})


def vault_kv_write(*, vault_addr, vault_token, vault_mount, path, data, opener=urllib.request.urlopen):
    url = f"{vault_addr.rstrip('/')}/v1/{vault_mount}/data/{path.lstrip('/')}"
    _http_json(
        url,
        method="POST",
        headers={"X-Vault-Token": vault_token},
        payload={"data": data},
        opener=opener,
    )


def cf_request(path, *, cf_token, method="GET", payload=None, allow_404=False, opener=urllib.request.urlopen):
    url = f"{CLOUDFLARE_API_BASE}{path}"
    try:
        _, parsed = _http_json(
            url,
            method=method,
            headers={"Authorization": f"Bearer {cf_token}"},
            payload=payload,
            opener=opener,
        )
    except _APIError as exc:
        if allow_404 and exc.status == 404:
            return None
        raise
    if not parsed.get("success", False):
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {parsed.get('errors')}")
    return parsed


def _percent_encode(val):
    return urllib.parse.quote(str(val), safe="~")


def sign_aliyun_rpc(params, access_key_secret, method="GET"):
    ordered = sorted((str(k), str(v)) for k, v in params.items())
    canonical = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in ordered)
    string_to_sign = f"{method}&%2F&{_percent_encode(canonical)}"
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    sig = base64.b64encode(digest).decode("utf-8")
    return f"{canonical}&Signature={_percent_encode(sig)}"


def aliyun_rpc(endpoint, version, action, extra_params, ak, sk, *, method="GET", opener=urllib.request.urlopen):
    params = {
        "Format": "JSON",
        "Version": version,
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "Action": action,
    }
    params.update(extra_params)
    query = sign_aliyun_rpc(params, sk, method=method)
    if method == "POST":
        url = f"{endpoint.rstrip('/')}/"
        req = urllib.request.Request(
            url,
            data=query.encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with opener(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise _APIError(exc.code, f"POST {url} HTTP {exc.code}: {err_body}") from exc
    url = f"{endpoint.rstrip('/')}/?{query}"
    _, parsed = _http_json(url, method=method, opener=opener)
    return parsed


def sign_oss_v1_headers(*, method, bucket, resource_suffix, content_type, ak, sk, oss_headers=None, date_str=None):
    date_header = date_str or datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    oss_headers = {k.lower(): str(v) for k, v in (oss_headers or {}).items()}
    canon_oss = "".join(f"{k}:{oss_headers[k]}\n" for k in sorted(oss_headers))
    canon_res = f"/{bucket}/{resource_suffix}"
    string_to_sign = f"{method}\n\n{content_type}\n{date_header}\n{canon_oss}{canon_res}"
    sig = base64.b64encode(
        hmac.new(sk.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    headers = {
        "Date": date_header,
        "Authorization": f"OSS {ak}:{sig}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    headers.update(oss_headers)
    return headers


def oss_request(
    *,
    bucket,
    oss_region,
    method,
    resource_suffix="",
    body=b"",
    content_type="",
    oss_headers=None,
    ak,
    sk,
    allow_404=False,
    opener=urllib.request.urlopen,
):
    url = f"https://{bucket}.oss-{oss_region}.aliyuncs.com/{resource_suffix}"
    # urllib auto-fills Content-Type=application/x-www-form-urlencoded when data is non-None without a header;
    # set an explicit Content-Type so OSS v1 StringToSign and the sent header always match.
    effective_content_type = content_type
    if method in ("PUT", "POST") and not effective_content_type:
        effective_content_type = "application/octet-stream"
    headers = sign_oss_v1_headers(
        method=method,
        bucket=bucket,
        resource_suffix=resource_suffix,
        content_type=effective_content_type,
        ak=ak,
        sk=sk,
        oss_headers=oss_headers,
    )
    req = urllib.request.Request(url, data=(body if method in ("PUT", "POST") else None), headers=headers, method=method)
    try:
        with opener(req, timeout=25) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return 404, b""
        err_body = exc.read().decode("utf-8", errors="replace")
        raise _APIError(exc.code, f"OSS {method} {url} HTTP {exc.code}: {err_body}") from exc


def sign_s3_v4_headers(*, method, url, payload_bytes, access_key, secret_key, region="auto", service="s3"):
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    canonical_uri = parsed.path or "/"
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    sign_headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(sign_headers))
    canonical = "".join(f"{k}:{sign_headers[k]}\n" for k in sorted(sign_headers))
    canonical_req = "\n".join([method, canonical_uri, parsed.query or "", canonical, signed, payload_hash])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_req.encode("utf-8")).hexdigest()])

    def _hm(k, msg):
        return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

    k_signing = _hm(_hm(_hm(_hm(("AWS4" + secret_key).encode("utf-8"), date_stamp), region), service), "aws4_request")
    sig = hmac.new(k_signing, sts.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Host": host,
        "Content-Type": "application/json",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}",
    }


def _reconcile_cloudflare_r2(
    *,
    r2_cfg,
    zone_id,
    cf_account_id,
    cf_token,
    existing_kv,
    apply,
    rotate_credentials,
    secret_bucket,
    opener,
):
    bucket = r2_cfg["bucket_name"]
    domain = r2_cfg["custom_domain"]
    endpoint = f"https://{cf_account_id}.r2.cloudflarestorage.com"
    actions = []
    changed = False

    bkt = cf_request(f"/accounts/{cf_account_id}/r2/buckets/{bucket}", cf_token=cf_token, allow_404=True, opener=opener)
    if bkt is None:
        if apply:
            cf_request(
                f"/accounts/{cf_account_id}/r2/buckets",
                cf_token=cf_token,
                method="POST",
                payload={"name": bucket, "locationHint": r2_cfg["location"]},
                opener=opener,
            )
            actions.append("r2_bucket:created")
            changed = True
        else:
            actions.append("r2_bucket:would-create")
    else:
        actions.append("r2_bucket:ok")

    doms_resp = cf_request(
        f"/accounts/{cf_account_id}/r2/buckets/{bucket}/domains/custom",
        cf_token=cf_token,
        allow_404=True,
        opener=opener,
    )
    domains = ((doms_resp or {}).get("result") or {}).get("domains") or []
    existing_dom = next((d for d in domains if d.get("domain") == domain), None)
    if existing_dom is None:
        if apply:
            cf_request(
                f"/accounts/{cf_account_id}/r2/buckets/{bucket}/domains/custom",
                cf_token=cf_token,
                method="POST",
                payload={"domain": domain, "zoneId": zone_id, "enabled": True, "minTLS": r2_cfg["min_tls"]},
                opener=opener,
            )
            actions.append("r2_custom_domain:created")
            changed = True
        else:
            actions.append("r2_custom_domain:would-create")
    elif not existing_dom.get("enabled", True) or existing_dom.get("minTLS") != r2_cfg["min_tls"]:
        if apply:
            cf_request(
                f"/accounts/{cf_account_id}/r2/buckets/{bucket}/domains/custom/{domain}",
                cf_token=cf_token,
                method="PUT",
                payload={"enabled": True, "minTLS": r2_cfg["min_tls"]},
                opener=opener,
            )
            actions.append("r2_custom_domain:updated")
            changed = True
        else:
            actions.append("r2_custom_domain:would-update")
    else:
        actions.append("r2_custom_domain:ok")

    desired_cors = [
        {
            "id": "client-mirrors-cors",
            "allowed": {
                "methods": r2_cfg["cors_allowed_methods"],
                "origins": r2_cfg["cors_allowed_origins"],
                "headers": ["*"],
            },
            "exposeHeaders": ["ETag", "Content-Length", "Content-Type"],
            "maxAgeSeconds": r2_cfg["cors_max_age_seconds"],
        }
    ]
    cors_resp = cf_request(
        f"/accounts/{cf_account_id}/r2/buckets/{bucket}/cors",
        cf_token=cf_token,
        allow_404=True,
        opener=opener,
    )
    current_cors = ((cors_resp or {}).get("result") or {}).get("rules") or []
    if current_cors != desired_cors:
        if apply:
            cf_request(
                f"/accounts/{cf_account_id}/r2/buckets/{bucket}/cors",
                cf_token=cf_token,
                method="PUT",
                payload={"rules": desired_cors},
                opener=opener,
            )
            actions.append("r2_cors:updated")
            changed = True
        else:
            actions.append("r2_cors:would-update")
    else:
        actions.append("r2_cors:ok")

    need_token = rotate_credentials or not (
        existing_kv.get("access_key_id") and existing_kv.get("secret_access_key")
    )
    r2_creds = dict(existing_kv)
    if need_token:
        if apply:
            pgs = cf_request(
                f"/accounts/{cf_account_id}/tokens/permission_groups",
                cf_token=cf_token,
                opener=opener,
            ).get("result") or []
            pg = next((g for g in pgs if g.get("name") == "Workers R2 Storage Bucket Item Write"), None)
            if pg is None:
                raise RuntimeError("Cloudflare permission group 'Workers R2 Storage Bucket Item Write' not found")
            scope_key = f"com.cloudflare.edge.r2.bucket.{cf_account_id}_default_{bucket}"
            token_res = cf_request(
                f"/accounts/{cf_account_id}/tokens",
                cf_token=cf_token,
                method="POST",
                payload={
                    "name": r2_cfg["token_name"],
                    "policies": [
                        {
                            "effect": "allow",
                            "permission_groups": [{"id": pg["id"]}],
                            "resources": {scope_key: "*"},
                        }
                    ],
                },
                opener=opener,
            )["result"]
            raw_val = token_res["value"]
            secret_bucket.append(raw_val)
            r2_creds = {
                "access_key_id": token_res["id"],
                "secret_access_key": hashlib.sha256(raw_val.encode("utf-8")).hexdigest(),
                "bucket": bucket,
                "endpoint": endpoint,
                "custom_domain": domain,
            }
            secret_bucket.extend([r2_creds["access_key_id"], r2_creds["secret_access_key"]])
            actions.append("r2_token:created")
            changed = True
        else:
            actions.append("r2_token:would-create")
    else:
        r2_creds.update({"bucket": bucket, "endpoint": endpoint, "custom_domain": domain})
        actions.append("r2_token:preserved")

    return {"actions": actions, "changed": changed, "kv_payload": r2_creds}


def _build_oss_cors_xml(ali_cfg):
    origins = "".join(f"<AllowedOrigin>{xml_escape(o)}</AllowedOrigin>" for o in ali_cfg["cors_allowed_origins"])
    methods = "".join(f"<AllowedMethod>{xml_escape(m)}</AllowedMethod>" for m in ali_cfg["cors_allowed_methods"])
    expose = "".join(
        f"<ExposeHeader>{h}</ExposeHeader>" for h in ("ETag", "Content-Length", "Content-Type")
    )
    return (
        "<CORSConfiguration><CORSRule>"
        f"{origins}{methods}<AllowedHeader>*</AllowedHeader>{expose}"
        f"<MaxAgeSeconds>{ali_cfg['cors_max_age_seconds']}</MaxAgeSeconds>"
        "</CORSRule><ResponseVary>true</ResponseVary></CORSConfiguration>"
    ).encode("utf-8")


def _upsert_cloudflare_dns(
    *,
    zone_id,
    cf_token,
    record_type,
    name,
    content,
    ttl,
    proxied,
    comment,
    apply,
    opener,
):
    existing = cf_request(
        f"/zones/{zone_id}/dns_records?type={record_type}&name={urllib.parse.quote(name)}",
        cf_token=cf_token,
        opener=opener,
    ).get("result") or []
    normalized_content = content.strip('"') if record_type == "TXT" else content
    matched = next(
        (
            r
            for r in existing
            if (str(r.get("content") or "").strip('"') if record_type == "TXT" else r.get("content"))
            == normalized_content
            and bool(r.get("proxied", False)) == bool(proxied)
        ),
        None,
    )
    if matched:
        return "unchanged", False
    if not apply:
        return ("would-update" if existing else "would-create"), False
    payload = {
        "type": record_type,
        "name": name,
        "content": f'"{normalized_content}"' if record_type == "TXT" else content,
        "ttl": ttl,
        "proxied": bool(proxied),
        "comment": comment,
    }
    if existing:
        rec_id = existing[0]["id"]
        cf_request(f"/zones/{zone_id}/dns_records/{rec_id}", cf_token=cf_token, method="PUT", payload=payload, opener=opener)
        return "updated", True
    cf_request(f"/zones/{zone_id}/dns_records", cf_token=cf_token, method="POST", payload=payload, opener=opener)
    return "created", True


def _reconcile_aliyun_oss_cdn(
    *,
    ali_cfg,
    zone_name,
    zone_id,
    cf_token,
    ali_ak,
    ali_sk,
    existing_kv,
    apply,
    run_cdn_verify_txt,
    rotate_credentials,
    wait_dns_seconds,
    secret_bucket,
    opener,
    vault_addr="",
    vault_token="",
    vault_mount="kv",
):
    bucket = ali_cfg["bucket_name"]
    region = ali_cfg["oss_region"]
    oss_endpoint = f"https://oss-{region}.aliyuncs.com"
    cdn_domain = ali_cfg["cdn_domain"]
    actions = []
    changed = False

    status, _ = oss_request(
        bucket=bucket, oss_region=region, method="HEAD", resource_suffix="?bucketInfo",
        ak=ali_ak, sk=ali_sk, allow_404=True, opener=opener,
    )
    if status == 404:
        if apply:
            bkt_xml = (
                "<CreateBucketConfiguration><StorageClass>Standard</StorageClass>"
                "<DataRedundancyType>LRS</DataRedundancyType></CreateBucketConfiguration>"
            ).encode("utf-8")
            oss_request(
                bucket=bucket, oss_region=region, method="PUT", body=bkt_xml, content_type="application/xml",
                oss_headers={"x-oss-acl": ali_cfg["oss_acl"]}, ak=ali_ak, sk=ali_sk, opener=opener,
            )
            actions.append("oss_bucket:created")
            changed = True
        else:
            actions.append("oss_bucket:would-create")
    else:
        actions.append("oss_bucket:ok")

    if apply:
        if ali_cfg["oss_acl"] != "private":
            oss_request(
                bucket=bucket, oss_region=region, method="DELETE", resource_suffix="?publicAccessBlock",
                ak=ali_ak, sk=ali_sk, allow_404=True, opener=opener,
            )
        oss_request(
            bucket=bucket, oss_region=region, method="PUT", resource_suffix="?acl",
            oss_headers={"x-oss-acl": ali_cfg["oss_acl"]}, ak=ali_ak, sk=ali_sk, opener=opener,
        )
        oss_request(
            bucket=bucket, oss_region=region, method="PUT", resource_suffix="?versioning",
            body=b"<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>",
            content_type="application/xml", ak=ali_ak, sk=ali_sk, opener=opener,
        )
        oss_request(
            bucket=bucket, oss_region=region, method="PUT", resource_suffix="?encryption",
            body=b"<ServerSideEncryptionRule><ApplyServerSideEncryptionByDefault><SSEAlgorithm>AES256</SSEAlgorithm></ApplyServerSideEncryptionByDefault></ServerSideEncryptionRule>",
            content_type="application/xml", ak=ali_ak, sk=ali_sk, opener=opener,
        )
        oss_request(
            bucket=bucket, oss_region=region, method="PUT", resource_suffix="?cors",
            body=_build_oss_cors_xml(ali_cfg), content_type="application/xml", ak=ali_ak, sk=ali_sk, opener=opener,
        )
        actions.append("oss_config:reconciled")
    else:
        actions.append("oss_config:planned")

    if run_cdn_verify_txt:
        verify_content = aliyun_rpc(
            ALICLOUD_CDN_ENDPOINT, "2018-05-10", "DescribeVerifyContent", {"DomainName": cdn_domain}, ali_ak, ali_sk, opener=opener
        ).get("Content", "")
        if verify_content:
            txt_action, txt_changed = _upsert_cloudflare_dns(
                zone_id=zone_id, cf_token=cf_token, record_type="TXT", name=f"verification.{zone_name}",
                content=verify_content, ttl=120, proxied=False, comment="Aliyun CDN domain ownership verification",
                apply=apply, opener=opener,
            )
            actions.append(f"cdn_verify_txt:{txt_action}")
            changed = changed or txt_changed
            if apply:
                if txt_changed and wait_dns_seconds > 0:
                    time.sleep(wait_dns_seconds)
                aliyun_rpc(
                    ALICLOUD_CDN_ENDPOINT, "2018-05-10", "VerifyDomainOwner",
                    {"DomainName": cdn_domain, "VerifyType": "dnsCheck"}, ali_ak, ali_sk, opener=opener,
                )
                actions.append("cdn_owner:verified")

    cdn_cname = ""
    ssl_status = ""
    try:
        detail = aliyun_rpc(
            ALICLOUD_CDN_ENDPOINT, "2018-05-10", "DescribeCdnDomainDetail", {"DomainName": cdn_domain}, ali_ak, ali_sk, opener=opener
        )
        detail_model = detail.get("GetDomainDetailModel") or {}
        cdn_cname = detail_model.get("Cname", "")
        ssl_status = detail_model.get("ServerCertificateStatus", "")
        actions.append("cdn_domain:ok")
    except _APIError as exc:
        if exc.status not in (400, 404):
            raise
        if apply:
            sources = json.dumps(
                [
                    {
                        "content": f"{bucket}.oss-{region}.aliyuncs.com",
                        "type": "oss",
                        "port": 80,
                        "priority": "20",
                        "weight": "10",
                    }
                ]
            )
            aliyun_rpc(
                ALICLOUD_CDN_ENDPOINT,
                "2018-05-10",
                "AddCdnDomain",
                {
                    "DomainName": cdn_domain,
                    "CdnType": ali_cfg["cdn_type"],
                    "Scope": ali_cfg["cdn_scope"],
                    "Sources": sources,
                },
                ali_ak,
                ali_sk,
                opener=opener,
            )
            detail = aliyun_rpc(
                ALICLOUD_CDN_ENDPOINT, "2018-05-10", "DescribeCdnDomainDetail", {"DomainName": cdn_domain}, ali_ak, ali_sk, opener=opener
            )
            detail_model = detail.get("GetDomainDetailModel") or {}
            cdn_cname = detail_model.get("Cname", "")
            ssl_status = detail_model.get("ServerCertificateStatus", "")
            actions.append("cdn_domain:created")
            changed = True
        else:
            actions.append("cdn_domain:would-create")

    if ali_cfg.get("cdn_tls_vault_path"):
        if apply:
            if not (vault_addr and vault_token):
                raise RuntimeError("vault_addr and vault_token are required to read CDN TLS Vault source")
            tls_kv = vault_kv_read(
                vault_addr=vault_addr,
                vault_token=vault_token,
                vault_mount=vault_mount,
                path=ali_cfg["cdn_tls_vault_path"],
                opener=opener,
            )
            fullchain_b64 = str(tls_kv.get("tls_fullchain_pem_b64") or "").strip()
            key_b64 = str(tls_kv.get("tls_key_pem_b64") or "").strip()
            secret_bucket.append(key_b64)
            if not (fullchain_b64 and key_b64):
                raise RuntimeError(
                    f"Missing tls_fullchain_pem_b64 or tls_key_pem_b64 in {vault_mount}/{ali_cfg['cdn_tls_vault_path']}"
                )
            ssl_pub = base64.b64decode(fullchain_b64).decode("utf-8")
            ssl_pri = base64.b64decode(key_b64).decode("utf-8")
            secret_bucket.append(ssl_pri)
            cert_name = f"svc-plus-{hashlib.sha256(ssl_pub.encode('utf-8')).hexdigest()[:10]}"
            aliyun_rpc(
                ALICLOUD_CDN_ENDPOINT,
                "2018-05-10",
                "SetCdnDomainSSLCertificate",
                {
                    "DomainName": cdn_domain,
                    "CertName": cert_name,
                    "CertType": "upload",
                    "SSLProtocol": "on",
                    "SSLPub": ssl_pub,
                    "SSLPri": ssl_pri,
                },
                ali_ak,
                ali_sk,
                method="POST",
                opener=opener,
            )
            actions.append("cdn_tls:configured")
            if ssl_status != "on":
                changed = True
        else:
            actions.append("cdn_tls:ok" if ssl_status == "on" else "cdn_tls:would-configure")

    if apply:
        funcs = [
            {
                "functionName": "limit_rate",
                "functionArgs": [
                    {"argName": "ali_limit_rate", "argValue": ali_cfg["cdn_rate_limit"]},
                    {"argName": "ali_limit_rate_after", "argValue": ali_cfg["cdn_rate_limit_after"]},
                ],
            }
        ]
        if ali_cfg["cdn_cas_cert_id"] or ali_cfg.get("cdn_tls_vault_path"):
            funcs.append(
                {
                    "functionName": "https_force",
                    "functionArgs": [
                        {"argName": "enable", "argValue": "on"},
                        {"argName": "https_rewrite", "argValue": "301"},
                    ],
                }
            )
        aliyun_rpc(
            ALICLOUD_CDN_ENDPOINT,
            "2018-05-10",
            "BatchSetCdnDomainConfig",
            {"DomainNames": cdn_domain, "Functions": json.dumps(funcs)},
            ali_ak,
            ali_sk,
            opener=opener,
        )
        actions.append("cdn_config:reconciled")

    if cdn_cname:
        cname_action, cname_changed = _upsert_cloudflare_dns(
            zone_id=zone_id, cf_token=cf_token, record_type="CNAME", name=cdn_domain, content=cdn_cname,
            ttl=300, proxied=False, comment="Managed by playbooks roles/saas/client_mirrors (Aliyun CDN CNAME)",
            apply=apply, opener=opener,
        )
        actions.append(f"cdn_cname:{cname_action}")
        changed = changed or cname_changed

    # RAM user + least-privilege OSS policy + AccessKey (preserve existing Vault AK/SK unless rotating)
    ram_user = ali_cfg["ram_user_name"]
    ram_policy = ali_cfg["ram_policy_name"]
    policy_doc = json.dumps(
        {
            "Version": "1",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "oss:PutObject",
                        "oss:GetObject",
                        "oss:GetObjectAcl",
                        "oss:DeleteObject",
                        "oss:AbortMultipartUpload",
                        "oss:ListMultipartUploads",
                        "oss:ListParts",
                    ],
                    "Resource": [
                        f"acs:oss:*:*:{bucket}/releases/*",
                        f"acs:oss:*:*:{bucket}/offline-package/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["oss:GetBucketInfo", "oss:GetBucketLocation", "oss:ListObjects"],
                    "Resource": [f"acs:oss:*:*:{bucket}"],
                    "Condition": {"StringLike": {"oss:Prefix": ["releases/*", "offline-package/*"]}},
                },
            ],
        }
    )

    if apply:
        try:
            aliyun_rpc(ALICLOUD_RAM_ENDPOINT, "2015-05-01", "GetUser", {"UserName": ram_user}, ali_ak, ali_sk, opener=opener)
            actions.append("ram_user:ok")
        except _APIError as exc:
            if exc.status != 404:
                raise
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT,
                "2015-05-01",
                "CreateUser",
                {"UserName": ram_user, "DisplayName": "CI Client Mirrors OSS Publisher"},
                ali_ak,
                ali_sk,
                opener=opener,
            )
            actions.append("ram_user:created")
            changed = True

        try:
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT, "2015-05-01", "GetPolicy",
                {"PolicyType": "Custom", "PolicyName": ram_policy}, ali_ak, ali_sk, opener=opener,
            )
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT, "2015-05-01", "CreatePolicyVersion",
                {
                    "PolicyName": ram_policy,
                    "PolicyDocument": policy_doc,
                    "SetAsDefault": "true",
                    "RotateStrategy": "DeleteOldestNonDefaultVersionWhenLimitExceeded",
                },
                ali_ak,
                ali_sk,
                opener=opener,
            )
            actions.append("ram_policy:updated")
        except _APIError as exc:
            if exc.status != 404:
                raise
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT,
                "2015-05-01",
                "CreatePolicy",
                {"PolicyName": ram_policy, "PolicyDocument": policy_doc, "Description": "Scoped OSS RW for client mirrors"},
                ali_ak,
                ali_sk,
                opener=opener,
            )
            actions.append("ram_policy:created")
            changed = True

        attached = (
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT, "2015-05-01", "ListPoliciesForUser", {"UserName": ram_user}, ali_ak, ali_sk, opener=opener
            )
            .get("Policies", {})
            .get("Policy", [])
        )
        if not any(p.get("PolicyName") == ram_policy for p in attached):
            aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT, "2015-05-01", "AttachPolicyToUser",
                {"PolicyType": "Custom", "PolicyName": ram_policy, "UserName": ram_user}, ali_ak, ali_sk, opener=opener,
            )
            actions.append("ram_attach:attached")
            changed = True

    need_ak = rotate_credentials or not (existing_kv.get("access_key_id") and existing_kv.get("access_key_secret"))
    ali_creds = dict(existing_kv)
    if need_ak:
        if apply:
            ak_res = aliyun_rpc(
                ALICLOUD_RAM_ENDPOINT, "2015-05-01", "CreateAccessKey", {"UserName": ram_user}, ali_ak, ali_sk, opener=opener
            )["AccessKey"]
            ali_creds = {
                "access_key_id": ak_res["AccessKeyId"],
                "access_key_secret": ak_res["AccessKeySecret"],
                "bucket": bucket,
                "endpoint": oss_endpoint,
                "cdn_domain": cdn_domain,
                "oss_region": region,
                "cdn_scope": ali_cfg["cdn_scope"],
            }
            secret_bucket.extend([ali_creds["access_key_id"], ali_creds["access_key_secret"]])
            actions.append("ram_ak:created")
            changed = True
        else:
            actions.append("ram_ak:would-create")
    else:
        ali_creds.update(
            {
                "bucket": bucket,
                "endpoint": oss_endpoint,
                "cdn_domain": cdn_domain,
                "oss_region": region,
                "cdn_scope": ali_cfg["cdn_scope"],
            }
        )
        actions.append("ram_ak:preserved")

    return {"actions": actions, "changed": changed, "kv_payload": ali_creds, "cdn_cname": cdn_cname}


def _probe_mirrors(*, r2_creds, ali_creds, probe_path, probe_origin, opener):
    clean_key = probe_path.lstrip("/")
    payload_bytes = json.dumps(
        {
            "status": "ok",
            "mirrors": [f"https://{r2_creds['custom_domain']}", f"https://{ali_creds['cdn_domain']}"],
            "checkedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        sort_keys=True,
    ).encode("utf-8")

    r2_url = f"{r2_creds['endpoint'].rstrip('/')}/{r2_creds['bucket']}/{clean_key}"
    r2_headers = sign_s3_v4_headers(
        method="PUT",
        url=r2_url,
        payload_bytes=payload_bytes,
        access_key=r2_creds["access_key_id"],
        secret_key=r2_creds["secret_access_key"],
    )
    with opener(urllib.request.Request(r2_url, data=payload_bytes, headers=r2_headers, method="PUT"), timeout=25) as r2_put:
        r2_put_status = r2_put.status

    oss_put_status, _ = oss_request(
        bucket=ali_creds["bucket"],
        oss_region=ali_creds["oss_region"],
        method="PUT",
        resource_suffix=clean_key,
        body=payload_bytes,
        content_type="application/json",
        ak=ali_creds["access_key_id"],
        sk=ali_creds["access_key_secret"],
        opener=opener,
    )

    probes = []
    for host in (r2_creds["custom_domain"], ali_creds["cdn_domain"]):
        get_url = f"https://{host}/{clean_key}"
        req = urllib.request.Request(
            get_url,
            headers={"Origin": probe_origin, "User-Agent": "playbooks-saas-client-mirrors/1"},
            method="GET",
        )
        with opener(req, timeout=25) as resp:
            headers_lower = {k.lower(): v for k, v in resp.headers.items()}
            acao = headers_lower.get("access-control-allow-origin", "")
            probes.append(
                {
                    "url": get_url,
                    "status": resp.status,
                    "acao": acao,
                    "ok": resp.status == 200 and acao in (probe_origin, "*"),
                }
            )
    return {"r2_put": r2_put_status, "oss_put": oss_put_status, "https_probes": probes}


def reconcile(request, *, opener=urllib.request.urlopen, wait_dns_seconds=5):
    manifest = load_and_validate_manifest(request["manifest_path"])
    mode = str(request.get("mode") or "plan").strip()
    apply = bool(request.get("apply", mode == "apply"))
    verify = bool(request.get("verify", mode in ("apply", "verify")))
    rotate = bool(request.get("rotate_credentials", False))
    run_cdn_verify_txt = bool(request.get("run_cdn_verify_txt", True))

    vault_addr = str(request.get("vault_addr") or "").strip()
    vault_token = str(request.get("vault_token") or "").strip()
    vault_mount = str(request.get("vault_mount") or "kv").strip()
    secret_bucket = [vault_token]

    r2_kv_path = manifest["r2"]["vault_kv_path"]
    ali_kv_path = manifest["ali"]["vault_kv_path"]
    existing_r2 = (
        vault_kv_read(vault_addr=vault_addr, vault_token=vault_token, vault_mount=vault_mount, path=r2_kv_path, opener=opener)
        if vault_addr and vault_token
        else {}
    )
    existing_ali = (
        vault_kv_read(vault_addr=vault_addr, vault_token=vault_token, vault_mount=vault_mount, path=ali_kv_path, opener=opener)
        if vault_addr and vault_token
        else {}
    )

    try:
        summary = {
            "mode": mode,
            "apply": apply,
            "products": manifest["products"],
            "changed": False,
        }

        r2_creds = existing_r2
        ali_creds = existing_ali

        if mode != "verify":
            cf_token = str(request.get("cloudflare_api_token") or "").strip()
            cf_account_id = str(request.get("cloudflare_account_id") or "").strip()
            ali_ak = str(request.get("alicloud_access_key") or "").strip()
            ali_sk = str(request.get("alicloud_secret_key") or "").strip()

            if (not cf_token or not cf_account_id) and vault_addr and vault_token:
                cf_kv = vault_kv_read(
                    vault_addr=vault_addr,
                    vault_token=vault_token,
                    vault_mount=vault_mount,
                    path=manifest["admin_sources"]["cloudflare"],
                    opener=opener,
                )
                cf_token = cf_token or str(cf_kv.get("CLOUDFLARE_API_TOKEN") or cf_kv.get("api_token") or "").strip()
                cf_account_id = cf_account_id or str(cf_kv.get("CLOUDFLARE_ACCOUNT_ID") or cf_kv.get("account_id") or "").strip()

            if (not ali_ak or not ali_sk) and vault_addr and vault_token:
                ali_kv = vault_kv_read(
                    vault_addr=vault_addr,
                    vault_token=vault_token,
                    vault_mount=vault_mount,
                    path=manifest["admin_sources"]["alicloud"],
                    opener=opener,
                )
                ali_ak = ali_ak or str(ali_kv.get("access_key_id") or ali_kv.get("ALICLOUD_ACCESS_KEY") or "").strip()
                ali_sk = ali_sk or str(ali_kv.get("access_key_secret") or ali_kv.get("ALICLOUD_SECRET_KEY") or "").strip()

            secret_bucket.extend([cf_token, ali_ak, ali_sk])
            if not (cf_token and cf_account_id and ali_ak and ali_sk):
                raise RuntimeError(
                    "Missing Cloudflare or Aliyun admin credentials in env/Vault "
                    f"({manifest['admin_sources']['cloudflare']}, {manifest['admin_sources']['alicloud']})"
                )
            if apply and not (vault_addr and vault_token):
                raise RuntimeError("vault_addr and vault_token are required to sync storage credentials")

            zones = cf_request(
                f"/zones?name={urllib.parse.quote(manifest['zone_name'])}",
                cf_token=cf_token,
                opener=opener,
            ).get("result") or []
            if not zones:
                raise RuntimeError(f"Cloudflare zone not found: {manifest['zone_name']}")
            zone_id = zones[0]["id"]

            r2_res = _reconcile_cloudflare_r2(
                r2_cfg=manifest["r2"],
                zone_id=zone_id,
                cf_account_id=cf_account_id,
                cf_token=cf_token,
                existing_kv=existing_r2,
                apply=apply,
                rotate_credentials=rotate,
                secret_bucket=secret_bucket,
                opener=opener,
            )
            r2_creds = r2_res["kv_payload"]
            if apply and r2_creds != existing_r2:
                vault_kv_write(
                    vault_addr=vault_addr,
                    vault_token=vault_token,
                    vault_mount=vault_mount,
                    path=r2_kv_path,
                    data=r2_creds,
                    opener=opener,
                )
                summary["changed"] = True

            ali_res = _reconcile_aliyun_oss_cdn(
                ali_cfg=manifest["ali"],
                zone_name=manifest["zone_name"],
                zone_id=zone_id,
                cf_token=cf_token,
                ali_ak=ali_ak,
                ali_sk=ali_sk,
                existing_kv=existing_ali,
                apply=apply,
                run_cdn_verify_txt=run_cdn_verify_txt,
                rotate_credentials=rotate,
                wait_dns_seconds=wait_dns_seconds,
                secret_bucket=secret_bucket,
                opener=opener,
                vault_addr=vault_addr,
                vault_token=vault_token,
                vault_mount=vault_mount,
            )
            ali_creds = ali_res["kv_payload"]
            if apply and ali_creds != existing_ali:
                vault_kv_write(
                    vault_addr=vault_addr,
                    vault_token=vault_token,
                    vault_mount=vault_mount,
                    path=ali_kv_path,
                    data=ali_creds,
                    opener=opener,
                )
                summary["changed"] = True

            summary["changed"] = summary["changed"] or r2_res["changed"] or ali_res["changed"]
            summary["cloudflare_r2"] = {
                "bucket": manifest["r2"]["bucket_name"],
                "custom_domain": manifest["r2"]["custom_domain"],
                "actions": r2_res["actions"],
                "vault_kv_path": f"{vault_mount}/{r2_kv_path}",
            }
            summary["aliyun_oss_cdn"] = {
                "bucket": manifest["ali"]["bucket_name"],
                "cdn_domain": manifest["ali"]["cdn_domain"],
                "cdn_cname": ali_res["cdn_cname"],
                "oss_region": manifest["ali"]["oss_region"],
                "cdn_scope": manifest["ali"]["cdn_scope"],
                "actions": ali_res["actions"],
                "vault_kv_path": f"{vault_mount}/{ali_kv_path}",
            }

        if verify:
            probe_res = _probe_mirrors(
                r2_creds=r2_creds,
                ali_creds=ali_creds,
                probe_path=request.get("probe_path") or "releases/_health/probe.json",
                probe_origin=request.get("probe_origin") or "https://console.svc.plus",
                opener=opener,
            )
            summary["verification"] = probe_res
            if not all(item["ok"] for item in probe_res["https_probes"]):
                raise RuntimeError(f"Dual-mirror HTTPS probe failed: {json.dumps(probe_res)}")

        return summary
    except Exception as exc:
        raise RuntimeError(_scrub(str(exc), secret_bucket)) from None


def main(argv=None):
    payload_secrets = []
    try:
        payload = json.load(sys.stdin)
        for key in ("vault_token", "cloudflare_api_token", "alicloud_access_key", "alicloud_secret_key"):
            val = str(payload.get(key) or "").strip()
            if val:
                payload_secrets.append(val)
        result = reconcile(payload)
        result["ok"] = True
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "changed": False,
                    "error": _scrub(str(exc), payload_secrets),
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
