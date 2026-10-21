#!/usr/bin/env bash
# Run on the maintenance host, or on a VPS with a reviewed local inventory.
set -euo pipefail
umask 077
: "${PLAYBOOKS_COMMIT:?full reviewed Playbooks commit required}"
: "${GITOPS_COMMIT:?full reviewed GitOps commit required}"
: "${NODE_RELEASE_MANIFEST:?GitOps-relative release contract required}"
[[ "$PLAYBOOKS_COMMIT" =~ ^[0-9a-f]{40}$ && "$GITOPS_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo 'Both revisions must be full immutable commit SHAs.' >&2; exit 1;
}
for tool in git python3 ansible-playbook; do
    command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
release_dir="$(mktemp -d /tmp/xconnect-release.XXXXXXXX)"
trap 'rm -rf -- "$release_dir"' EXIT
checkout_release() {
    local name="$1" commit="$2"
    git init -q "$release_dir/$name"
    git -C "$release_dir/$name" remote add origin "https://github.com/ai-workspace-infra/$name.git"
    git -C "$release_dir/$name" fetch -q --depth=1 origin "$commit"
    git -C "$release_dir/$name" checkout -q --detach FETCH_HEAD
}
checkout_release playbooks "$PLAYBOOKS_COMMIT"
checkout_release gitops "$GITOPS_COMMIT"
python3 "$release_dir/playbooks/scripts/deploy-pinned-xconnect.py" \
    --gitops "$release_dir/gitops" --gitops-commit "$GITOPS_COMMIT" \
    --manifest "$NODE_RELEASE_MANIFEST" "$@"
