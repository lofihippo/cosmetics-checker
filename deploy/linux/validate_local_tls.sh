#!/usr/bin/env bash
# Local HTTPS validation of the prepared proxy + both Linux origins.
#
# Regulatory information, not legal advice.
#
# This runs everything on the LOOPBACK interface and uses a TEMPORARY
# self-signed CA/cert so no OS or device certificate trust is changed. It does
# NOT publish/expose a LAN/public interface. It is the local preparation step;
# actual device/public-host acceptance stays unrun until exercised with the
# user's participation.
#
# The two services run in one container and bind to 127.0.0.1 (loopback-only, as
# the launchers are designed). The Caddy proxy container SHARES that container's
# network namespace, so it reaches both services on 127.0.0.1:8000/8010 and
# terminates TLS on 443, which is published only to the host's 127.0.0.1.
# Nothing is exposed on a LAN/public interface.
#
# Prereqs: docker (a Linux daemon), curl, openssl, and the Linux release kit at
# $RELEASE_KIT (default ./linux-out/release-kit). Run from the repo root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d /tmp/eu-proxy-val.XXXXXX)"
RELEASE_KIT="${RELEASE_KIT:-$REPO/../linux-out/release-kit}"
CA_NAME="eu-local-validate"

echo "work=$WORK"
echo "release_kit=$RELEASE_KIT"

cleanup() {
  docker rm -f eu-svc-ns eu-proxy-ns 2>/dev/null || true
}
trap cleanup EXIT

# --- 1. temporary CA + certs ------------------------------------------------
echo "[1/4] generate temporary CA + certificates (no machine trust change)"
openssl genrsa -out "$WORK/$CA_NAME.key" 2048 >/dev/null 2>&1
openssl req -x509 -new -nodes -key "$WORK/$CA_NAME.key" -sha256 -days 2 \
  -out "$WORK/$CA_NAME.crt" -subj "/CN=$CA_NAME" >/dev/null 2>&1
openssl genrsa -out "$WORK/server.key" 2048 >/dev/null 2>&1
openssl req -new -key "$WORK/server.key" -out "$WORK/server.csr" \
  -subj "/CN=api.local" >/dev/null 2>&1
cat > "$WORK/ext.cnf" <<EOF
subjectAltName=DNS:api.local,DNS:scan.local
EOF
openssl x509 -req -in "$WORK/server.csr" -CA "$WORK/$CA_NAME.crt" \
  -CAkey "$WORK/$CA_NAME.key" -CAcreateserial -out "$WORK/server.crt" \
  -days 2 -sha256 -extfile "$WORK/ext.cnf" >/dev/null 2>&1
echo "  CA: $WORK/$CA_NAME.crt"

# --- 2. services container (loopback), then Caddy shares its netns ----------
echo "[2/4] start services container + Caddy (shared loopback netns)"
docker rm -f eu-svc-ns eu-proxy-ns 2>/dev/null || true
# The -p on the SERVICES container publishes 443 (bound by Caddy later in the
# shared namespace) to the host's 127.0.0.1 loopback only. Docker requires port
# publishing on the container that owns the network namespace, so we publish
# here rather than on the Caddy --network container: process.
docker run --rm -d --name eu-svc-ns \
  -p 127.0.0.1:443:443 \
  -v "$RELEASE_KIT:/out/release-kit" -w /out/release-kit \
  python:3.14-slim bash -c './run-eu-reg-api.sh & ./run-scan-to-check.sh & sleep 600' >/dev/null

# Wait for both loopback services inside the svc container.
for i in $(seq 1 40); do
  a=$(docker exec eu-svc-ns python -c "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8000))" 2>/dev/null && echo up || echo down)
  s=$(docker exec eu-svc-ns python -c "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',8010))" 2>/dev/null && echo up || echo down)
  [ "$a" = up ] && [ "$s" = up ] && break
  sleep 1
done
echo "  api=$a scan=$s"

cat > "$WORK/Caddyfile" <<EOF
{
	admin off
}

api.local {
	tls /work/server.crt /work/server.key
	encode zstd gzip
	reverse_proxy 127.0.0.1:8000
}

