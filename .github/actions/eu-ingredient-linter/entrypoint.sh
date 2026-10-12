#!/usr/bin/env bash
#
# EU Ingredient Linter Action entrypoint.
#
# Regulatory information, not legal advice.
#
# Validates the supplied arguments, fully verifies the explicitly-supplied
# snapshot (bundle .zip OR imported directory — both run the established full
# snapshot verifier, never a size heuristic), runs the installed
# eu-ingredient-linter CLI once to emit GitHub annotations AND the cited JSON
# report from a single shared evaluation, then propagates the exit code
# according to the documented `fail-on` policy. It never fetches official
# exports and never falls back to a curated/empty registry.
#
# Operational failures (missing/invalid input, snapshot verification, CLI
# execution, report write) always fail regardless of the `fail-on` policy. Only
# a regulatory finding is governed by `fail-on`.
#
# All user-supplied paths are kept quoted; all diagnostics are escaped so an
# untrusted path cannot inject a second workflow command.

set -euo pipefail

DISCLAIMER="Regulatory information, not legal advice."

INPUT_FILE="${1:?missing input-file}"
INPUT_FORMAT="${2:-csv}"
SNAPSHOT_PATH="${3:?missing snapshot-path}"
REPORT_OUTPUT="${4:-eu-ingredient-report.json}"
FAIL_ON="${5:-findings}"
GITHUB_FILE="${6:-${INPUT_FILE}}"

declare -a REG_ARGS=()
declare -a INPUT_ARGS=()

usage_line() { printf 'usage: entrypoint <input-file> <input-format> <snapshot-path> <report-output> <fail-on> [github-file]\n' >&2; }

# --- Escape untrusted text for the GitHub message body (stderr). ------------
gh_msg() {
  local value="$1"
  value="${value//\%/\%25}"
  value="${value//$'\r'/%0D}"
  value="${value//$'\n'/%0A}"
  printf '%s' "$value"
}

gh_err() {
  local msg
  msg="$(gh_msg "$1")"
  printf '::error title=EU Ingredient Linter::%s\n' "$msg" >&2
}

gh_warn() {
  local msg
  msg="$(gh_msg "$1")"
  printf '::warning title=EU Ingredient Linter::%s\n' "$msg" >&2
}

# --- Return a canonicalized path (resolve symlinks + collapse dot segments) -
canonicalize() {
  python - "$1" <<'PY'
import os, sys
print(os.path.realpath(os.path.abspath(sys.argv[1])))
PY
}

# --- Reject a newline in a path-valued input early prior to any transport ----
# A path containing a hard line break cannot be transported safely through the
# line-oriented GITHUB_OUTPUT / internal status format without allowing an
# output-key or command injection. Reject it up front with an escaped
# diagnostic rather than attempting a partial/incorrect multiline transport.
reject_newlines() {
  local label="$1" value="$2"
  if [[ "${value}" == *$'\r'* || "${value}" == *$'\n'* ]]; then
    gh_err "${label} contains a carriage return or line feed; refusing a path that breaks output transport: $(printf '%q' "${value}")"
    exit 2
  fi
}

# Reject CR/LF in every path-valued input BEFORE any transport or filesystem
# access, so a newline never reaches GITHUB_OUTPUT / the internal status file
# or injects a second output key. Spaces and shell-looking text remain data.
reject_newlines "input-file" "${INPUT_FILE}"
reject_newlines "snapshot-path" "${SNAPSHOT_PATH}"
reject_newlines "report-output" "${REPORT_OUTPUT}"
reject_newlines "github-file" "${GITHUB_FILE}"

# --- Validate args ----------------------------------------------------------
case "${INPUT_FORMAT}" in
  csv|tsv|json) ;;
  *) gh_err "unsupported input-format \"${INPUT_FORMAT}\" (expected csv|tsv|json)"; usage_line; exit 2 ;;
esac

case "${FAIL_ON}" in
  findings|invalid-input) ;;
  *) gh_err "unsupported fail-on \"${FAIL_ON}\" (expected findings|invalid-input)"; usage_line; exit 2 ;;
esac

if [[ ! -f "${INPUT_FILE}" ]]; then
  gh_err "input-file not found: ${INPUT_FILE}"
  exit 2
fi

if [[ ! -e "${SNAPSHOT_PATH}" ]]; then
  gh_err "snapshot-path not found: ${SNAPSHOT_PATH}"
  exit 2
fi

# --- Resolve / fully verify the snapshot ------------------------------------

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

SNAPSHOT_PY="${EU_REP_ROOT:-/opt/eu-reg-tooling}/data/scripts/snapshot.py"
if [[ ! -f "${SNAPSHOT_PY}" ]]; then
  gh_err "snapshot tooling not found at ${SNAPSHOT_PY}"
  exit 2
