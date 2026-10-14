# EU Compliance Developer Tools -- Phase 1 (eu-reg-lists)
.PHONY: refresh-data test lint build clean package-test openapi snapshot-export snapshot-verify snapshot-import frontend-test quality coverage deploy-build deploy-build-consume-existing

# Python used for tooling.
# Use the active `python` interpreter by default so the SAME interpreter that
# installed the dev deps is used consistently (workflow setup-python or a local
# venv). Override with `make PY=/path/to/python`.
PY      ?= python3
PIP     ?= python3 -m pip

# --- Data pipeline -----------------------------------------------------
# fetch official exports -> normalize into processed artifacts
refresh-data:
	$(PY) data/scripts/fetch.py
	$(PY) data/scripts/normalize.py

# Offline transfer; imports only into a new directory.
SNAPSHOT ?= .local-reference/snapshots/annex-ii.zip
DEST ?= .local-reference/restored-annex-ii
snapshot-export:
	$(PY) data/scripts/snapshot.py export --root . --output "$(SNAPSHOT)"

snapshot-verify:
	$(PY) data/scripts/snapshot.py verify "$(SNAPSHOT)"

snapshot-import:
	$(PY) data/scripts/snapshot.py import "$(SNAPSHOT)" --destination "$(DEST)"

# Reproducible LOCAL release kit (local-only; never publishes/hosts).
# Builds a source-independent release kit from a verified snapshot bundle into a
# NEW release-kit directory.
#
# Two coherent modes:
#   * Default `make deploy-build` EXPORTS a fresh snapshot bundle from the live
#     checkout's accepted snapshot into `DEPLOY_BUNDLE` (refusing to overwrite an
#     existing archive) then builds the kit into a NEW `DEPLOY_DEST`.
#   * `make deploy-build-consume-existing` consumes an ALREADY-EXISTING trusted
#     bundle without exporting a new one (the default "export" step refuses to
#     overwrite it). You may also set `DEPLOY_BUNDLE` and use `deploy-build`.
#
# `DEPLOY_DEST` must be a NEW directory outside the live checkout (the builder
# refuses to overlay the live checkout, an existing path, or any destination
# inside the checkout). There is NO default destination: an empty DEPLOY_DEST
# makes both targets fail the preflight with a copyable example instead of
# silently building into the repo. Set it explicitly on every invocation.
DEPLOY_BUNDLE ?= .local-reference/deploy/annex-verified.zip
ifndef DEPLOY_DEST
DEPLOY_DEST :=
endif
deploy-build:
	@test -n "$$(command -v $(PY))" || { echo "error: $(PY) not found"; exit 2; }
	@if [ -z "$(DEPLOY_DEST)" ]; then \
	  echo "error: DEPLOY_DEST is required and must be a NEW directory OUTSIDE the"; \
	  echo "       checkout. Example: make deploy-build DEPLOY_DEST=/tmp/eu-release-kit"; \
	  exit 2; \
	fi
	@if [ -f "$(DEPLOY_BUNDLE)" ]; then \
	  echo "error: $(DEPLOY_BUNDLE) already exists; it will not be overwritten."; \
	  echo "       Use 'make deploy-build-consume-existing' (or set a fresh DEPLOY_BUNDLE)."; \
	  exit 2; \
	fi
	$(PY) data/scripts/snapshot.py export --root . --output "$(DEPLOY_BUNDLE)"
	$(PY) deploy/build_deployment.py --snapshot-bundle "$(DEPLOY_BUNDLE)" --dest "$(DEPLOY_DEST)"

deploy-build-consume-existing:
	@test -n "$$(command -v $(PY))" || { echo "error: $(PY) not found"; exit 2; }
	@if [ -z "$(DEPLOY_DEST)" ]; then \
	  echo "error: DEPLOY_DEST is required and must be a NEW directory OUTSIDE the"; \
	  echo "       checkout. Example: make deploy-build-consume-existing DEPLOY_BUNDLE=/tmp/t.zip DEPLOY_DEST=/tmp/eu-release-kit"; \
	  exit 2; \
	fi
	@test -f "$(DEPLOY_BUNDLE)" || { echo "error: existing bundle not found: $(DEPLOY_BUNDLE)"; exit 2; }
	$(PY) deploy/build_deployment.py --snapshot-bundle "$(DEPLOY_BUNDLE)" --dest "$(DEPLOY_DEST)"

