#!/usr/bin/env bash
set -euo pipefail

# Helper script to start the Zitadel docker compose stack
cd "$(dirname "$0")"
docker-compose -f docker-compose.yaml up -d
