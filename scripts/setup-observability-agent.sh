#!/usr/bin/env bash
# setup-observability-agent.sh - Standalone agent installer wrapper
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${DIR}/setup-observability.sh" --agent "$@"
