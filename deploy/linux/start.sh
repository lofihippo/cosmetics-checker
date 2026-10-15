#!/usr/bin/env bash
# Container-local supervisor; both services stay on loopback.
# Regulatory information, not legal advice.
set -euo pipefail
cd "${RELEASE_KIT:-/opt/release-kit}"
./run-eu-reg-api.sh &
API_PID=$!
./run-scan-to-check.sh &
SCAN_PID=$!
cleanup() {
  kill "$API_PID" "$SCAN_PID" 2>/dev/null || true
  wait "$API_PID" "$SCAN_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Linux Bash supports wait -n. An unexpected exit of either service stops both.
set +e
wait -n "$API_PID" "$SCAN_PID"
status=$?
set -e
if [ "$status" -eq 0 ]; then status=1; fi
exit "$status"
