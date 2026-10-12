# Runner image for the EU Ingredient Linter Action.
#
# Regulatory information, not legal advice.
#
# This image installs the local eu-reg-lists + eu-ingredient-linter packages and
# the snapshot tooling so the Action can check a supplied ingredient file against
# a *verified, explicitly supplied* snapshot. It does NOT fetch any official
# export and does not bundle a registry: the snapshot must be supplied by the
# caller (see action.yml ``snapshot-path``). The entrypoint script does the
# verification and check.
#
# The packages are copied into the image so the Action runs standalone without a
# separate Python environment step.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install the local packages. They are copied before the entrypoint.
COPY packages/eu-reg-lists /opt/eu-reg-lists
COPY packages/eu-ingredient-linter /opt/eu-ingredient-linter
# Copy the snapshot tooling preserving the data/scripts layout so the snapshot
# verifier/import commands work with a correct repo-root (ROOT resolves to
# /opt/eu-reg-tooling). The eu_reg_lists package is installed above.
COPY data/scripts /opt/eu-reg-tooling/data/scripts

RUN pip install --no-cache-dir /opt/eu-reg-lists \
    && pip install --no-cache-dir /opt/eu-ingredient-linter \
    && chmod -R +x /opt/eu-reg-tooling

COPY .github/actions/eu-ingredient-linter/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV EU_REP_ROOT=/opt/eu-reg-tooling

ENTRYPOINT ["/entrypoint.sh"]
