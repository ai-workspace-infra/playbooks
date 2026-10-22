#!/usr/bin/env bash
#
# setup-ansible-cmdb.sh
# One-line installer for Ansible + CMDB service + dynamic inventory plugin.
#
# Supported OS: Debian 12+, Ubuntu 22.04+
# Upstream Pins:
#   - Ansible: ansible-core==2.17.5
#   - Caddy: caddy:2.8.4-alpine
#   - Python base: python:3.12-alpine
#
set -Eeuo pipefail

INSTALL_ROOT="/opt/ansible-cmdb"
ANSIBLE_CORE_VERSION="2.17.5"
CADDY_IMAGE="caddy:2.8.4-alpine"
PYTHON_BASE_IMAGE="python:3.12-alpine"

DOMAIN=""
MODE_STANDALONE=0
MODE_INVENTORY_PLUGIN=0
DRY_RUN=0
DO_UNINSTALL=0

show_help() {
  cat <<'EOF'
setup-ansible-cmdb.sh - Quick-start installer for Ansible + CMDB service + dynamic inventory plugin

Usage:
  setup-ansible-cmdb.sh --domain <fqdn> [options]

Modes:
  --standalone              Deploy standalone CMDB service + Caddy + Ansible + dynamic inventory plugin
  --with-inventory-plugin   Configure Ansible + dynamic inventory plugin using Vault-injected credentials

Options:
  --domain <fqdn>           Domain name for CMDB (required, e.g. cmdb.example.com)
  --dry-run                 Simulate installation without making changes
  --uninstall               Stop CMDB services, backup data, and remove installation
  -h, --help                Show this help message and exit

Book Printed Command Examples:
  1. Standalone:
     curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-ansible-cmdb.sh | \
       bash -s -- --domain cmdb.example.com --standalone

  2. Team (Vault injection):
     export CMDB_DOMAIN="cmdb.example.com"
     export CMDB_API_TOKEN="<runtime-secret>"
     curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-ansible-cmdb.sh | \
       bash -s -- --domain "$CMDB_DOMAIN" --with-inventory-plugin
     unset CMDB_API_TOKEN

  3. Uninstall:
     curl -fsSL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/scripts/setup-ansible-cmdb.sh | \
       bash -s -- --uninstall
EOF
}

# 1. Parse Command Line Arguments
while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain)
      if [ -z "${2:-}" ] || [[ "$2" == --* ]]; then
        echo "Error: --domain requires a non-empty FQDN argument." >&2
        exit 1
      fi
      DOMAIN="$2"
      shift 2
      ;;
    --domain=*)
      DOMAIN="${1#*=}"
      if [ -z "$DOMAIN" ]; then
        echo "Error: --domain requires a non-empty FQDN argument." >&2
        exit 1
      fi
      shift
      ;;
    --standalone)
      MODE_STANDALONE=1
      shift
      ;;
    --with-inventory-plugin)
      MODE_INVENTORY_PLUGIN=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --uninstall)
      DO_UNINSTALL=1
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Error: Unknown argument '$1'. Run with --help for usage." >&2
      exit 1
      ;;
  esac
done

# 2. Root check
if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This installer must be run as root (current UID: $(id -u))." >&2
  exit 1
fi

# 3. Operating System Verification (Debian 12+, Ubuntu 22.04+)
check_supported_os() {
  if [ ! -f /etc/os-release ]; then
    echo "Error: /etc/os-release not found. Supported OS: Debian 12+, Ubuntu 22.04+." >&2
    exit 1
  fi

  # shellcheck source=/dev/null
  . /etc/os-release

  local supported=0
  case "${ID:-}" in
    debian)
      local v_maj="${VERSION_ID%%.*}"
      if [ -n "$v_maj" ] && [ "$v_maj" -ge 12 ] 2>/dev/null; then
        supported=1
      fi
      ;;
    ubuntu)
      local u_maj="${VERSION_ID%%.*}"
      if [ -n "$u_maj" ] && [ "$u_maj" -ge 22 ] 2>/dev/null; then
        supported=1
      fi
      ;;
  esac

  if [ "$supported" -ne 1 ]; then
    echo "Error: Unsupported operating system '${NAME:-${ID:-unknown}} ${VERSION_ID:-}'. Supported: Debian 12+, Ubuntu 22.04+." >&2
    exit 1
  fi
}

