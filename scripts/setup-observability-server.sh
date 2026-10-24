#!/usr/bin/env bash
# setup-observability-server.sh - Standalone server installer wrapper
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${DIR}/setup-observability.sh" --server "$@"
