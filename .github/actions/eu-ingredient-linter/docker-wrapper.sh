#!/usr/bin/env bash
#
# Docker wrapper for the EU Ingredient Linter Action.
#
# Regulatory information, not legal advice.
#
# GitHub builds a Docker *Action* from the Dockerfile's parent directory as the
# build context, but this action's Dockerfile COPYs repository-root paths
# (packages/, data/scripts/). Under that contract a Docker action cannot see its
# own package sources. This composite wrapper:
#
#   1. locates the ACTION REPOSITORY ROOT (the directory that owns packages/ and
#      data/scripts/) by walking up from this action's source directory — this is
#      the correct build context for the Dockerfile's COPY instructions;
#   2. builds the container image from that root;
#   3. bind-mounts the CONSUMING WORKSPACE at the same absolute path so the
#      snapshot / input / report paths resolve unchanged inside the container;
#   4. propagates the container exit code so a checker failure is never masked.
#
# It performs no regulatory network fetch and executes no PR-supplied code.

set -euo pipefail

ACTION_DIR="${1:?missing action source directory}"
WORKSPACE="${2:?missing consuming workspace}"
INPUT_FILE="${3:?missing input-file}"
INPUT_FORMAT="${4:-csv}"
SNAPSHOT_PATH="${5:?missing snapshot-path}"
REPORT_OUTPUT="${6:-eu-ingredient-report.json}"
FAIL_ON="${7:-findings}"

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

# --- Locate the ACTION REPOSITORY ROOT (build context). ---------------------
# The Dockerfile COPYs <root>/packages and <root>/data/scripts. Under a local
# `uses: ./.github/actions/...` the action source lives inside the workspace, so
# the root is the workspace. Under a shared `uses: owner/repo@ref` the action is
# checked out under _actions/<owner>/<repo>/<ref> and the root is that checkout.
# Walk up from ACTION_DIR until we find the package + tooling.
find_repo_root() {
  local dir="$1"
  while [[ -n "${dir}" && "${dir}" != "/" ]]; do
    if [[ -d "${dir}/packages/eu-reg-lists" \
          && -d "${dir}/packages/eu-ingredient-linter" \
          && -f "${dir}/data/scripts/snapshot.py" ]]; then
      printf '%s' "${dir}"
      return 0
    fi
    dir="$(dirname "${dir}")"
  done
  return 1
}

ACTION_REPO_ROOT="$(find_repo_root "${ACTION_DIR}" || true)"
if [[ -z "${ACTION_REPO_ROOT}" ]]; then
  gh_err "could not locate the action repository root (packages/ + data/scripts/) from ${ACTION_DIR}"
  exit 2
fi

# --- Validate the consuming workspace ---------------------------------------
if [[ ! -d "${WORKSPACE}" ]]; then
  gh_err "consuming workspace not found: ${WORKSPACE}"
  exit 2
fi

# --- Build the container image from the ACTION REPOSITORY ROOT --------------
# pip's build isolation may download setuptools/wheel, so the BUILD is not made
# network-isolated (this is not a regulatory fetch). The check itself is fully
# network-isolated below.
IMAGE_TAG="eu-ingredient-linter-action:local"
if ! docker build --pull=false \
  -f "${ACTION_DIR}/Dockerfile" \
  -t "${IMAGE_TAG}" \
  "${ACTION_REPO_ROOT}" >/dev/null 2>&1; then
  gh_err "failed to build the EU Ingredient Linter image"
  exit 2
fi

# --- Resolve REPORT_OUTPUT to an absolute path in the mounted workspace ------
if [[ "${REPORT_OUTPUT}" != /* ]]; then
  REPORT_OUTPUT="${WORKSPACE}/${REPORT_OUTPUT}"
fi

# The annotation ``file`` property must be workspace-relative for GitHub to
# attach it to the checked-out file. Derive it from the host-side workspace
# prefix; fall back to the full path if the input is outside the workspace.
GITHUB_FILE="${INPUT_FILE}"
if [[ "${INPUT_FILE}" == "${WORKSPACE}/"* ]]; then
  GITHUB_FILE="${INPUT_FILE#"${WORKSPACE}/"}"
fi

# A run-owned status file (inside the mounted workspace) carrying report-valid
# and report-path, written by the container entrypoint. The host reads it after
# the container exits and surfaces it as composite outputs.
RUN_STATUS_FILE="${WORKSPACE}/.eu-lint-run/status-${RANDOM}-$$.txt"
mkdir -p "$(dirname "${RUN_STATUS_FILE}")"

# --- Run the container ------------------------------------------------------
# Bind-mount the consuming workspace at the SAME absolute path, so absolute
# paths (and the resolved REPORT_OUTPUT) resolve unchanged inside the container.
# The container's entrypoint fully verifies the snapshot and runs the CLI once.
set +e
docker run --rm \
  --network=none \
  -v "${WORKSPACE}:${WORKSPACE}" \
  -e "EU_REP_ROOT=/opt/eu-reg-tooling" \
  -e "RUN_STATUS_FILE=${RUN_STATUS_FILE}" \
  -w "${WORKSPACE}" \
  "${IMAGE_TAG}" \
  "${INPUT_FILE}" \
  "${INPUT_FORMAT}" \
  "${SNAPSHOT_PATH}" \
  "${REPORT_OUTPUT}" \
  "${FAIL_ON}" \
  "${GITHUB_FILE}"
docker_rc=$?
set -e

# --- Surface the current-run report state as composite outputs --------------
# report-valid and report-path are only set when the container reported a valid,
# fresh current report. If the run produced none (missing/malformed input,
# snapshot failure, write failure, unexpected CLI exit), report-valid stays 0 so
# the workflow will not upload a stale/absent report.
REPORT_VALID=0
REPORT_PATH=""
if [[ -f "${RUN_STATUS_FILE}" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "${k}" ]] && continue
    case "${k}" in
      report-valid) REPORT_VALID="${v}" ;;
      report-path) REPORT_PATH="${v}" ;;
    esac
  done < "${RUN_STATUS_FILE}"
fi
# Remove the run-owned status file (it is not an input/snapshot and is owned by
# this run).
rm -f "${RUN_STATUS_FILE}"
rmdir "$(dirname "${RUN_STATUS_FILE}")" 2>/dev/null || true

# GITHUB_OUTPUT is a REQUIRED runner output sink: a failure to write it must not
# be silenced (the caller would not receive the publication state), so we write
# it without ``|| true``. The values come from the container's validated report
# state and, because the entrypoint rejects CR/LF in path inputs, never contain a
# bare newline that could inject a second output key. We defensively reject any
# CR/LF here too rather than corrupt the runner-output stream.
if [[ "${REPORT_VALID}" == *$'\r'* || "${REPORT_VALID}" == *$'\n'* \
      || "${REPORT_PATH}" == *$'\r'* || "${REPORT_PATH}" == *$'\n'* ]]; then
  gh_err "report state contains a carriage return or line feed; refusing to write runner output"
  exit 2
fi

if [[ "${REPORT_VALID}" == "1" && -n "${REPORT_PATH}" ]]; then
  printf 'report-valid=1\nreport-path=%s\n' "${REPORT_PATH}" >> "${GITHUB_OUTPUT}"
else
  printf 'report-valid=0\nreport-path=\n' >> "${GITHUB_OUTPUT}"
fi

# Propagate the container exit code: 0 = no finding (or finding under the
# invalid-input policy), 1 = finding under the findings policy, 2 = invalid
# input / snapshot / report-write / unexpected failure. Never mask a failure.
exit "${docker_rc}"
