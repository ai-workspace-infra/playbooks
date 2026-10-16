#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd); source "$SCRIPT_DIR/common.sh"
usage() { echo "Usage: control.sh --env ENV --deployment-id ID --console-domain HOST --accounts-domain HOST --billing-domain HOST --postgres-domain HOST --manifest FILE --inventory FILE --vars-file FILE --phase preflight|bootstrap|initialize|activate|verify|converge"; }
ENVIRONMENT= DEPLOYMENT_ID= CONSOLE_DOMAIN= ACCOUNTS_DOMAIN= BILLING_DOMAIN= POSTGRES_DOMAIN= MANIFEST= INVENTORY= VARS_FILE= PHASE= RECEIPT_DIR=
while (($#)); do
  case "$1" in
    --env) ENVIRONMENT=$2; shift 2;; --deployment-id) DEPLOYMENT_ID=$2; shift 2;; --console-domain) CONSOLE_DOMAIN=$2; shift 2;; --accounts-domain) ACCOUNTS_DOMAIN=$2; shift 2;;
    --billing-domain) BILLING_DOMAIN=$2; shift 2;; --postgres-domain) POSTGRES_DOMAIN=$2; shift 2;; --manifest) MANIFEST=$2; shift 2;; --inventory) INVENTORY=$2; shift 2;; --vars-file) VARS_FILE=$2; shift 2;; --phase) PHASE=$2; shift 2;; --receipt-dir) RECEIPT_DIR=$2; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; die "unknown argument: $1";;
  esac
done
[[ -n "$ENVIRONMENT" && -n "$DEPLOYMENT_ID" && -n "$CONSOLE_DOMAIN" && -n "$ACCOUNTS_DOMAIN" && -n "$BILLING_DOMAIN" && -n "$POSTGRES_DOMAIN" && -n "$MANIFEST" && -n "$INVENTORY" && -n "$VARS_FILE" && -n "$PHASE" ]] || { usage >&2; exit 2; }
valid_host "$CONSOLE_DOMAIN"; valid_host "$ACCOUNTS_DOMAIN"; valid_host "$BILLING_DOMAIN"; valid_host "$POSTGRES_DOMAIN"; require_common
[[ "$PHASE" == preflight || "$PHASE" == bootstrap || "$PHASE" == initialize || "$PHASE" == activate || "$PHASE" == verify || "$PHASE" == converge ]] || die "invalid control phase: $PHASE"
extra=(-e "prod_v2_console_domain=$CONSOLE_DOMAIN" -e "prod_v2_accounts_domain=$ACCOUNTS_DOMAIN" -e "prod_v2_billing_domain=$BILLING_DOMAIN" -e "prod_v2_postgres_domain=$POSTGRES_DOMAIN")
case "$PHASE" in
  preflight) ansible-playbook --syntax-check -i "$INVENTORY" "$PLAYBOOK_ROOT/deploy-prod-v2-control.yml";;
  bootstrap) run_playbook deploy-prod-v2-control.yml "${extra[@]}";;
  initialize) run_playbook initialize-web-saas-native.yml "${extra[@]}";;
  activate|converge) run_playbook activate-prod-v2-control.yml "${extra[@]}";;
  verify) run_playbook activate-prod-v2-control.yml "${extra[@]}" --check;;
esac
receipt "control-$PHASE" applied

