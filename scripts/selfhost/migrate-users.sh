#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd); source "$SCRIPT_DIR/common.sh"
usage() { echo "Usage: migrate-users.sh --env ENV --deployment-id ID --source-accounts-domain HOST --accounts-domain HOST --manifest FILE --inventory FILE --vars-file FILE --phase preflight|export|preview|apply|compare|verify"; }
ENVIRONMENT= DEPLOYMENT_ID= SOURCE_ACCOUNTS_DOMAIN= ACCOUNTS_DOMAIN= MANIFEST= INVENTORY= VARS_FILE= PHASE= RECEIPT_DIR=
while (($#)); do
  case "$1" in
    --env) ENVIRONMENT=$2; shift 2;; --deployment-id) DEPLOYMENT_ID=$2; shift 2;; --source-accounts-domain) SOURCE_ACCOUNTS_DOMAIN=$2; shift 2;; --accounts-domain) ACCOUNTS_DOMAIN=$2; shift 2;; --manifest) MANIFEST=$2; shift 2;; --inventory) INVENTORY=$2; shift 2;; --vars-file) VARS_FILE=$2; shift 2;; --phase) PHASE=$2; shift 2;; --receipt-dir) RECEIPT_DIR=$2; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; die "unknown argument: $1";;
  esac
done
[[ -n "$ENVIRONMENT" && -n "$DEPLOYMENT_ID" && -n "$SOURCE_ACCOUNTS_DOMAIN" && -n "$ACCOUNTS_DOMAIN" && -n "$MANIFEST" && -n "$INVENTORY" && -n "$VARS_FILE" && -n "$PHASE" ]] || { usage >&2; exit 2; }
valid_host "$SOURCE_ACCOUNTS_DOMAIN"; valid_host "$ACCOUNTS_DOMAIN"; require_common
[[ "$PHASE" == preflight || "$PHASE" == export || "$PHASE" == preview || "$PHASE" == apply || "$PHASE" == compare || "$PHASE" == verify ]] || die "invalid migration phase: $PHASE"
extra=(-e "prod_v2_source_accounts_domain=$SOURCE_ACCOUNTS_DOMAIN" -e "prod_v2_target_accounts_domain=$ACCOUNTS_DOMAIN" -e "prod_v2_migration_phase=$PHASE")
case "$PHASE" in
  preflight) ansible-playbook --syntax-check -i "$INVENTORY" "$PLAYBOOK_ROOT/migrate-prod-v2-accounts.yml";;
  *) run_playbook migrate-prod-v2-accounts.yml "${extra[@]}";;
esac
receipt "migrate-users-$PHASE" applied

