#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: check-wireguard-over-vless-closure-evidence.sh <evidence-dir>

Checks a WireGuard-over-VLESS closure evidence directory produced by
verify-wireguard-over-vless-closure.sh.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

evidence_dir="${1:-}"
if [[ -z "${evidence_dir}" ]]; then
  usage >&2
  exit 2
fi
if [[ ! -d "${evidence_dir}" ]]; then
  echo "evidence directory not found: ${evidence_dir}" >&2
  exit 2
fi

verdict_file="${evidence_dir}/closure-verdict.env"
requirements_file="${evidence_dir}/closure-requirements.tsv"
summary_file="${evidence_dir}/summary.env"
steps_file="${evidence_dir}/steps.log"
closure_check_file="${evidence_dir}/closure-check.log"

missing_files=()
for file in "${verdict_file}" "${requirements_file}" "${summary_file}" "${steps_file}" "${closure_check_file}"; do
  if [[ ! -f "${file}" ]]; then
    missing_files+=("${file}")
  fi
done
if [[ ${#missing_files[@]} -gt 0 ]]; then
  printf 'missing evidence file: %s\n' "${missing_files[@]}" >&2
  exit 2
fi

closure_ready="$(awk -F '=' '$1 == "closure_ready" { print $2 }' "${verdict_file}" | tail -n 1)"
required_items_failed="$(awk -F '=' '$1 == "required_items_failed" { print $2 }' "${verdict_file}" | tail -n 1)"
verdict_requirements_file="$(awk -F '=' '$1 == "requirements_file" { print $2 }' "${verdict_file}" | tail -n 1)"
summary_status="$(awk -F '=' '$1 == "status" { print $2 }' "${summary_file}" | tail -n 1)"
closure_complete="$(awk -F '=' '$1 == "closure_complete" { print $2 }' "${summary_file}" | tail -n 1)"

tsv_failed_items="$(
  awk -F '\t' '
    NR == 1 {
      next
    }
    $4 == "1" && $3 != "ok" {
      print $1 ":" $3
    }
  ' "${requirements_file}" | paste -sd ',' -
)"

step_mismatches="$(
  awk -F '\t' '
    FNR == NR {
      step_status[$2] = $3
      next
    }
    FNR == 1 {
      next
    }
    $4 == "1" && step_status[$2] != $3 {
      actual = step_status[$2]
      if (actual == "") {
        actual = "not_run"
      }
      print $1 ":" $2 ":tsv=" $3 ":steps=" actual
    }
  ' "${steps_file}" "${requirements_file}" | paste -sd ',' -
)"

if [[ "${closure_ready}" != "1" ]]; then
  echo "closure_ready=${closure_ready:-missing}" >&2
  echo "summary_status=${summary_status:-missing}" >&2
  echo "closure_complete=${closure_complete:-missing}" >&2
  if [[ -n "${required_items_failed}" ]]; then
    echo "required_items_failed=${required_items_failed}" >&2
  fi
  if [[ -n "${tsv_failed_items}" && "${tsv_failed_items}" != "${required_items_failed}" ]]; then
    echo "requirements_tsv_failed=${tsv_failed_items}" >&2
  fi
  exit 1
fi

if [[ "${summary_status}" != "0" ]]; then
  echo "closure verdict says ready, but summary status=${summary_status:-missing}" >&2
  exit 1
fi

if [[ "${verdict_requirements_file}" != "${requirements_file}" ]]; then
  echo "closure verdict requirements_file does not match evidence directory" >&2
  echo "requirements_file=${verdict_requirements_file:-missing}" >&2
  echo "expected_requirements_file=${requirements_file}" >&2
  exit 1
fi

if [[ "${closure_complete}" != "1" ]]; then
  echo "closure verdict says ready, but summary closure_complete=${closure_complete:-missing}" >&2
  exit 1
fi

if [[ -n "${required_items_failed}" ]]; then
  echo "closure verdict says ready, but required_items_failed=${required_items_failed}" >&2
  if [[ -n "${tsv_failed_items}" && "${tsv_failed_items}" != "${required_items_failed}" ]]; then
    echo "requirements_tsv_failed=${tsv_failed_items}" >&2
  fi
  exit 1
fi

if [[ -n "${tsv_failed_items}" ]]; then
  echo "closure verdict says ready, but required TSV items are not ok: ${tsv_failed_items}" >&2
  exit 1
fi

if [[ -n "${step_mismatches}" ]]; then
  echo "closure TSV does not match steps.log: ${step_mismatches}" >&2
  exit 1
fi

echo "closure evidence OK: ${evidence_dir}"
