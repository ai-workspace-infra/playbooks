#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.svc.plus")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "")
VAULT_ROLE = os.environ.get("VAULT_ROLE", "")
VAULT_JWT = os.environ.get("VAULT_JWT", "")

ENCRYPTION_PASS = os.environ.get("BACKUP_ENCRYPTION_PASS", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_REGION = os.environ.get("S3_REGION", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "vault-backups")
VAULT_IAC_STATE_PATH = os.environ.get(
    "VAULT_IAC_STATE_PATH",
    f"CICD/{os.environ.get('VAULT_ENV_PATH', 'prod')}/iac_state",
)

# Fallback token locations
if not VAULT_TOKEN:
    for path in [os.path.expanduser("~/.ai_workspace_auth_token"), os.path.expanduser("~/.vault-token")]:
        if os.path.exists(path):
            with open(path, "r") as f:
                VAULT_TOKEN = f.read().strip()
                break

if not ENCRYPTION_PASS:
    print("[ERROR] BACKUP_ENCRYPTION_PASS environment variable is required for high-strength encryption.", file=sys.stderr)
    sys.exit(1)

def make_request(url, headers=None, method="GET", data=None):
    if headers is None: headers = {}
    headers["X-Vault-Token"] = VAULT_TOKEN
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise e

def login_jwt():
    global VAULT_TOKEN
    if VAULT_JWT and VAULT_ROLE:
        url = f"{VAULT_ADDR}/v1/auth/jwt/login"
        payload = json.dumps({"jwt": VAULT_JWT, "role": VAULT_ROLE}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                VAULT_TOKEN = json.loads(resp.read().decode())["auth"]["client_token"]
                print("[INFO] Successfully authenticated to Vault via JWT.")
        except Exception as e:
            print(f"[FATAL] JWT authentication failed: {e}", file=sys.stderr)
            sys.exit(1)

def get_kv_engines():
    res = make_request(f"{VAULT_ADDR}/v1/sys/mounts")
    if not res: return {}
    mounts = res.get("data", res)
    kv_engines = {}
    for mount_path, config in mounts.items():
        if isinstance(config, dict) and config.get("type") == "kv":
            clean_path = mount_path.rstrip("/")
            # Options might be None or missing config
            options = config.get("options") or {}
            version = options.get("version", "1")
            kv_engines[clean_path] = int(version)
    return kv_engines

def list_keys(engine, version, path):
    endpoint = "metadata/" if version == 2 else ""
    url = f"{VAULT_ADDR}/v1/{engine}/{endpoint}{path}?list=true"
    res = make_request(url)
    return res["data"]["keys"] if res and "data" in res and "keys" in res["data"] else []

def get_secret(engine, version, path):
    endpoint = "data/" if version == 2 else ""
    url = f"{VAULT_ADDR}/v1/{engine}/{endpoint}{path}"
    res = make_request(url)
    if res and "data" in res:
        return res["data"].get("data", {}) if version == 2 else res["data"]
    return {}

def crawl_engine(engine, version, current_path=""):
    secrets = {}
    keys = list_keys(engine, version, current_path)
    for key in keys:
        full_path = current_path + key
        if key.endswith("/"):
            secrets.update(crawl_engine(engine, version, full_path))
        else:
            secret_data = get_secret(engine, version, full_path)
            if secret_data:
                secrets[full_path] = secret_data
    return secrets

def main():
    login_jwt()
    if not VAULT_TOKEN:
        print("[FATAL] Vault Token is missing and JWT authentication did not run.", file=sys.stderr)
        sys.exit(1)

    # 1. Fetch S3 config from Vault if not defined in Env
    global S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT, S3_REGION
    if not S3_BUCKET or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print(f"[INFO] Fetching S3 configurations dynamically from Vault {VAULT_IAC_STATE_PATH}...")
        s3_secrets = get_secret("kv", 2, VAULT_IAC_STATE_PATH.removeprefix("kv/"))
        if not s3_secrets:
            print(f"[FATAL] Could not retrieve S3 config from Vault {VAULT_IAC_STATE_PATH}.", file=sys.stderr)
            sys.exit(1)
        S3_BUCKET = s3_secrets.get("TF_STATE_BUCKET", S3_BUCKET)
        S3_ACCESS_KEY = s3_secrets.get("TF_STATE_ACCESS_KEY", S3_ACCESS_KEY)
        S3_SECRET_KEY = s3_secrets.get("TF_STATE_SECRET_KEY", S3_SECRET_KEY)
        S3_ENDPOINT = s3_secrets.get("TF_STATE_ENDPOINT", S3_ENDPOINT)
        S3_REGION = s3_secrets.get("TF_STATE_REGION", S3_REGION)

    if not S3_BUCKET or not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print("[FATAL] S3 Credentials are not provided and could not be loaded from Vault.", file=sys.stderr)
        sys.exit(1)

    # 2. Backup KV secrets
    engines = get_kv_engines()
    print(f"[INFO] Found KV Engines: {engines}")
    
    backup_data = {"timestamp": datetime.utcnow().isoformat() + "Z", "engines": {}}
    for engine, version in engines.items():
        print(f"[INFO] Crawling engine '{engine}' (KV v{version})...")
        secrets = crawl_engine(engine, version)
        backup_data["engines"][engine] = {"version": version, "secrets": secrets}
        print(f"[INFO] Backed up {len(secrets)} secrets.")
            
    staging_file = "/tmp/vault_backup.json"
    enc_file = f"/tmp/vault_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json.enc"
    with open(staging_file, "w") as f:
        json.dump(backup_data, f, indent=2)
        
    print("[INFO] Encrypting vault data using AES-256-CBC...")
    subprocess.run([
        "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "100000",
        "-pass", f"pass:{ENCRYPTION_PASS}", "-in", staging_file, "-out", enc_file
    ], check=True)
    os.remove(staging_file)
        
    s3_path = f"s3://{S3_BUCKET}/{S3_PREFIX}/{os.path.basename(enc_file)}"
    print(f"[INFO] Uploading Vault backup to S3: {s3_path}")
    
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = S3_ACCESS_KEY
    env["AWS_SECRET_ACCESS_KEY"] = S3_SECRET_KEY
    env["AWS_DEFAULT_REGION"] = S3_REGION or "us-east-1"
    
    upload_cmd = ["aws", "s3", "cp", enc_file, s3_path]
    if S3_ENDPOINT:
        upload_cmd.extend(["--endpoint-url", S3_ENDPOINT])
        
    subprocess.run(upload_cmd, env=env, check=True)
    os.remove(enc_file)
    print("[INFO] Vault backup completed successfully.")

if __name__ == "__main__":
    main()
