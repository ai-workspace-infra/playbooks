#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.svc.plus")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "")
ENCRYPTION_PASS = os.environ.get("BACKUP_ENCRYPTION_PASS", "")

if not VAULT_TOKEN:
    for path in [os.path.expanduser("~/.ai_workspace_auth_token"), os.path.expanduser("~/.vault-token")]:
        if os.path.exists(path):
            with open(path, "r") as f:
                VAULT_TOKEN = f.read().strip()
                break

if not VAULT_TOKEN or not ENCRYPTION_PASS:
    print("[ERROR] VAULT_TOKEN and BACKUP_ENCRYPTION_PASS are required.", file=sys.stderr)
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: restore_vault_kv.py <path_to_encrypted_backup.enc>", file=sys.stderr)
    sys.exit(1)

backup_file = sys.argv[1]

def make_request(url, headers=None, method="POST", data=None):
    if headers is None: headers = {}
    headers["X-Vault-Token"] = VAULT_TOKEN
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req) as r:
        return r.read().decode()

def ensure_engine_enabled(engine, version):
    req = urllib.request.Request(f"{VAULT_ADDR}/v1/sys/mounts", headers={"X-Vault-Token": VAULT_TOKEN})
    with urllib.request.urlopen(req) as r:
        mounts = json.loads(r.read().decode())
        if f"{engine}/" in mounts:
            return
    print(f"[INFO] Enabling KV engine '{engine}' (Version {version})...")
    make_request(f"{VAULT_ADDR}/v1/sys/mounts/{engine}", data=json.dumps({
        "type": "kv", "options": {"version": str(version)}
    }).encode("utf-8"))

def write_secret(engine, version, path, secret_data):
    endpoint = "data/" if version == 2 else ""
    url = f"{VAULT_ADDR}/v1/{engine}/{endpoint}{path}"
    payload = json.dumps({"data": secret_data} if version == 2 else secret_data).encode("utf-8")
    make_request(url, data=payload)

def main():
    dec_file = "/tmp/vault_backup_decrypted.json"
    print(f"[INFO] Decrypting file {backup_file} using AES-256-CBC...")
    subprocess.run([
        "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-salt",
        "-pass", f"pass:{ENCRYPTION_PASS}", "-in", backup_file, "-out", dec_file
    ], check=True)
        
    with open(dec_file, "r") as f:
        backup_data = json.load(f)
    os.remove(dec_file)

    engines = backup_data.get("engines", {})
    for engine, engine_data in engines.items():
        version = engine_data.get("version", 2)
        secrets = engine_data.get("secrets", {})
        print(f"[INFO] Restoring engine '{engine}' with {len(secrets)} secrets...")
        ensure_engine_enabled(engine, version)
        for path, secret_data in secrets.items():
            write_secret(engine, version, path, secret_data)
    print("[INFO] Vault restore completed.")

if __name__ == "__main__":
    main()
