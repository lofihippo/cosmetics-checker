# Linux and Docker

Regulatory information, not legal advice.

Docker is optional for the local app. These tools build a Linux release kit
and validate both services over loopback HTTPS. They do not provision a public
host. A verified snapshot bundle is required; no full dataset comes with a clone.
See [snapshot setup](../../docs/snapshot-setup.md) and [local kits](../README.md).

## Build a Linux kit

From the repository root, with Python 3 and a running Linux Docker daemon:

```sh
RELEASE_KIT_BUNDLE=/absolute/path/verified-snapshot.zip \
RELEASE_KIT_OUT=/tmp/cosmetics-linux-build \
  deploy/linux/build_linux_kit.sh
```

The output directory must not already exist. The build uses `python:3.14-slim`
and network access for build tooling and the pinned runtime wheels. It mounts
the source and bundle read-only and stages build inputs inside the container.
The resulting kit is at `/tmp/cosmetics-linux-build/release-kit`.

Inspect `release-manifest.json` for the actual CPython, OS and architecture.
A macOS wheelhouse cannot run on Linux, and an arm64 kit cannot be assumed to
run on amd64. Building is not fully hermetic: the base-image tag and build tools
can change; the runtime lock and retained wheel hashes record the kit inputs.

## Check both services over local HTTPS

Requires Docker, Python 3, curl and openssl. Port 18443 must be available:

```sh
RELEASE_KIT=/tmp/cosmetics-linux-build/release-kit \
  deploy/linux/validate_local_tls.sh
```

The script launches both services inside a container. Caddy shares that
container's network namespace so it can reach their loopback-only listeners.
Only the proxy port is published to the host, at `127.0.0.1:18443`. Temporary
certificates are trusted only by the script's curl calls; no system trust store
is changed. The script removes its own containers and temporary files on exit
and exits nonzero if a required check fails. Set `HTTPS_PORT` to another unused
port if needed. This is a finite validation run, not a persistent deployment.

Checks cover health, a cited Benzene finding, unavailable SVHC coverage, invalid
formula input, the PWA worker and manifest, and scanner findings. Inspect each
service's coverage rather than assuming every snapshot contains the same lists.

## Optional self-contained image

The Dockerfile builds a kit into an image. Docker `COPY` needs a path **inside**
the build context; an absolute host path cannot be supplied directly:

```sh
mkdir -p .docker-input
cp /absolute/path/verified-snapshot.zip .docker-input/snapshot.zip
docker build -f deploy/linux/Dockerfile \
  --build-arg SNAPSHOT_BUNDLE=.docker-input/snapshot.zip \
  -t cosmetics-checker:local .
docker run --rm --name cosmetics-checker-local cosmetics-checker:local
```

In a second terminal, check from inside the container:

```sh
docker exec cosmetics-checker-local /opt/release-kit/.venv/bin/python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
docker stop cosmetics-checker-local
```

The services bind to container loopback. A plain `docker run -p 8000:8000`
does **not** expose them correctly; use the shared-network proxy approach above.
The snapshot is embedded in this optional image, so retain its attribution and
check redistribution terms before sharing it. `.docker-input/` is gitignored.

## Public hosting remains separate

Use distinct API and scanner origins so the scanner's root-scoped service worker
does not collide with the playground. The worker caches the explicit shell;
it does not cache API responses, product labels, findings or the OCR vendor tree.

`Caddyfile` is a template requiring real domains and TLS configuration. It does
not implement authentication or rate limiting. No systemd units are supplied;
create and test a supervisor configuration before using systemctl commands.
Serve only the proxy publicly, keep upstream services on loopback, and mount the
verified snapshot read-only. Configure forwarded-header trust only for the actual
proxy. Do not trust client-supplied IP headers indiscriminately.

When replacing a snapshot, verify the new bundle, stop both services, switch to
the verified tree, restart both, and confirm their health hashes and coverage.
Retain the prior tree for rollback. Update the kit's manifest/install contract
by rebuilding the kit when distributing a changed snapshot.

Product lookup needs the provider network; OCR assets need the running server
unless already available. The PWA shell is not a complete offline checker.
Physical-device testing, public rate limiting, hosting and hosted snapshot
publication remain outside this local setup.
