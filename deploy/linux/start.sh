#!/usr/bin/env bash
# Start both eu-reg services from the Linux release kit inside the container.
#
# Regulatory information, not legal advice.
#
# This is the LOCAL-CONTAINER entrypoint (reverse proxy / TLS terminate outside
# the container). It runs the two launchers the builder generated, which bind to
# 127.0.0.1 inside the container and read the kit's own snapshot tree.
#
# On a real host you would run each service as its own systemd unit and let a
# reverse proxy terminate TLS (see deploy/linux/README.md). This script is for
# container-local validation of both origins.
set -euo pipefail
KIT=/opt/release-kit
cd "$KIT"

# Start the label checker + playground (default 8000).
./run-eu-reg-api.sh &
API_PID=$!

# Start the barcode -> ingredients -> checker + PWA (default 8010).
./run-scan-to-check.sh &
SCAN_PID=$!

# Reap children on exit so the container stops cleanly.
trap 'kill $API_PID $SCAN_PID 2>/dev/null || true' EXIT INT TERM

# Report readiness once both ports respond.
for port in 8000 8010; do
  for _ in $(seq 1 60); do
    if python -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)"; then
      echo "service up on $port"
      break
    fi
    sleep 1
  done
done

wait