fi

SNAPSHOT_SQLITE=""
if [[ -f "${SNAPSHOT_PATH}" ]]; then
  # A single file: a snapshot bundle .zip. Verify it (hash + purity + full
  # tree-verification contract), then import into a fresh temp directory.
  SNAPSHOT_IMPORT_DIR="${WORKDIR}/snapshot-import"
  if ! python "${SNAPSHOT_PY}" verify "${SNAPSHOT_PATH}" >/dev/null 2>&1; then
    gh_err "snapshot bundle failed verification: ${SNAPSHOT_PATH}"
    exit 2
  fi
  if ! python "${SNAPSHOT_PY}" import "${SNAPSHOT_PATH}" --destination "${SNAPSHOT_IMPORT_DIR}" >/dev/null 2>&1; then
    gh_err "snapshot bundle import failed: ${SNAPSHOT_PATH}"
    exit 2
  fi
  SNAPSHOT_SQLITE="${SNAPSHOT_IMPORT_DIR}/data/processed/eu_reg_lists.sqlite"
else
  # A directory snapshot: run the SAME full tree-verification contract that a
  # verified bundle uses (source hashes, format parity, normalized content hash,
  # counts, no symlinks). This rejects missing/tampered/empty/identity-only
  # snapshots; it never relies on a nonempty SQLite file size. The directory is
  # verified in place and never mutated.
  SNAPSHOT_DIR="${SNAPSHOT_PATH}"
  if ! python "${SNAPSHOT_PY}" verify-tree "${SNAPSHOT_DIR}" >/dev/null 2>&1; then
    gh_err "snapshot directory failed full verification: ${SNAPSHOT_DIR}"
    exit 2
  fi
  SNAPSHOT_SQLITE="${SNAPSHOT_DIR}/data/processed/eu_reg_lists.sqlite"
fi

# The verifier guarantees a nonempty, parity-consistent registry; still guard
# the resolved SQLite exists (defensive, not a coverage claim).
if [[ ! -s "${SNAPSHOT_SQLITE}" ]]; then
  gh_err "refusing empty registry: ${SNAPSHOT_SQLITE}"
  exit 2
fi

# --- Locate the installed CLI -----------------------------------------------
CLI="$(command -v eu-ingredient-linter)" || {
  gh_err "eu-ingredient-linter not on PATH; install packages/eu-ingredient-linter"
  exit 2
}

# --- Run the check (single shared evaluation, fresh per-invocation report) ---
# Emit GitHub annotations (--format github) to stdout AND write the cited JSON
# report atomically to a FRESH per-invocation staging path so a prior report can
# never satisfy THIS run's success contract. After the CLI returns we validate
# the staged report structure; only if it is a well-formed current report do we
# promote it to the caller's REPORT_OUTPUT and record report-valid/path.
set +e
STAGING_DIR="$(mktemp -d)"
STAGING_REPORT="${STAGING_DIR}/eu-ingredient-report.json"
trap 'rm -rf "${WORKDIR}" "${STAGING_DIR}"' EXIT
"${CLI}" \
  --registry "${SNAPSHOT_SQLITE}" \
  --input "${INPUT_FILE}" \
  --input-format "${INPUT_FORMAT}" \
  --format github \
  --github-file "${GITHUB_FILE}" \
  --output "${STAGING_REPORT}"
cli_rc=$?
set -e

