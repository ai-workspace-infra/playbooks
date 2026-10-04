#!/bin/bash
set -euo pipefail

# Build the migratectl binary from the checked-out accounts service.
#
# The same binary is used for both export (PROD) and import (UAT): the snapshot
# embeds a schemaHash derived from the binary's own schema.sql, and import
# refuses a snapshot whose hash does not match. Two binaries built from
# different accounts refs would fail that check.

ACCOUNTS_DIR="${ACCOUNTS_DIR:-accounts}"
OUTPUT="${OUTPUT:-${GITHUB_WORKSPACE:-$PWD}/bin/migratectl}"

if [ ! -d "${ACCOUNTS_DIR}/cmd/migratectl" ]; then
  echo "ERROR: ${ACCOUNTS_DIR}/cmd/migratectl not found -- is the accounts repository checked out?" >&2
  exit 1
fi

# Pinned to a static linux/amd64 binary rather than the runner's native target:
# the ssh transport copies this same file to the PROD and UAT hosts (both
# x86_64) and runs it there inside a minimal container, so it must not depend on
# the runner's architecture or on glibc being present in that image.
export CGO_ENABLED=0
export GOOS="${GOOS:-linux}"
export GOARCH="${GOARCH:-amd64}"

mkdir -p "$(dirname "${OUTPUT}")"
(cd "${ACCOUNTS_DIR}" && go build -o "${OUTPUT}" ./cmd/migratectl)

# Only meaningful when building for the host we are on; skip it when cross
# compiling so the check never becomes a false gate.
if [ "$(go env GOOS)/$(go env GOARCH)" = "${GOOS}/${GOARCH}" ]; then
  "${OUTPUT}" --help >/dev/null
fi

file "${OUTPUT}" 2>/dev/null || true
echo "migratectl built at ${OUTPUT} (${GOOS}/${GOARCH}, static)"
