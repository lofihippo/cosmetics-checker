# Public-host / Linux deployment reference (preparation only)

Regulatory information, not legal advice.

This directory is the **provider-neutral reference** for putting the two existing
services on a Linux host behind a standard reverse proxy. It is **preparation
only**: nothing here publishes, exposes a LAN/public listener, creates accounts,
buys a domain, or incurs charges. Actual public deployment and physical-device acceptance are not provided by
this reference configuration.

For local validation, see
[`validate_local_tls.sh`](validate_local_tls.sh) — it runs both services on
loopback and a Caddy reverse proxy over a **temporary CA** on the loopback
interface, so no OS/device certificate trust is changed and nothing is exposed
publicly.

## The two services and the shared snapshot

Two independent FastAPI processes read **one** verified snapshot. Inspect its
metadata and service health for the loaded coverage, content hash and timestamp:

- **`eu-reg-api`** — label checker + browser playground (`/`, `/formula`,
  `/health`, `/v1/...`, `/static/...`), default port **8000**.
- **`scan-to-check`** — barcode -> ingredients -> checker + PWA (`/`, `/sw.js`,
  `/manifest.webmanifest`, `/api/health`, `/api/product/{barcode}`,
  `/api/check`, `/static/...`), default port **8010**.

Both bind to `127.0.0.1` with no auth. ECHA is excluded/deferred; every surface
reports it as a coverage gap / not-included, **never** a clean result. Product
lookup and ingredient checks need the running services; the barcode->product
flow needs the Open Beauty Facts provider. This is **not** an offline checker.

## Topology

One reverse proxy terminates TLS and routes by **host** (two origins), keeping
the scanner's root-scoped service worker on its own origin so it does not
collide with the API playground's `/static`:

```text
                 [ public 443 ]
                        |
                  reverse proxy
                 (TLS terminate)
                   /          \
   api.EXAMPLE  (8000)   scan.EXAMPLE  (8010)
     eu-reg-api              scan-to-check
         \                      /
          one shared read-only snapshot (mounted read-only)
```

Separate `api` and `scan` hostnames is the preferred reference because the
`scan-to-check` service worker has scope `/` on its origin and intercepts
`/api/` and `/static/vendor/tesseract/`; keeping it on its own domain avoids any
path/scope overlap with `eu-reg-api`.

- `api.EXAMPLE/`, `/formula`, `/health`, `/v1/...`, `/static/...` -> `eu-reg-api`
- `scan.EXAMPLE/`, `/sw.js`, `/manifest.webmanifest`, `/api/...`, `/static/...` -> `scan-to-check`

## Why the Linux wheels must be built on Linux

The retained runtime wheels for `uvloop`, `httptools`, `pydantic-core`,
`websockets`, `PyYAML`, and `watchfiles` are platform- and CPython-version
specific. A macOS kit is not a Linux runtime; **do not** copy its `.venv` or platform
wheels to a Linux host. Build a fresh Linux kit.

`build_linux_kit.sh` builds a Linux `manylinux` release kit in a container that
matches the declared CPython/architecture target. Inspect the resulting release
manifest before installing it on another machine.

## Local validation

```bash
# Build the Linux release kit (from the verified snapshot bundle).
docker build -f deploy/linux/Dockerfile -t eu-release-linux:local .

# Exercise both origins over HTTPS with a temporary CA (loopback only).
RELEASE_KIT=/tmp/linux-out/release-kit deploy/linux/validate_local_tls.sh
```

The validation script asserts: `api /health` 200 with snapshot identity; `api
/v1/check` Benzene FLAGGED; `api /v1/lists/ECHA` 404 `unknown_list` (ECHA
missing, never clean); `api /v1/check-formula` 422 `invalid_formula` on an empty
body; `scan /sw.js` 200 (root-scoped service worker); `scan
/manifest.webmanifest` 200; `scan /api/check` FLAGGED with ECHA
`not_included`. Run these assertions against the exact kit and snapshot being deployed.

## Runbook for the Linux host

### 1. Build the Linux kit

Run `build_linux_kit.sh` (or the equivalent Dockerfile) on the Linux build host/
CI for the **declared runtime** (a CPython 3.14 Linux `manylinux` release built
on the same OS/arch as the host). Never use macOS wheels/venv.

### 2. Mount the snapshot read-only

The services read `snapshot/data/processed/eu_reg_lists.sqlite`. Mount the
`snapshot/` tree **read-only** (bind mount or a read-only volume). Both services
share it, so they stay in sync.

```bash
# runbook: read-only snapshot mount
mkdir -p /srv/eu-release/snapshot
mount --bind /srv/eu-snapshots/<version>/snapshot /srv/eu-release/snapshot
mount -o remount,ro,bind /srv/eu-release/snapshot
```

### 3. Run the services

Run each service as its own systemd unit (or a process supervisor). Both bind
to 127.0.0.1. `install.sh` recreates `.venv` from the retained, Linux-built
wheelhouse offline; it validates the manifest, retained wheel hashes, snapshot
integrity and host target, and rolls back to the prior runtime on failure.

```bash
systemctl start eu-reg-api   # binds 127.0.0.1:8000
systemctl start scan-to-check # binds 127.0.0.1:8010
```

### 4. Reverse proxy (TLS)

Use `Caddyfile` (or the equivalent nginx config) to terminate TLS and route by
host. Caddy auto-issues/renews certificates for a public domain; do **not** serve
either service port publicly. `request_body` caps payload size at the proxy.

### 5. Health / start / stop

```bash
curl -f https://api.EXAMPLE/health                 # content_hash + coverage
curl -f https://scan.EXAMPLE/api/health            # status ok + data_updated_at
systemctl start eu-reg-api scan-to-check
systemctl stop  eu-reg-api scan-to-check
```

### 6. Snapshot replacement and rollback

Both services read the same snapshot. To refresh:

1. Re-export a new bundle from a trusted checkout and verify/release-check it
   (`snapshot.py verify`; no deferred ECHA data).
2. Stage the new `snapshot/` tree next to the live one.
3. Atomically swap the bind-mount and restart both services together.
4. Verify `/health` reports the expected `content_hash` and `data_updated_at`.

Rollback: keep the prior `snapshot/` path and bind-mount it back; restart both
services. `install.sh` also rolls back the `.venv` if a reinstall fails, so a
bad runtime never leaves both services down.

### 7. Payload / rate limiting

- **Payload**: the proxy `request_body max_size` caps bodies (64 MiB API, 80 MiB
  scan) and the app also validates formula token counts. A malformed payload
  returns a structured 4xx (`invalid_formula`) with the disclaimer.
- **Rate limit**: no limiter is wired into this reference configuration. Add and
  test an appropriate application or proxy policy before public exposure,
  including the trusted-client-IP boundary below.

### 8. Trusted-proxy / client-IP boundary

Because the app sees `127.0.0.1` (the proxy) as the client, the proxy must set
`X-Forwarded-For`/`X-Real-IP` and the app must be told it is behind a trusted
proxy. On Caddy, `reverse_proxy` sets `X-Forwarded-For` automatically; do not
trust client-supplied `X-Forwarded-For` from the public client. On nginx use
`set_real_ip_from` for the proxy subnet and `real_ip_header X-Forwarded-For`.
Never bind the app to a public interface directly.

## Deployment limits

This reference does not provision a public host, complete physical-device
acceptance, configure hosted snapshot publication or implement rate limiting.
Those need their own configuration and validation before public use.

Regulatory information, not legal advice.