scan.local {
	tls /work/server.crt /work/server.key
	encode zstd gzip
	reverse_proxy 127.0.0.1:8010
}
EOF
# Caddy shares the svc container's network namespace and publishes 443 to host loopback.
docker run --rm -d --name eu-proxy-ns \
  --network container:eu-svc-ns \
  -v "$WORK:/work" \
  caddy:2 caddy run --config /work/Caddyfile >/dev/null 2>"$WORK/proxy.log"
sleep 4
echo "  proxy log:"; tail -8 "$WORK/proxy.log"

# --- 3. exercise both origins over HTTPS (loopback, temp cert) --------------
echo "[3/4] exercise HTTPS"
CA="$WORK/$CA_NAME.crt"

api_health=$(curl -sS --resolve api.local:443:127.0.0.1 --cacert "$CA" https://api.local/health -o /tmp/api-health.json -w '%{http_code}')
api_benzene=$(curl -sS --resolve api.local:443:127.0.0.1 --cacert "$CA" 'https://api.local/v1/check?ingredients=Benzene' -o /tmp/api-benzene.json -w '%{http_code}')
api_echa=$(curl -sS --resolve api.local:443:127.0.0.1 --cacert "$CA" https://api.local/v1/lists/ECHA -o /tmp/api-echa.json -w '%{http_code}')
api_formula=$(curl -sS --resolve api.local:443:127.0.0.1 --cacert "$CA" -X POST https://api.local/v1/check-formula -H 'content-type: application/json' -d '{}' -o /tmp/api-formula.json -w '%{http_code}')
scan_sw=$(curl -sS --resolve scan.local:443:127.0.0.1 --cacert "$CA" https://scan.local/sw.js -o /tmp/scan-sw.js -w '%{http_code}')
scan_manifest=$(curl -sS --resolve scan.local:443:127.0.0.1 --cacert "$CA" https://scan.local/manifest.webmanifest -o /tmp/scan-manifest.json -w '%{http_code}')
scan_check=$(curl -sS --resolve scan.local:443:127.0.0.1 --cacert "$CA" -X POST https://scan.local/api/check -H 'content-type: application/json' -d '{"ingredients":"Benzene"}' -o /tmp/scan-check.json -w '%{http_code}')

echo "  api/health=$api_health  api/v1/check=$api_benzene  api/v1/lists/ECHA=$api_echa  api/v1/check-formula=$api_formula"
echo "  scan/sw.js=$scan_sw  scan/manifest=$scan_manifest  scan/api/check=$scan_check"

# --- 4. assertions ----------------------------------------------------------
echo "[4/4] assertions"
python3 - "$api_health" "$api_benzene" "$api_echa" "$api_formula" "$scan_sw" "$scan_manifest" "$scan_check" <<'PY'
import pathlib, sys
ah, ab, ae, af, sw, mf, sc = sys.argv[1:8]
def r(p): return pathlib.Path(p).read_text()
checks = {
    'api_health_200_content_hash': ah == '200' and 'content_hash' in r('/tmp/api-health.json'),
    'api_benzene_FLAGGED': ab == '200' and 'FLAGGED' in r('/tmp/api-benzene.json'),
    'api_echa_404_unknown_list': ae == '404' and 'unknown_list' in r('/tmp/api-echa.json'),
    'api_formula_422_invalid_shape': af == '422' and 'invalid_formula' in r('/tmp/api-formula.json'),
    'scan_sw_200_root_scoped': sw == '200' and 'CACHE_NAMESPACE' in r('/tmp/scan-sw.js'),
    'scan_manifest_200': mf == '200' and 'start_url' in r('/tmp/scan-manifest.json') and 'icons' in r('/tmp/scan-manifest.json'),
    'scan_check_FLAGGED_echa_notincl': sc == '200' and 'FLAGGED' in r('/tmp/scan-check.json') and 'not_included' in r('/tmp/scan-check.json'),
}
for k, v in checks.items():
    print(f"  {'PASS' if v else 'FAIL'} {k}")
print('ALL_PASS', all(checks.values()))
PY

clear_container() { docker rm -f eu-svc-ns eu-proxy-ns 2>/dev/null || true; }
trap clear_container EXIT
# copy back the signed-ish artifacts for the record
cp "$WORK/$CA_NAME.crt" /tmp/eu-local-ca.crt 2>/dev/null || true
echo "ca_cert=/tmp/eu-local-ca.crt"
echo "done"
