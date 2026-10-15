#!/usr/bin/env bash
# Finite loopback HTTPS check; no public listener or system trust changes.
# Regulatory information, not legal advice.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RELEASE_KIT="${RELEASE_KIT:-$REPO/linux-out/release-kit}"
HTTPS_PORT="${HTTPS_PORT:-18443}"
for tool in docker curl openssl python3; do
  command -v "$tool" >/dev/null || { echo "error: $tool is required" >&2; exit 2; }
done
RELEASE_KIT="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$RELEASE_KIT")"
for file in run-eu-reg-api.sh run-scan-to-check.sh .venv/bin/python release-manifest.json; do
  [ -f "$RELEASE_KIT/$file" ] || { echo "error: incomplete Linux kit: $file" >&2; exit 2; }
done
python3 - "$HTTPS_PORT" <<'PY'
import sys
if not sys.argv[1].isdigit() or not 1024 <= int(sys.argv[1]) <= 65535:
    sys.exit("error: HTTPS_PORT must be an integer between 1024 and 65535")
PY
docker info >/dev/null
WORK="$(mktemp -d /tmp/cosmetics-tls.XXXXXX)"
RUN_ID="${WORK##*.}"
SVC="cosmetics-svc-$RUN_ID"
PROXY="cosmetics-proxy-$RUN_ID"
cleanup() {
  docker rm -f "$PROXY" "$SVC" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK/ca.key" \
  -out "$WORK/ca.crt" -days 2 -subj '/CN=Cosmetics local test CA' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -keyout "$WORK/server.key" \
  -out "$WORK/server.csr" -subj '/CN=api.local' >/dev/null 2>&1
printf 'subjectAltName=DNS:api.local,DNS:scan.local\n' > "$WORK/ext.cnf"
openssl x509 -req -in "$WORK/server.csr" -CA "$WORK/ca.crt" \
  -CAkey "$WORK/ca.key" -CAcreateserial -out "$WORK/server.crt" \
  -days 2 -sha256 -extfile "$WORK/ext.cnf" >/dev/null 2>&1
# The kit was built at /out/release-kit; retain that path inside the container.
docker run --rm -d --name "$SVC" -p "127.0.0.1:$HTTPS_PORT:443" \
  -v "$RELEASE_KIT:/out/release-kit:ro" \
  -v "$REPO/deploy/linux/start.sh:/start-services.sh:ro" \
  -e RELEASE_KIT=/out/release-kit python:3.14-slim \
  bash /start-services.sh >/dev/null
ready=0
for _ in $(seq 1 60); do
  if docker exec "$SVC" python -c 'import socket; [socket.create_connection(("127.0.0.1",p),1).close() for p in (8000,8010)]' 2>/dev/null; then
    ready=1; break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  docker logs "$SVC" >&2 || true
  echo 'error: services did not become ready' >&2
  exit 1
fi
cat > "$WORK/Caddyfile" <<'CADDY'
{
    admin off
    auto_https disable_redirects
}
https://api.local {
    tls /work/server.crt /work/server.key
    reverse_proxy 127.0.0.1:8000
}
https://scan.local {
    tls /work/server.crt /work/server.key
    reverse_proxy 127.0.0.1:8010
}
CADDY
docker run --rm -d --name "$PROXY" --network "container:$SVC" \
  -v "$WORK:/work:ro" caddy:2 caddy run --config /work/Caddyfile >/dev/null
fetch() {
  local host="$1" path="$2" output="$3"
  shift 3
  curl --noproxy '*' --silent --show-error --max-time 15 \
    --resolve "$host:$HTTPS_PORT:127.0.0.1" --cacert "$WORK/ca.crt" \
    "https://$host:$HTTPS_PORT$path" -o "$WORK/$output" -w '%{http_code}' "$@"
}
for _ in $(seq 1 30); do
  if [ "$(fetch api.local /health health.json 2>/dev/null || true)" = 200 ]; then break; fi
  sleep 1
done
ah=$(fetch api.local /health health.json)
ab=$(fetch api.local '/v1/check?ingredients=Benzene' benzene.json)
ae=$(fetch api.local /v1/lists/svhc echa.json)
af=$(fetch api.local /v1/check-formula formula.json -H 'content-type: application/json' -d '{}')
sw=$(fetch scan.local /sw.js sw.js)
mf=$(fetch scan.local /manifest.webmanifest manifest.json)
sc=$(fetch scan.local /api/check check.json -H 'content-type: application/json' -d '{"ingredients":"Benzene"}')
python3 - "$WORK" "$ah" "$ab" "$ae" "$af" "$sw" "$mf" "$sc" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
ah, ab, ae, af, sw, mf, sc = sys.argv[2:]
def read(name):
    return (root/name).read_text()
def doc(name):
    return json.loads(read(name))
checks = {
    "health": ah == "200" and bool(doc("health.json").get("content_hash")),
    "benzene": ab == "200" and doc("benzene.json").get("verdict") == "FLAGGED",
    "excluded_svhc": ae == "422" and "coverage_unavailable" in read("echa.json"),
    "invalid_formula": af == "422" and "invalid_formula" in read("formula.json"),
    "service_worker": sw == "200" and "CACHE_NAMESPACE" in read("sw.js"),
    "manifest": mf == "200" and bool(doc("manifest.json").get("icons")),
    "scanner": sc == "200" and "FLAGGED" in read("check.json") and "not_included" in read("check.json"),
}
for name, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'} {name}")
sys.exit(0 if all(checks.values()) else 1)
PY
echo 'Regulatory information, not legal advice.'