# --- Testing -----------------------------------------------------------
test:
	$(PY) -m pytest tests/ packages/eu-reg-lists/tests/ packages/eu-ingredient-linter/tests/ packages/eu-reg-api/tests/ packages/scan-to-check/tests/ -q

package-test:
	$(PY) -m pytest packages/eu-reg-lists/tests/ packages/eu-ingredient-linter/tests/ packages/eu-reg-api/tests/ packages/scan-to-check/tests/ -q

# Run only the browser (Playwright) regression tests for the label playground, the
# formula editor, and the product scanner. These build real Uvicorn apps over an
# in-memory registry and drive system Chrome; they do not touch the workspace
# snapshot.
frontend-test:
	$(PY) -m pytest packages/eu-reg-api/tests/test_playground_frontend.py packages/eu-reg-api/tests/test_formula_editor_frontend.py packages/scan-to-check/tests/test_scan_frontend.py packages/scan-to-check/tests/test_pwa_shell.py -q

# --- OpenAPI -----------------------------------------------------------
# Regenerates packages/eu-reg-api/openapi.yaml from the live app schema.
openapi:
	$(PY) -c "import eu_reg_lists as erl, eu_reg_api.main as m, yaml; \
reg=erl.load('data/processed/eu_reg_lists.sqlite'); \
app=m.create_app(reg, reg.metadata); \
f=open('packages/eu-reg-api/openapi.yaml','w'); \
yaml.safe_dump(app.openapi(), f, sort_keys=False, allow_unicode=True); \
print('wrote openapi.yaml')"

# --- Quality -----------------------------------------------------------
# ruff is a required dev tool for the quality gate. The gate MUST NOT turn a
# failed check into a passing "not installed" echo: it distinguishes a missing
# tool (hard error, tells the developer to install it) from actual findings
# (exit non-zero, diagnostics preserved) and returns failure on either.
QUALITY_LINT_PATHS = packages/eu-reg-lists/eu_reg_lists packages/eu-ingredient-linter/eu_ingredient_linter packages/eu-reg-api/eu_reg_api packages/scan-to-check/scan_to_check eu_lint.py app.py data/scripts examples/acceptance_demo.py tools/quality_gate.py deploy/build_deployment.py

lint:
	@test -n "$$(command -v $(PY))" || { echo "error: $(PY) not found"; exit 2; }
	@$(PY) -c "import ruff" 2>/dev/null || { echo "error: ruff is not installed (pip install ruff)"; exit 2; }
	$(PY) -m ruff check --select E4,E7,E9,F $(QUALITY_LINT_PATHS)

# Reproducible local coverage gate for the parser/normalizer/checker core.
# Produces machine-readable per-file + aggregate evidence in a temp dir and
# never exits 0 on an under-target result.
coverage:
	$(PY) tools/quality_gate.py coverage

# One documented command that orchestrates ALL required quality stages:
# full test suite, named-core coverage, scoped lint and mypy type checks.
# Each stage reports its status; any required failure makes the command fail.
# Missing tools are hard errors, never successful skips.
# Set QUALITY_OUT to a run-owned dir so CI can upload the exact evidence path.
quality:
	@test -n "$$(command -v $(PY))" || { echo "error: $(PY) not found"; exit 2; }
	@if [ -n "$${QUALITY_OUT:-}" ]; then \
	  $(PY) tools/quality_gate.py check --out "$${QUALITY_OUT}"; \
	else \
	  $(PY) tools/quality_gate.py check; \
	fi

# --- Build -------------------------------------------------------------
build:
	$(PY) -m pip install --quiet -e packages/eu-reg-lists
	$(PY) -m pip install --quiet -e packages/eu-ingredient-linter
	$(PY) -m pip install --quiet -e packages/eu-reg-api
	$(PY) -m pip install --quiet -e packages/scan-to-check

clean:
	rm -rf data/processed/*.jsonl data/processed/*.sqlite data/processed/metadata.json
	rm -rf packages/eu-reg-lists/**/__pycache__ packages/eu-ingredient-linter/**/__pycache__ packages/eu-reg-api/**/__pycache__ tests/__pycache__ data/scripts/__pycache__ .pytest_cache