# Determine the final report path (absolute) the caller uses for upload.
FINAL_REPORT="${REPORT_OUTPUT}"
if [[ "${FINAL_REPORT}" != /* ]]; then
  FINAL_REPORT="${PWD}/${FINAL_REPORT}"
fi

# Validate that THIS invocation produced a well-formed current report. This
# distinguishes a genuine finding (valid report on exit 1) from a process crash
# or missing report (no valid report on exit 0/1). Never apply the finding policy
# on an unvalidated report.
report_valid=0
if "${CLI}" --registry "${SNAPSHOT_SQLITE}" --check-report "${STAGING_REPORT}" >/dev/null 2>&1; then
  report_valid=1
else
  "${CLI}" --registry "${SNAPSHOT_SQLITE}" --check-report "${STAGING_REPORT}" >&2 || true
fi

# --- Exit policy ------------------------------------------------------------
# Operational failures always fail (exit 2):
#   * no valid current report (missing/malformed/empty), even on CLI exit 0/1
#   * invalid/parseable input (exit 2)
#   * a report that could not be written (exit 2)
#   * an unexpected CLI execution failure (non-0/1/2) — never masked.
if [[ ${report_valid} -ne 1 ]]; then
  gh_err "no valid current report produced by this invocation (exit ${cli_rc}); not applying finding policy"
  exit 2
fi

if [[ ${cli_rc} -eq 2 ]]; then
  gh_err "invalid input or report-write failure; no reliable report"
  exit 2
fi
if [[ ${cli_rc} -ne 0 && ${cli_rc} -ne 1 ]]; then
  gh_err "eu-ingredient-linter exited unexpectedly with code ${cli_rc}"
  exit 2
fi

# Only now (report proven valid + current) promote it to the caller's stable
# REPORT_OUTPUT. We never overwrite an existing file unless this run produced a
# valid report; and we never delete arbitrary user input/snapshot paths.
#
# Protection against destination/input collisions: the final report destination
# must not equal (canonically) the input file, the supplied snapshot bundle, or
# fall inside a supplied snapshot tree. An output that overlaps the input or
# snapshot would destroy the very data being read. We also reject a destination
# that is an existing directory (an invalid report destination).
#
# Publication is ATOMIC: we write to a temporary file on the destination
# filesystem, then rename into place only after a successful durable write. A
# prior destination is only replaced once the new file is in place; if anything
# fails the previous destination (if any) is preserved and the run exits 2.
# snapshot_canon is the canonical snapshot path; the checks below distinguish a
# bundle file from a directory tree (in which every member is protected).
snapshot_canon="$(canonicalize "${SNAPSHOT_PATH}")"
input_canon="$(canonicalize "${INPUT_FILE}")"
final_canon="$(canonicalize "${FINAL_REPORT}")"

overlap=0
protected_desc=""
if [[ "${final_canon}" == "${input_canon}" ]]; then
  overlap=1
  protected_desc="the input file (${input_canon})"
elif [[ -f "${SNAPSHOT_PATH}" && "${final_canon}" == "${snapshot_canon}" ]]; then
  overlap=1
  protected_desc="the supplied snapshot bundle (${snapshot_canon})"
elif [[ -d "${SNAPSHOT_PATH}" && "${final_canon}" == "${snapshot_canon}"* ]]; then
  # A destination inside the snapshot tree (including the tree root itself).
  overlap=1
  protected_desc="the supplied snapshot tree (${snapshot_canon})"
fi
if [[ "${overlap}" -eq 1 ]]; then
  gh_err "report-output overlaps ${protected_desc}; refusing to overwrite protected data"
  exit 2
fi

# Reject an existing directory / non-regular destination up front.
if [[ -e "${FINAL_REPORT}" && ! -f "${FINAL_REPORT}" ]]; then
  gh_err "report-output is an existing directory or non-regular file: ${FINAL_REPORT}"
  exit 2
fi

# Atomic publication: write to a temp file on the destination filesystem, then
# rename over the destination only after a successful durable write. The prior
# destination is never deleted before the replacement succeeds. If the optional
# destination is inside a directory, make it first.
DEST_DIR="$(dirname "${FINAL_REPORT}")"
if [[ ! -d "${DEST_DIR}" ]]; then
  gh_err "report-output parent directory does not exist: ${DEST_DIR}"
  exit 2
fi
TMP_REPORT="${DEST_DIR}/.eu-lint-report-${RANDOM}-$$.tmp"
if ! cp "${STAGING_REPORT}" "${TMP_REPORT}"; then
  rm -f "${TMP_REPORT}"
  gh_err "failed to stage report for atomic publication: ${DEST_DIR}"
  exit 2
fi
if ! mv -f "${TMP_REPORT}" "${FINAL_REPORT}"; then
  rm -f "${TMP_REPORT}"
  gh_err "failed to publish report: ${FINAL_REPORT}"
  exit 2
fi

# Record a machine-readable status in the run-owned temp dir so the host
# composite step can surface report-valid/report-path. The path must be visible
# to the host; we place it under the mounted consumer workspace.
STATUS="${RUN_STATUS_FILE:-}"
if [[ -n "${STATUS}" ]]; then
  mkdir -p "$(dirname "${STATUS}")"
  printf 'report-valid=%s\nreport-path=%s\n' "${report_valid}" "${FINAL_REPORT}" > "${STATUS}"
fi

if [[ "${FAIL_ON}" == "findings" && ${cli_rc} -eq 1 ]]; then
  # Findings are annotated + a valid current report exists; keep the step failing
  # so the workflow can gate on it.
  exit 1
fi

# ``findings`` policy with no finding, or ``invalid-input`` policy: a finding is
# NOT an operational failure and must not fail the step. A valid report was
# written either way. Exit 0 is never a compliance determination.
exit 0
