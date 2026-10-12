#!/usr/bin/env bash
# Shared, executable body of the "Restore and refresh isolated snapshot" step
# in .github/workflows/refresh-data.yml.
#
# Regulatory information, not legal advice.
#
# This script is the SINGLE source of truth for the refresh step's shell logic.
# The workflow invokes it (with environment providing WORK/OUT/URL/EXPECTED/
# AUDIT/GITHUB_OUTPUT/PY/SCRIPT) and the local audit-retention test drives the
# SAME script with substituted local paths -- so no copied bash block can drift
# from the workflow.
#
# Behaviour:
#   * Initializes the reviewable audit path BEFORE any preflight check, so a
#     missing baseline URL/digest still leaves a durable failure diagnostic
#     (with a non-zero exit) rather than exiting before any audit exists.
#   * Never produces a candidate artifact on no-change or failure.
#   * Preserves the documented non-zero (failure) status for downstream
#     gating and the always-run audit upload.
#
# Required environment:
#   PY          python interpreter used to run refresh_isolated.py
#   SCRIPT      absolute/relative path to refresh_isolated.py
#   WORK        run-owned work dir (scrubbed first)
#   OUT         candidate output dir (scrubbed first; only written on a change)
#   URL         SNAPSHOT_BASELINE_URL (may be empty for the preflight check)
#   EXPECTED    SNAPSHOT_BASELINE_SHA256 (may be empty for the preflight check)
#   AUDIT       audit folder to populate (always written)
#   GITHUB_OUTPUT  file to append "key=value" outputs to
#
# Optional environment (used by local tests; unset in the real workflow):
#   LIVE=1      pass --live (default: offline -- the tests never fetch)
#   SOURCE_FILE "key=path" to pass --source-file to refresh_isolated.py
#
set -euo pipefail

: "${PY:?PY is required}"
: "${SCRIPT:?SCRIPT is required}"
: "${WORK:?WORK is required}"
: "${OUT:?OUT is required}"
: "${AUDIT:?AUDIT is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
URL="${URL:-}"
EXPECTED="${EXPECTED:-}"

rm -rf "${WORK}" "${OUT}"

# Initialize the audit directory BEFORE preflight so preflight failures also
# produce a reviewable diagnostic (the workflow's always-run upload receives a
# path even when it exits 2 before attempting a refresh).
rm -rf "${AUDIT}"
mkdir -p "${AUDIT}"

# Write a failure diagnostic and record the audit outputs. Used by preflight
# failures and by a refresh that never wrote its own audit.
_write_failure_audit() {
  local message="$1"
  "${PY}" - "$message" <<'PYEOF' > "${AUDIT}/refresh-audit.json"
import json, sys
print(json.dumps({"status": "failure", "attempted": False,
                  "message": sys.argv[1], "promotion_ready": False,
                  "disclaimer": "Regulatory information, not legal advice."}))
PYEOF
  echo "status=failure" >> "${GITHUB_OUTPUT}"
  echo "promotion_ready=false" >> "${GITHUB_OUTPUT}"
  echo "review_required=false" >> "${GITHUB_OUTPUT}"
  echo "selected_snapshot=" >> "${GITHUB_OUTPUT}"
  echo "audit_dir=${AUDIT}" >> "${GITHUB_OUTPUT}"
}

# --- Preflight: verify the trusted baseline configuration is complete --------
if [[ -z "${URL}" ]]; then
  _write_failure_audit "refresh failed: SNAPSHOT_BASELINE_URL is not set"
  echo "::error title=Snapshot refresh::SNAPSHOT_BASELINE_URL is not set; configure a trusted baseline bundle source."
  exit 2
fi
if [[ -z "${EXPECTED}" ]]; then
  _write_failure_audit "refresh failed: SNAPSHOT_BASELINE_SHA256 is not set"
  echo "::error title=Snapshot refresh::SNAPSHOT_BASELINE_SHA256 is not set; supply the trusted expected baseline digest."
  exit 2
fi

# --- Run the refresh, capturing the exit code WITHOUT aborting ---------------
bash_args=( "${PY}" "${SCRIPT}" --work "${WORK}" --baseline-url "${URL}" \
  --baseline-sha256 "${EXPECTED}" --out "${OUT}" )
if [[ "${LIVE:-}" == "1" ]]; then
  bash_args+=( --live )
fi
if [[ -n "${SOURCE_FILE:-}" ]]; then
  bash_args+=( --source-file "${SOURCE_FILE}" )
fi
set +e
"${bash_args[@]}"
REFRESH_RC=$?
set -e

# --- Surface accurate outputs for downstream gating --------------------------
if [[ -f "${WORK}/refresh-result.json" ]]; then
  STATUS="$("${PY}" -c "import json;print(json.load(open('${WORK}/refresh-result.json'))['status'])")"
  echo "status=${STATUS}" >> "${GITHUB_OUTPUT}"
  PROMO="$("${PY}" -c "import json;print(str(json.load(open('${WORK}/refresh-result.json')).get('promotion_ready', False)).lower())")"
  REVIEW="$("${PY}" -c "import json;print(str(json.load(open('${WORK}/refresh-result.json')).get('review_required', False)).lower())")"
  SEL="$("${PY}" -c "import json;print(json.load(open('${WORK}/refresh-result.json')).get('selected_snapshot_label', ''))")"
  echo "promotion_ready=${PROMO}" >> "${GITHUB_OUTPUT}"
  echo "review_required=${REVIEW}" >> "${GITHUB_OUTPUT}"
  echo "selected_snapshot=${SEL}" >> "${GITHUB_OUTPUT}"
else
  _write_failure_audit "refresh failed: refresh_isolated.py produced no refresh-result.json"
fi

# --- Copy the always-run outcome audit into the dedicated folder -------------
if [[ -f "${WORK}/refresh-audit.json" ]]; then
  cp "${WORK}/refresh-audit.json" "${AUDIT}/refresh-audit.json"
else
  # A run that failed before writing the audit is surfaced honestly as a
  # failure diagnostic rather than silently omitted.
  _write_failure_audit "refresh-audit.json was not produced"
fi
if [[ -f "${WORK}/refresh-result.json" ]]; then
  cp "${WORK}/refresh-result.json" "${AUDIT}/refresh-result.json"
fi
echo "audit_dir=${AUDIT}" >> "${GITHUB_OUTPUT}"

# --- Preserve the documented non-zero (failure) status ----------------------
if [[ "${REFRESH_RC}" -ne 0 ]]; then
  echo "::error title=Snapshot refresh::refresh_isolated.py exited ${REFRESH_RC} (see refresh-audit artifact)."
  exit "${REFRESH_RC}"
fi
exit 0
