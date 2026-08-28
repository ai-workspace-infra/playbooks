#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module_root="${repo_root}/tools/xconnect-gateway-agent"

(
  cd "${module_root}"
  go test ./internal/cutover \
    -run 'Test(Readiness|CutoverAuthorizationSigningBytesGolden|EmptyPeer|MockHTTPS)' \
    -count=1
  go test ./internal/gateway \
    -run 'Test(CredentialResolverAndXrayCandidateProtection|RollbackCoversCommandsThatFailAfterPartialMutation|RuntimeRollbackFailureQuarantinesInterface|RuntimeRejectsSnapshotNodeBindingMismatchBeforeCommands)' \
    -count=1
)

"${repo_root}/scripts/validate-xconnect-gateway-role.sh"
"${repo_root}/scripts/test-xconnect-gateway-namespace.sh"

echo "accounts-only cutover integration harness passed"