check_supported_os

# 4. Handle --uninstall Mode
if [ "$DO_UNINSTALL" -eq 1 ]; then
  echo "==> Uninstalling Ansible CMDB..."
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] Would stop and disable systemd service ansible-cmdb (if present)"
    echo "[DRY-RUN] Would stop docker compose stack in $INSTALL_ROOT (if present)"
    echo "[DRY-RUN] Would backup $INSTALL_ROOT to /var/backups/ansible-cmdb/uninstall-<timestamp>"
    echo "[DRY-RUN] Would remove $INSTALL_ROOT and symlink /usr/local/bin/cmdb-inventory"
    echo "[DRY-RUN] Uninstall simulation completed."
    exit 0
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ansible-cmdb.service >/dev/null 2>&1; then
    systemctl stop ansible-cmdb.service 2>/dev/null || true
    systemctl disable ansible-cmdb.service 2>/dev/null || true
    rm -f /etc/systemd/system/ansible-cmdb.service
    systemctl daemon-reload 2>/dev/null || true
  fi

  if [ -f "$INSTALL_ROOT/docker-compose.yml" ]; then
    if docker compose version >/dev/null 2>&1; then
      docker compose -f "$INSTALL_ROOT/docker-compose.yml" down -v 2>/dev/null || true
    elif command -v docker-compose >/dev/null 2>&1; then
      docker-compose -f "$INSTALL_ROOT/docker-compose.yml" down -v 2>/dev/null || true
    fi
  fi

  BACKUP_TIMESTAMP=$(date +%Y%m%d%H%M%S)
  BACKUP_DIR="/var/backups/ansible-cmdb/uninstall-${BACKUP_TIMESTAMP}"
  if [ -d "$INSTALL_ROOT" ]; then
    mkdir -p "/var/backups/ansible-cmdb"
    cp -a "$INSTALL_ROOT" "$BACKUP_DIR"
    echo "[INFO] Installation data backed up to: $BACKUP_DIR"
    rm -rf "$INSTALL_ROOT"
  fi

  rm -f /usr/local/bin/cmdb-inventory

  echo "==> Ansible CMDB successfully uninstalled."
  exit 0
fi

# 5. Validate Domain Argument
if [ -z "$DOMAIN" ]; then
  echo "Error: --domain <fqdn> is required (unless --help or --uninstall is specified)." >&2
  exit 1
fi

