# Build a Linux kit into an image. Use the repository root as build context.
# Regulatory information, not legal advice.
# SNAPSHOT_BUNDLE is required and must name a file INSIDE that context.
# See README.md for build commands and container-loopback networking.
FROM python:3.14.7-slim-trixie

# Build-only tooling: the builder's only network step (downloading the pinned
# runtime wheels) needs pip; building the four local wheels needs setuptools/
# wheel.
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /src

# Copy only what the builder needs, preserving the checkout layout so the
# builder's ROOT detection (`parents[1]` of deploy/build_deployment.py) works
# exactly as it does in the repository. This is SOURCE only; the services are
# installed non-editably from the built wheelhouse (no editable source path).
COPY packages ./packages
COPY data/scripts ./data/scripts
COPY LICENSE LICENSES.md pyproject.toml ./
COPY deploy/build_deployment.py deploy/runtime.lock ./deploy/

# Stage the verified, release-eligible snapshot bundle.
ARG SNAPSHOT_BUNDLE
COPY ${SNAPSHOT_BUNDLE} /tmp/snapshot-bundle.zip

# Build the Linux release kit into /opt/release-kit.
RUN python deploy/build_deployment.py \
      --snapshot-bundle /tmp/snapshot-bundle.zip \
      --dest /opt/release-kit

# The services read the snapshot from the kit's own snapshot tree and bind to
# 127.0.0.1. Expose both documented ports.
EXPOSE 8000 8010

# Default: start both services on loopback. In the real host path a supervisor
# (systemd / a process manager) runs these separately; this entrypoint is for
# local container validation. The kit launchers live at /opt/release-kit.
COPY deploy/linux/start.sh /usr/local/bin/start-eu-services.sh
# Keep the installed code/snapshot root-owned, but readable by the runtime
# identity. Only its home and ordinary temporary paths need to be writable.
RUN groupadd --gid 10001 checker \
    && useradd --uid 10001 --gid checker --create-home --shell /usr/sbin/nologin checker \
    && chmod -R a+rX /opt/release-kit
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
WORKDIR /opt/release-kit
CMD ["start-eu-services.sh"]
