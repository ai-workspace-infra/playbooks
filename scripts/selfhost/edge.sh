#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd); source "$SCRIPT_DIR/common.sh"
usage() { echo "Usage: edge.sh --resource-class free|plus|team --env ENV --deployment-id ID --region REGION --node-id ID --node-domain HOST --accounts-domain HOST --billing-domain HOST --manifest FILE --inventory FILE --vars-file FILE --phase preflight|deploy|verify|converge"; }
RESOURCE_CLASS= ENVIRONMENT= DEPLOYMENT_ID= REGION= NODE_ID= NODE_DOMAIN= ACCOUNTS_DOMAIN= BILLING_DOMAIN= MANIFEST= INVENTORY= VARS_FILE= PHASE= RECEIPT_DIR=
while (($#)); do
  case "$1" in
    --resource-class) RESOURCE_CLASS=$2; shift 2;; --env) ENVIRONMENT=$2; shift 2;; --deployment-id) DEPLOYMENT_ID=$2; shift 2;;
    --region) REGION=$2; shift 2;; --node-id) NODE_ID=$2; shift 2;; --node-domain) NODE_DOMAIN=$2; shift 2;;
    --accounts-domain) ACCOUNTS_DOMAIN=$2; shift 2;; --billing-domain) BILLING_DOMAIN=$2; shift 2;;
    --manifest) MANIFEST=$2; shift 2;; --inventory) INVENTORY=$2; shift 2;; --vars-file) VARS_FILE=$2; shift 2;;
    --phase) PHASE=$2; shift 2;; --receipt-dir) RECEIPT_DIR=$2; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; die "unknown argument: $1";;
  esac
done
[[ -n "$RESOURCE_CLASS" && -n "$ENVIRONMENT" && -n "$DEPLOYMENT_ID" && -n "$REGION" && -n "$NODE_ID" && -n "$NODE_DOMAIN" && -n "$ACCOUNTS_DOMAIN" && -n "$BILLING_DOMAIN" && -n "$MANIFEST" && -n "$INVENTORY" && -n "$VARS_FILE" && -n "$PHASE" ]] || { usage >&2; exit 2; }
valid_class "$RESOURCE_CLASS"; valid_env "$ENVIRONMENT"; valid_id "$DEPLOYMENT_ID"; valid_id "$NODE_ID"; valid_host "$NODE_DOMAIN"; valid_host "$ACCOUNTS_DOMAIN"; valid_host "$BILLING_DOMAIN"; require_common
[[ "$PHASE" == preflight || "$PHASE" == deploy || "$PHASE" == verify || "$PHASE" == converge ]] || die "invalid edge phase: $PHASE"
extra=(-e "resource_class=$RESOURCE_CLASS" -e "prod_v2_region=$REGION" -e "prod_v2_node_id=$NODE_ID" -e "prod_v2_node_domain=$NODE_DOMAIN" -e "prod_v2_accounts_domain=$ACCOUNTS_DOMAIN" -e "prod_v2_billing_domain=$BILLING_DOMAIN")
case "$PHASE" in
  preflight) ansible-playbook --syntax-check -i "$INVENTORY" "$PLAYBOOK_ROOT/deploy-prod-v2-edges.yml";;
  deploy|converge) run_playbook deploy-prod-v2-edges.yml "${extra[@]}" --limit "$NODE_ID";;
  verify) run_playbook refresh-prod-v2-edge-config.yml "${extra[@]}" --limit "$NODE_ID";;
esac
receipt "edge-$RESOURCE_CLASS-$NODE_ID-$PHASE" applied

