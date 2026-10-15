#!/usr/bin/env bash
# Build Linux wheels and an offline kit; no deployment or public listener.
# Regulatory information, not legal advice.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
: "${RELEASE_KIT_BUNDLE:?Set RELEASE_KIT_BUNDLE to a trusted snapshot ZIP}"
BUNDLE="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$RELEASE_KIT_BUNDLE")"
OUT="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "${RELEASE_KIT_OUT:-$REPO/linux-out}")"
if [ ! -f "$BUNDLE" ]; then
  echo "error: snapshot bundle not found: $BUNDLE" >&2
  exit 2
fi
if [ -e "$OUT" ]; then
  echo "error: output already exists; select a new RELEASE_KIT_OUT: $OUT" >&2
  exit 2
fi
docker info >/dev/null
mkdir -p "$OUT"
# Stage only build inputs inside the container; source and bundle mounts stay read-only.
docker run --rm \
  -v "$REPO:/repo:ro" -w /repo \
  -v "$BUNDLE:/snapshot-bundle.zip:ro" \
  -v "$OUT:/out" \
  python:3.14-slim bash -c '
set -e
python -m pip install --quiet --upgrade pip setuptools wheel
python - <<"PYBUILD"
from pathlib import Path
import shutil
r, d = Path("/repo"), Path("/tmp/source")
d.mkdir()
for name in ("packages", "data/scripts", "deploy"):
    shutil.copytree(r/name, d/name, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__"))
for name in ("LICENSE", "LICENSES.md", "pyproject.toml"):
    shutil.copy2(r/name, d/name)
PYBUILD
python /tmp/source/deploy/build_deployment.py \
  --snapshot-bundle /snapshot-bundle.zip --dest /out/release-kit
'
echo "Linux release kit built at $OUT/release-kit"
echo 'Regulatory information, not legal advice.'
