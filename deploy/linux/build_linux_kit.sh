#!/usr/bin/env bash
# Build the Linux release kit for the eu-reg services inside a container.
#
# Regulatory information, not legal advice.
#
# This builds a FRESH Linux kit (real manylinux wheels) from the verified,
# release-eligible snapshot bundle so platform-specific dependencies are the
# correct Linux wheels for the declared runtime. It does NOT publish/deploy.
#
# Usage (from the repository root):
#   RELEASE_KIT_BUNDLE=/path/to/verified-annex-bundle.zip \
#     deploy/linux/build_linux_kit.sh
#
# Required:
#   * a Docker daemon (Linux runtime) in the repo root;
#   * the verified, release-eligible snapshot bundle path in
#     RELEASE_KIT_BUNDLE (defaults to the accepted bundle used for this release).
#   * the repo source tree (it is mounted into the build container).
#
# Output: ./linux-out/release-kit (Linux target_platform, content_hash pinned).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
BUNDLE="${RELEASE_KIT_BUNDLE:-.local-reference/milestone-svhc-2026-10-12/evidence/baseline-svhc-pre.bundle.zip}"
OUT="${RELEASE_KIT_OUT:-$REPO/linux-out}"
KIT="$OUT/release-kit"

if [ ! -f "$BUNDLE" ]; then
  echo "error: snapshot bundle not found: $BUNDLE" >&2
  echo "       set RELEASE_KIT_BUNDLE to a verified, release-eligible bundle" >&2
  exit 2
fi
rm -rf "$OUT"
mkdir -p "$OUT"

# Use the script's Dockerfile to build the Linux kit. The bundle and repo are
# mounted so the builder verifies/imports the snapshot and downloads the pinned
# Linux runtime wheels INSIDE the container (never macOS wheels).
docker run --rm \
  -v "$REPO:/repo" -w /repo \
  -v "$BUNDLE:/snapshot-bundle.zip:ro" \
  -v "$OUT:/out" \
  python:3.14-slim bash -c '
set -e
python -m pip install --quiet --upgrade pip setuptools wheel
python /repo/deploy/build_deployment.py \
  --snapshot-bundle /snapshot-bundle.zip \
  --dest /out/release-kit
'

echo
echo "Linux release kit built at $KIT"
python3 - "$KIT/release-manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print("  target_platform :", m["target_platform"])
print("  interpreter     :", m["interpreter"])
print("  content_hash    :", m["snapshot"]["content_hash"])
print("  data_updated_at :", m["snapshot"]["data_updated_at"])
print("Regulatory information, not legal advice.")
PY
