#!/usr/bin/env bash
set -euo pipefail
umask 077
usage() { echo "Usage: import-subscriptions.sh --env ENV --deployment-id ID --accounts-domain HOST --csv FILE --manifest FILE --vars-file FILE --phase preview|apply|replay|compare|export [--cookie-file FILE] [--preview-receipt FILE] [--apply-payload FILE]"; }
ENVIRONMENT= DEPLOYMENT_ID= ACCOUNTS_DOMAIN= CSV= MANIFEST= VARS_FILE= PHASE= COOKIE_FILE= PREVIEW_RECEIPT= APPLY_PAYLOAD= REASON= REQUEST_ID= RECEIPT_DIR=
while (($#)); do
  case "$1" in
    --env) ENVIRONMENT=$2; shift 2;; --deployment-id) DEPLOYMENT_ID=$2; shift 2;; --accounts-domain) ACCOUNTS_DOMAIN=$2; shift 2;; --csv) CSV=$2; shift 2;; --manifest) MANIFEST=$2; shift 2;; --vars-file) VARS_FILE=$2; shift 2;; --phase) PHASE=$2; shift 2;; --cookie-file) COOKIE_FILE=$2; shift 2;; --preview-receipt) PREVIEW_RECEIPT=$2; shift 2;; --apply-payload) APPLY_PAYLOAD=$2; shift 2;; --reason) REASON=$2; shift 2;; --request-id) REQUEST_ID=$2; shift 2;; --receipt-dir) RECEIPT_DIR=$2; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;;
  esac
done
[[ -n "$ENVIRONMENT" && -n "$DEPLOYMENT_ID" && -n "$ACCOUNTS_DOMAIN" && -n "$CSV" && -n "$MANIFEST" && -n "$VARS_FILE" && -n "$PHASE" ]] || { usage >&2; exit 2; }
[[ -f "$CSV" && -f "$MANIFEST" && -f "$VARS_FILE" ]] || { echo "error: csv, manifest, and vars-file must exist" >&2; exit 2; }
valid_env() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "error: invalid environment" >&2; exit 2; }; }
valid_id() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]] || { echo "error: invalid deployment id" >&2; exit 2; }; }
valid_host() { [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$ ]] || { echo "error: invalid host" >&2; exit 2; }; }
valid_env "$ENVIRONMENT"; valid_id "$DEPLOYMENT_ID"; valid_host "$ACCOUNTS_DOMAIN"
[[ "$PHASE" == preview || "$PHASE" == apply || "$PHASE" == replay || "$PHASE" == compare || "$PHASE" == export ]] || { echo "error: invalid subscription phase" >&2; exit 2; }
command -v curl >/dev/null || { echo "error: missing curl" >&2; exit 2; }; command -v jq >/dev/null || { echo "error: missing jq" >&2; exit 2; }
if [[ -n "$COOKIE_FILE" ]]; then
  [[ -f "$COOKIE_FILE" ]] || { echo "error: cookie file not found" >&2; exit 2; }
  mode=$(stat -f "%Lp" "$COOKIE_FILE" 2>/dev/null || stat -c "%a" "$COOKIE_FILE")
  [[ "$mode" == 600 ]] || { echo "error: cookie file must be mode 0600" >&2; exit 2; }
fi
OUT_DIR=$(dirname "$MANIFEST")/receipts; [[ -n "$RECEIPT_DIR" ]] && OUT_DIR=$RECEIPT_DIR
mkdir -p "$OUT_DIR"; chmod 0700 "$OUT_DIR"
if [[ -z "$REQUEST_ID" ]]; then REQUEST_ID=$(uuidgen 2>/dev/null || date +%s); fi
if [[ -z "$REASON" ]]; then REASON="selfhost subscription import"; fi
endpoint="https://$ACCOUNTS_DOMAIN/admin/users/plan-groups/batch"
if [[ "$PHASE" == preview ]]; then
  [[ -n "$COOKIE_FILE" ]] || { echo "error: --cookie-file is required for preview" >&2; exit 2; }
  body=$(jq -n --arg requestId "$REQUEST_ID" --arg reason "$REASON" --rawfile csv "$CSV" '{mode:"preview",requestId:$requestId,reason:$reason,csv:$csv,reconcile:false}')
  response=$(curl --fail-with-body --silent --show-error --cookie "$COOKIE_FILE" -H "Content-Type: application/json" -X PUT "$endpoint" --data "$body")
  receipt_path="$OUT_DIR/$DEPLOYMENT_ID-subscription-preview-$REQUEST_ID.json"; printf "%s\n" "$response" >"$receipt_path"; chmod 0600 "$receipt_path"
  jq '{requestId:(.requestId // .request_id), changed:(.changed // .changes // .updates // null), expiresAt:(.expiresAt // .expires_at // null)}' "$receipt_path" || true
  printf "preview receipt: %s\n" "$receipt_path"
elif [[ "$PHASE" == apply || "$PHASE" == replay ]]; then
  [[ -n "$COOKIE_FILE" && -n "$PREVIEW_RECEIPT" && -n "$APPLY_PAYLOAD" ]] || { echo "error: apply/replay require --cookie-file, --preview-receipt, and reviewed --apply-payload" >&2; exit 2; }
  [[ -f "$PREVIEW_RECEIPT" && -f "$APPLY_PAYLOAD" ]] || { echo "error: apply files not found" >&2; exit 2; }
  token=$(jq -er '.previewToken // .preview_token' "$PREVIEW_RECEIPT") || { echo "error: preview receipt has no preview token" >&2; exit 2; }
  body=$(jq --arg token "$token" --arg requestId "$REQUEST_ID" --arg reason "$REASON" '. + {mode:"apply",previewToken:$token,requestId:$requestId,reason:$reason}' "$APPLY_PAYLOAD")
  response=$(curl --fail-with-body --silent --show-error --cookie "$COOKIE_FILE" -H "Content-Type: application/json" -X PUT "$endpoint" --data "$body")
  receipt_path="$OUT_DIR/$DEPLOYMENT_ID-subscription-$PHASE-$REQUEST_ID.json"; printf "%s\n" "$response" >"$receipt_path"; chmod 0600 "$receipt_path"
  jq '{requestId:(.requestId // .request_id), applied:(.applied // .updated // .changes // null)}' "$receipt_path" || true
  printf "apply receipt: %s\n" "$receipt_path"
else
  echo "$PHASE is a read-only contract; use the reviewed Accounts export/compare command from the vars file." >&2
  exit 3
fi