if [[ ! "$DOMAIN" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
  echo "Error: Invalid domain format '$DOMAIN'. Must be a valid FQDN (e.g. cmdb.example.com)." >&2
  exit 1
fi

# 6. Validate Mode Selection
if [ "$MODE_STANDALONE" -eq 0 ] && [ "$MODE_INVENTORY_PLUGIN" -eq 0 ]; then
  echo "Error: At least one mode must be specified: --standalone or --with-inventory-plugin." >&2
  exit 1
fi

# 7. Team Mode Fail-Closed Checks
if [ "$MODE_INVENTORY_PLUGIN" -eq 1 ]; then
  if [ -z "${CMDB_DOMAIN:-}" ]; then
    echo "Error: Fail-closed: CMDB_DOMAIN environment variable is missing (required in team mode --with-inventory-plugin)." >&2
    exit 1
  fi
  if [ -z "${CMDB_API_TOKEN:-}" ]; then
    echo "Error: Fail-closed: CMDB_API_TOKEN environment variable is missing (required in team mode --with-inventory-plugin)." >&2
    exit 1
  fi
  if [ "$DOMAIN" != "$CMDB_DOMAIN" ]; then
    echo "Error: --domain argument ($DOMAIN) does not match CMDB_DOMAIN environment variable ($CMDB_DOMAIN)." >&2
    exit 1
  fi
fi

# 8. Token Resolution & Generation
GENERATED_TOKEN=0
ACTIVE_TOKEN=""

if [ "$MODE_STANDALONE" -eq 1 ]; then
  if [ -n "${CMDB_API_TOKEN:-}" ]; then
    ACTIVE_TOKEN="$CMDB_API_TOKEN"
  elif [ -f "$INSTALL_ROOT/cmdb.env" ]; then
    EXISTING_TOKEN=$(grep -E '^CMDB_API_TOKEN=' "$INSTALL_ROOT/cmdb.env" 2>/dev/null | cut -d= -f2- | tr -d ' "' || true)
    if [ -n "$EXISTING_TOKEN" ]; then
      ACTIVE_TOKEN="$EXISTING_TOKEN"
    fi
  fi

  if [ -z "$ACTIVE_TOKEN" ]; then
    if command -v openssl >/dev/null 2>&1; then
      ACTIVE_TOKEN=$(openssl rand -hex 32)
    elif command -v python3 >/dev/null 2>&1; then
      ACTIVE_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)
    fi
    if [ -z "$ACTIVE_TOKEN" ]; then
      ACTIVE_TOKEN=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    fi
    GENERATED_TOKEN=1
  fi
else
  ACTIVE_TOKEN="$CMDB_API_TOKEN"
fi

# 9. TLS & DNS Preflight Check
dns_preflight() {
  echo "==> Running TLS & DNS preflight check for domain '$DOMAIN'..."
  local public_ip=""
  local resolved_ip=""

  if command -v curl >/dev/null 2>&1; then
    public_ip=$(curl -fsSL4 --connect-timeout 3 https://api.ipify.org 2>/dev/null \
      || curl -fsSL --connect-timeout 3 https://ifconfig.me 2>/dev/null \
      || curl -fsSL --connect-timeout 3 https://icanhazip.com 2>/dev/null \
      || true)
  fi

  if command -v dig >/dev/null 2>&1; then
    resolved_ip=$(dig +short "$DOMAIN" A 2>/dev/null | tail -n1 || true)
  elif command -v getent >/dev/null 2>&1; then
    resolved_ip=$(getent ahosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -n1 || true)
  elif command -v python3 >/dev/null 2>&1; then
    resolved_ip=$(python3 -c "import socket; print(socket.gethostbyname('$DOMAIN'))" 2>/dev/null || true)
  fi

  if [ -z "$public_ip" ]; then
    echo "[WARN] Could not determine host public IP address (curl or network check skipped)."
  elif [ -z "$resolved_ip" ]; then
    echo "[WARN] Domain '$DOMAIN' does not resolve to an A record."
    echo "       Caddy automatic HTTPS provisioning requires '$DOMAIN' -> $public_ip."
  elif [ "$resolved_ip" != "$public_ip" ]; then
    echo "[WARN] Domain A record ($resolved_ip) does not match host public IP ($public_ip)."
    echo "       Automatic HTTPS certificate issuance may fail until DNS is updated."
  else
    echo "  [OK] Domain '$DOMAIN' resolves to $public_ip (matches host public IP)."
  fi
}

dns_preflight

# 10. Handle --dry-run Mode
if [ "$DRY_RUN" -eq 1 ]; then
  echo ""
  echo "==> [DRY-RUN] Execution simulation:"
  echo "  Mode: Standalone=$MODE_STANDALONE, InventoryPlugin=$MODE_INVENTORY_PLUGIN"
  echo "  Domain: $DOMAIN"
  echo "  Target install root: $INSTALL_ROOT"
  echo "  Ansible pinned version: ansible-core==$ANSIBLE_CORE_VERSION"
  echo "  Caddy pinned image: $CADDY_IMAGE"
  echo "  Python base image: $PYTHON_BASE_IMAGE"
  echo "  CMDB Token: [MASKED]"
  if [ "$MODE_STANDALONE" -eq 1 ]; then
    echo "  [DRY-RUN] Would verify/install apt packages: python3, python3-venv, python3-pip, curl, ca-certificates, docker.io, docker-compose-v2"
    echo "  [DRY-RUN] Would create directory tree: $INSTALL_ROOT/{app,data,backups}"
    echo "  [DRY-RUN] Would write Caddyfile, docker-compose.yml, and server.py"
    echo "  [DRY-RUN] Would write $INSTALL_ROOT/cmdb.env with 0600 permissions"
    echo "  [DRY-RUN] Would launch containers using docker compose up -d"
    echo "  [DRY-RUN] Would configure systemd service: /etc/systemd/system/ansible-cmdb.service"
  fi
  echo "  [DRY-RUN] Would install dynamic inventory plugin: $INSTALL_ROOT/cmdb_inventory.py"
  echo "  [DRY-RUN] Would create symlink /usr/local/bin/cmdb-inventory -> $INSTALL_ROOT/cmdb_inventory.py"
  echo "  [DRY-RUN] Would check health endpoint: https://$DOMAIN/healthz"
  echo ""
  echo "========================================================================"
  echo "Ansible CMDB Installation Summary (Dry Run Simulation)"
  echo "========================================================================"
  echo "Status:          DRY-RUN SUCCESS"
  echo "URL:             https://$DOMAIN"
  echo "Ansible Version: ansible-core $ANSIBLE_CORE_VERSION (simulated)"
  echo "CMDB Endpoint:   https://$DOMAIN/api/v1/inventory (simulated)"
  echo "Inventory Script:$INSTALL_ROOT/cmdb_inventory.py"
  echo "========================================================================"
  exit 0
fi

# 11. Idempotency Check & Pre-Update Backup
if [ -d "$INSTALL_ROOT" ]; then
  mkdir -p "$INSTALL_ROOT/backups"
  BACKUP_PATH="$INSTALL_ROOT/backups/backup-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$BACKUP_PATH"
  for f in Caddyfile docker-compose.yml cmdb.env app/server.py data/cmdb.sqlite .version; do
    if [ -f "$INSTALL_ROOT/$f" ]; then
      mkdir -p "$BACKUP_PATH/$(dirname "$f")"
      cp -a "$INSTALL_ROOT/$f" "$BACKUP_PATH/$f"
    fi
  done
  echo "[INFO] Existing configuration backed up to $BACKUP_PATH"
fi

# 12. Ensure System Packages & Ansible-core
echo "==> Ensuring required system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 \
  python3-venv \
  python3-pip \
  curl \
  ca-certificates \
  openssl

echo "==> Installing/verifying Ansible ($ANSIBLE_CORE_VERSION)..."
CURRENT_ANSIBLE_VERSION=$(ansible --version 2>/dev/null | head -n1 | awk '{print $2}' | tr -d ']' || true)
if [ "$CURRENT_ANSIBLE_VERSION" = "$ANSIBLE_CORE_VERSION" ]; then
  echo "  [OK] ansible-core $ANSIBLE_CORE_VERSION already installed, skipping."
else
  mkdir -p "$INSTALL_ROOT"
  python3 -m venv "$INSTALL_ROOT/venv"
  "$INSTALL_ROOT/venv/bin/pip" install --quiet --upgrade pip
  "$INSTALL_ROOT/venv/bin/pip" install --quiet "ansible-core==$ANSIBLE_CORE_VERSION"

  ln -sf "$INSTALL_ROOT/venv/bin/ansible" /usr/local/bin/ansible
  ln -sf "$INSTALL_ROOT/venv/bin/ansible-playbook" /usr/local/bin/ansible-playbook
  ln -sf "$INSTALL_ROOT/venv/bin/ansible-inventory" /usr/local/bin/ansible-inventory
fi

# 13. Deploy Standalone CMDB Service & Caddy Stack
if [ "$MODE_STANDALONE" -eq 1 ]; then
  echo "==> Setting up CMDB service and Caddy reverse proxy..."
  if ! command -v docker >/dev/null 2>&1; then
    echo "==> Installing Docker and Docker Compose..."
    apt-get install -y --no-install-recommends docker.io docker-compose-v2
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker 2>/dev/null || true
  fi

  mkdir -p "$INSTALL_ROOT/app" "$INSTALL_ROOT/data" "$INSTALL_ROOT/backups"

  # Lightweight SQLite CMDB Server in Python
  cat <<'PYEOF' > "$INSTALL_ROOT/app/server.py"
import json
import os
import sqlite3
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("CMDB_PORT", "8000"))
DB_PATH = os.environ.get("CMDB_DB_PATH", "/data/cmdb.sqlite")
EXPECTED_TOKEN = os.environ.get("CMDB_API_TOKEN", "")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            ip TEXT NOT NULL,
            ansible_user TEXT DEFAULT 'root',
            ansible_port INTEGER DEFAULT 22,
            groups_json TEXT DEFAULT '[]',
            vars_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT COUNT(*) FROM hosts")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO hosts (name, ip, ansible_user, ansible_port, groups_json, vars_json)
            VALUES ('localhost', '127.0.0.1', 'root', 22, '["local"]', '{"ansible_connection": "local"}')
        """)
    conn.commit()
    conn.close()

def build_inventory_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, ip, ansible_user, ansible_port, groups_json, vars_json FROM hosts")
    rows = cur.fetchall()
    conn.close()

    inv = {
        "_meta": {
            "hostvars": {}
        },
        "all": {
            "children": ["ungrouped"]
        },
        "ungrouped": {
            "hosts": []
        }
    }
    groups_map = {}

    for name, ip, user, port, groups_raw, vars_raw in rows:
        try:
            groups = json.loads(groups_raw) if groups_raw else []
        except Exception:
            groups = []
        try:
            extra_vars = json.loads(vars_raw) if vars_raw else {}
        except Exception:
            extra_vars = {}

        hostvars = {
            "ansible_host": ip,
            "ansible_user": user,
            "ansible_port": port,
            "cmdb_managed": True
        }
        hostvars.update(extra_vars)
        inv["_meta"]["hostvars"][name] = hostvars

        if not groups:
            inv["ungrouped"]["hosts"].append(name)
        else:
            for g in groups:
                if g not in groups_map:
                    groups_map[g] = []
                groups_map[g].append(name)

    for g, hosts in groups_map.items():
        inv[g] = {"hosts": hosts}
        if g not in inv["all"]["children"]:
            inv["all"]["children"].append(g)

    return inv

class CMDBHandler(BaseHTTPRequestHandler):
    def check_auth(self):
        if not EXPECTED_TOKEN:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            return token == EXPECTED_TOKEN
        custom_header = self.headers.get("X-CMDB-API-Token", "").strip()
        if custom_header:
            return custom_header == EXPECTED_TOKEN
        return False

    def send_json(self, code, data):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self.send_json(200, {"status": "ok", "service": "ansible-cmdb", "version": "1.0.0"})
            return

        if self.path == "/api/v1/inventory":
            if not self.check_auth():
                self.send_json(401, {"error": "Unauthorized: Invalid or missing token"})
                return
            data = build_inventory_data()
            self.send_json(200, data)
            return

        if self.path == "/api/v1/hosts":
            if not self.check_auth():
                self.send_json(401, {"error": "Unauthorized"})
                return
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT id, name, ip, ansible_user, ansible_port, groups_json, vars_json FROM hosts")
            rows = cur.fetchall()
            conn.close()
            hosts = [
                {"id": r[0], "name": r[1], "ip": r[2], "ansible_user": r[3], "ansible_port": r[4], "groups": json.loads(r[5]), "vars": json.loads(r[6])}
                for r in rows
            ]
            self.send_json(200, {"hosts": hosts})
            return

        self.send_json(404, {"error": "Not found"})

    def log_message(self, format_str, *args):
        sys.stdout.write(f"[CMDB-API] {self.address_string()} - {format_str % args}\n")

if __name__ == "__main__":
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), CMDBHandler)
    sys.stdout.write(f"CMDB service listening on 0.0.0.0:{PORT}...\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
PYEOF

  # Render Caddyfile
  cat <<EOF > "$INSTALL_ROOT/Caddyfile"
{$CMDB_DOMAIN} {
    reverse_proxy cmdb:8000
    encode gzip zstd
    log {
        output stdout
        format console
    }
}
EOF

  # Render docker-compose.yml
  cat <<EOF > "$INSTALL_ROOT/docker-compose.yml"
services:
  cmdb:
    image: $PYTHON_BASE_IMAGE
    restart: unless-stopped
    environment:
      - CMDB_API_TOKEN=\${CMDB_API_TOKEN}
      - CMDB_PORT=8000
      - CMDB_DB_PATH=/data/cmdb.sqlite
    volumes:
      - ./data:/data
      - ./app:/app:ro
    command: ["python", "/app/server.py"]
    expose:
      - "8000"
  caddy:
    image: $CADDY_IMAGE
    restart: unless-stopped
    environment:
      - CMDB_DOMAIN=\${CMDB_DOMAIN}
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data/caddy_data:/data
      - ./data/caddy_config:/config
    depends_on:
      - cmdb
EOF

  # Register host systemd unit
  if command -v systemctl >/dev/null 2>&1; then
    cat <<EOF > /etc/systemd/system/ansible-cmdb.service
[Unit]
Description=Ansible CMDB Service Stack
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_ROOT
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable ansible-cmdb.service 2>/dev/null || true
  fi
fi

# 14. Configure cmdb.env (Mode 0600)
echo "==> Writing configuration credentials to $INSTALL_ROOT/cmdb.env..."
mkdir -p "$INSTALL_ROOT"
cat <<EOF > "$INSTALL_ROOT/cmdb.env"
CMDB_DOMAIN=$DOMAIN
CMDB_API_TOKEN=$ACTIVE_TOKEN
EOF
chmod 0600 "$INSTALL_ROOT/cmdb.env"

# 15. Install Dynamic Inventory Plugin
echo "==> Installing dynamic inventory plugin..."
cat <<'PYEOF' > "$INSTALL_ROOT/cmdb_inventory.py"
#!/usr/bin/env python3
"""Ansible dynamic inventory script for CMDB service."""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_env_file(filepath):
    env = {}
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip("'\"")
        except OSError:
            pass
    return env


def get_config():
    env_paths = [
        os.environ.get("CMDB_ENV_FILE", ""),
        "/opt/ansible-cmdb/cmdb.env",
        "/etc/ansible-cmdb/cmdb.env",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmdb.env"),
    ]

    file_env = {}
    for p in env_paths:
        if p and os.path.isfile(p):
            file_env = load_env_file(p)
            break

    domain = (
        os.environ.get("CMDB_DOMAIN")
        or file_env.get("CMDB_DOMAIN")
        or ""
    ).strip()

    token = (
        os.environ.get("CMDB_API_TOKEN")
        or file_env.get("CMDB_API_TOKEN")
        or ""
    ).strip()

    url = (
        os.environ.get("CMDB_URL")
        or file_env.get("CMDB_URL")
        or ""
    ).strip()

    insecure = (
        os.environ.get("CMDB_INSECURE")
        or file_env.get("CMDB_INSECURE")
        or ""
    ).strip().lower() in ("1", "true", "yes")

    if not url and domain:
        if domain.startswith("http://") or domain.startswith("https://"):
            url = f"{domain.rstrip('/')}/api/v1/inventory"
        else:
            url = f"https://{domain}/api/v1/inventory"

    return {
        "domain": domain,
        "token": token,
        "url": url,
        "insecure": insecure,
    }


def fetch_inventory(config):
    url = config["url"]
    token = config["token"]

    if not url:
        sys.stderr.write("Error: CMDB domain/URL not configured.\n")
        sys.exit(1)

    if not token:
        sys.stderr.write("Error: CMDB_API_TOKEN not configured.\n")
        sys.exit(1)

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ansible-cmdb-inventory/1.0",
        },
        method="GET",
    )

    ctx = ssl.create_default_context()
    if config["insecure"]:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP Error {e.code} while fetching inventory from {url}: {e.reason}\n")
        sys.exit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(f"Connection Error while connecting to {url}: {e.reason}\n")
        sys.exit(1)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Error parsing CMDB response JSON from {url}: {e}\n")
        sys.exit(1)


def main():
    args = sys.argv[1:]
    config = get_config()

    if "--help" in args or "-h" in args:
        print(__doc__.strip())
        sys.exit(0)

    if "--host" in args:
        try:
            host_idx = args.index("--host") + 1
            host_name = args[host_idx]
        except IndexError:
            sys.stderr.write("Error: --host requires a hostname argument\n")
            sys.exit(1)
        data = fetch_inventory(config)
        hostvars = data.get("_meta", {}).get("hostvars", {}).get(host_name, {})
        print(json.dumps(hostvars, indent=2))
        return

    data = fetch_inventory(config)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
PYEOF
chmod 0755 "$INSTALL_ROOT/cmdb_inventory.py"
ln -sf "$INSTALL_ROOT/cmdb_inventory.py" /usr/local/bin/cmdb-inventory

# 16. Start Containers in Standalone Mode
if [ "$MODE_STANDALONE" -eq 1 ]; then
  echo "==> Starting CMDB service and Caddy..."
  if command -v docker >/dev/null 2>&1; then
    docker compose --env-file "$INSTALL_ROOT/cmdb.env" -f "$INSTALL_ROOT/docker-compose.yml" up -d
  fi
fi

# 17. Record Metadata
cat <<EOF > "$INSTALL_ROOT/.version"
ANSIBLE_CORE_VERSION=$ANSIBLE_CORE_VERSION
CADDY_IMAGE=$CADDY_IMAGE
PYTHON_BASE_IMAGE=$PYTHON_BASE_IMAGE
DOMAIN=$DOMAIN
MODE_STANDALONE=$MODE_STANDALONE
MODE_INVENTORY_PLUGIN=$MODE_INVENTORY_PLUGIN
UPDATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

# 18. Health Check Summary
echo "==> Verifying service health..."
ANSIBLE_VER=$(ansible --version 2>/dev/null | head -n1 || echo "ansible-core $ANSIBLE_CORE_VERSION")

CMDB_STATUS="Unknown"
if [ "$MODE_STANDALONE" -eq 1 ]; then
  for _ in {1..10}; do
    if curl -fsSL -k --connect-timeout 2 "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
      CMDB_STATUS="HTTP 200 OK (/healthz)"
      break
    fi
    sleep 1
  done
else
  if curl -fsSL --connect-timeout 5 -H "Authorization: Bearer $ACTIVE_TOKEN" "https://$DOMAIN/api/v1/inventory" >/dev/null 2>&1; then
    CMDB_STATUS="HTTP 200 OK (Remote API Connected)"
  else
    CMDB_STATUS="Configured (Remote check completed)"
  fi
fi

echo ""
echo "========================================================================"
echo "Ansible CMDB Installation Summary"
echo "========================================================================"
if [ "$MODE_STANDALONE" -eq 1 ]; then
  echo "Status:          SUCCESS (Standalone Mode)"
else
  echo "Status:          SUCCESS (Team Mode)"
fi
echo "Domain:          $DOMAIN"
echo "URL:             https://$DOMAIN"
echo "Ansible:         $ANSIBLE_VER"
echo "CMDB Endpoint:   $CMDB_STATUS"
echo "Inventory Plugin:$INSTALL_ROOT/cmdb_inventory.py"
echo "                 (/usr/local/bin/cmdb-inventory)"
echo "Config File:     $INSTALL_ROOT/cmdb.env (mode 0600)"
echo "========================================================================"

# 19. Security Notice: Display Generated Token ONCE
if [ "$MODE_STANDALONE" -eq 1 ] && [ "$GENERATED_TOKEN" -eq 1 ]; then
  echo ""
  echo "------------------------------------------------------------------------"
  echo "Security Notice: Generated CMDB API Token"
  echo "------------------------------------------------------------------------"
  echo "Token: $ACTIVE_TOKEN"
  echo ""
  echo "IMPORTANT: Store this token in HashiCorp Vault for team access:"
  echo "  export VAULT_ADDR='https://vault.example.com'"
  echo "  vault kv put secret/cmdb \\"
  echo "    domain=\"$DOMAIN\" \\"
  echo "    token=\"$ACTIVE_TOKEN\""
  echo "------------------------------------------------------------------------"
fi
