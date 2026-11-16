#!/usr/bin/env bash
# ==============================================================================
# safe-k3s-mem-restart: Safe, low-impact restart of K3s server to reclaim memory
#
# Follows the standard procedure:
#   0. Safety checks & maintenance window confirmation
#   1. Pre-restart etcd snapshot & health baseline capture
#   2. Validate/reload systemd environment (/etc/systemd/system/k3s.service.env)
#   3. Restart k3s service (KillMode=process protects running containers)
#   4. Wait for Kubernetes API readiness (/readyz)
#   5. Post-restart verification (Nodes, Pods, Flux Kustomizations, RSS memory)
# ==============================================================================

set -euo pipefail

KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG
ENV_FILE="/etc/systemd/system/k3s.service.env"
NON_INTERACTIVE=false

for arg in "$@"; do
  case "$arg" in
    --non-interactive|-y)
      NON_INTERACTIVE=true
      ;;
    --help|-h)
      echo "Usage: $0 [--non-interactive|-y]"
      echo "Safely restarts k3s server to release accumulated Go runtime memory."
      exit 0
      ;;
  esac
done

log() {
  echo "[$(date -Is)] $*"
}

warn() {
  echo "[$(date -Is)] [WARN] $*" >&2
}

err() {
  echo "[$(date -Is)] [ERROR] $*" >&2
  exit 1
}

# ------------------------------------------------------------------------------
# 0. Safety preflight
# ------------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  err "This script must be run as root."
fi

if ! command -v k3s >/dev/null 2>&1; then
  err "k3s binary not found on this host."
fi

if [ ! -f "$KUBECONFIG" ]; then
  err "Kubeconfig not found at $KUBECONFIG."
fi

CURRENT_PID=$(pgrep -f "^/usr/local/bin/k3s server" | tail -1 || true)
if [ -z "$CURRENT_PID" ]; then
  err "k3s server process is not currently running."
fi

CURRENT_RSS_KB=$(grep -i "VmRSS:" "/proc/${CURRENT_PID}/status" | awk '{print $2}' || echo "0")
CURRENT_RSS_MB=$((CURRENT_RSS_KB / 1024))
log "Target host: $(hostname)"
log "Current k3s server PID: ${CURRENT_PID}, Resident Memory (RSS): ${CURRENT_RSS_MB} MiB"

if [ "$NON_INTERACTIVE" = false ]; then
  echo ""
  echo "=================================================================="
  echo " SAFETY NOTICE:"
  echo " This will restart the k3s server process to reclaim memory."
  echo " Running workload containers (containerd-shim) will NOT be killed."
  echo " Kubernetes API access and Flux reconciliation will pause for ~30-60s."
  echo "=================================================================="
  read -r -p "Proceed with safe k3s restart? [y/N]: " confirm
  case "$confirm" in
    [yY][eE][sS]|[yY])
      log "Operator confirmed restart."
      ;;
    *)
      log "Aborted by operator."
      exit 0
      ;;
  esac
fi

# ------------------------------------------------------------------------------
# 1. Pre-restart snapshot and health baseline
# ------------------------------------------------------------------------------
SNAPSHOT_NAME="pre-mem-restart-$(date +%Y%m%d%H%M%S)"
log "Step 1: Taking etcd snapshot '${SNAPSHOT_NAME}'..."
if k3s etcd-snapshot save --name "${SNAPSHOT_NAME}"; then
  log "etcd snapshot saved successfully."
else
  warn "etcd snapshot save returned non-zero; continuing with caution if single-node cluster."
fi

log "Capturing baseline node & workload health..."
kubectl get nodes
log "Checking for existing failed resources..."
EXISTING_FAILS=$(kubectl get kustomization,helmrelease -A --no-headers 2>/dev/null | grep -v "True" || true)
if [ -n "$EXISTING_FAILS" ]; then
  warn "Pre-existing unready resources detected:"
  echo "$EXISTING_FAILS"
else
  log "All Kustomizations and HelmReleases are currently healthy (True)."
fi

# ------------------------------------------------------------------------------
# 2. Configure & reload systemd environment
# ------------------------------------------------------------------------------
log "Step 2: Checking systemd environment file (${ENV_FILE})..."
if [ ! -f "$ENV_FILE" ]; then
  log "Creating default ${ENV_FILE} with GOMEMLIMIT=768MiB and GOGC=50..."
  cat << 'EOF' > "$ENV_FILE"
# Managed by safe-k3s-mem-restart / Ansible
GOMEMLIMIT=768MiB
GOGC=50
EOF
fi

log "Current environment settings in ${ENV_FILE}:"
cat "$ENV_FILE"

log "Reloading systemd daemon..."
systemctl daemon-reload

# ------------------------------------------------------------------------------
# 3. Restart k3s service
# ------------------------------------------------------------------------------
log "Step 3: Restarting k3s.service..."
systemctl restart k3s
log "Restart command executed."

# ------------------------------------------------------------------------------
# 4. Wait for API readiness
# ------------------------------------------------------------------------------
log "Step 4: Waiting for Kubernetes API readiness (/readyz)..."
READY=false
for attempt in $(seq 1 30); do
  if kubectl get --raw=/readyz >/dev/null 2>&1; then
    READY=true
    log "Kubernetes API ready after attempt ${attempt}/30."
    break
  fi
  sleep 2
done

if [ "$READY" = false ]; then
  err "Kubernetes API failed to become ready within 60 seconds."
fi

# ------------------------------------------------------------------------------
# 5. Post-restart verification
# ------------------------------------------------------------------------------
log "Step 5: Verifying cluster and workload status..."
kubectl get nodes

log "Checking for abnormal pods (not Running and not Completed)..."
ABNORMAL_PODS=$(kubectl get pods -A --no-headers | grep -vE 'Running|Completed' || true)
if [ -n "$ABNORMAL_PODS" ]; then
  warn "Detected abnormal pods post-restart:"
  echo "$ABNORMAL_PODS"
else
  log "All pods are in Running or Completed state."
fi

log "Checking Flux Kustomization and HelmRelease status..."
kubectl get kustomization,helmrelease -A

NEW_PID=$(pgrep -f "^/usr/local/bin/k3s server" | tail -1 || true)
if [ -n "$NEW_PID" ]; then
  NEW_RSS_KB=$(grep -i "VmRSS:" "/proc/${NEW_PID}/status" | awk '{print $2}' || echo "0")
  NEW_RSS_MB=$((NEW_RSS_KB / 1024))
  log "Memory Comparison:"
  log "  Pre-restart PID: ${CURRENT_PID}, RSS: ${CURRENT_RSS_MB} MiB"
  log "  Post-restart PID: ${NEW_PID}, RSS: ${NEW_RSS_MB} MiB"
  RECLAIMED_MB=$((CURRENT_RSS_MB - NEW_RSS_MB))
  log "  Net physical memory reclaimed: ${RECLAIMED_MB} MiB"
fi

if kubectl get --raw /metrics >/dev/null 2>&1; then
  log "APIServer Go Runtime Metrics:"
  kubectl get --raw /metrics | grep -E '^go_goroutines|^process_resident_memory_bytes' || true
fi

log "Safe k3s restart completed successfully."
