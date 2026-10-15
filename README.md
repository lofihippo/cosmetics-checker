# Cosmetics Checker

Check cosmetic ingredient labels against a versioned EU regulatory snapshot. Paste a label, review a barcode lookup or photo-OCR draft, or supply a formula with explicit concentrations. Results show matched rules, conditions, sources and what remains unknown.

**Regulatory information, not legal advice.** A flag is not necessarily a demonstrated violation. No match is not proof of safety, and this is not a complete product compliance assessment.

## Current status

This project provides a local desktop preview. Public hosting and physical-device acceptance remain outside the current release.

The accepted local snapshot contains CosIng Annex II–VI and the supported cosmetic branches of REACH Annex XVII entry 70. It does **not** cover all REACH restrictions. ECHA exports/SVHC/Annex XIV remain excluded. Food screening is not supported by the current product flow.

The source checkout contains the application, curated seed and snapshot metadata. Full raw exports, SQLite/JSONL snapshots and private development notes are kept separately. Publishing the code does not deploy a public service.

## Run locally

From the repository root, use Python 3.11 or newer and install the four packages into a virtual environment (dependency installation needs network access or a package cache):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e packages/eu-reg-lists \
  -e packages/eu-ingredient-linter -e packages/eu-reg-api \
  -e packages/scan-to-check
```

A verified regulatory snapshot is required. Raw exports and SQLite/JSONL are gitignored; a fresh clone does not include them. Follow [snapshot setup](docs/snapshot-setup.md) to restore a trusted bundle. Set an absolute path to the restored SQLite file:

```sh
export EU_REG_SNAPSHOT="/absolute/path/to/eu_reg_lists.sqlite"
EU_REG_PORT=18000 .venv/bin/eu-reg-api
```

Open `http://127.0.0.1:18000/` for labels, `/formula` for formulas and `/docs` for the API. In a second terminal:

```sh
export EU_REG_SNAPSHOT="/absolute/path/to/eu_reg_lists.sqlite"
PORT=18010 .venv/bin/scan-to-check
```

Open `http://127.0.0.1:18010/` for barcode lookup, manual labels and editable photo OCR. Stop each service with Ctrl-C in its own terminal. Use available ports; do not terminate unrelated listeners.

An installed kit can also be started from its own launchers. Kit creation and offline installation are documented in [deploy/README.md](deploy/README.md). For Linux/Docker builds and a local HTTPS check, see the [Linux guide](deploy/linux/README.md). Docker is optional for the normal local preview. Offline installation does not make the application a complete offline checker.

## How the pieces fit

| Part | Purpose |
|---|---|
| `eu-reg-lists` | Substance identities, source rules, lookup and snapshot coverage |
| `eu-ingredient-linter` | Shared label/formula checking, CLI and cited reports |
| `eu-reg-api` | HTTP API, label playground and formula editor |
| `scan-to-check` | Product lookup, camera input, editable OCR and PWA shell |
| `data/scripts` | Official-export import, snapshot validation and isolated refresh |
| `deploy` / GitHub Actions | Local release kits and optional automation |

All interfaces reuse the Python checking libraries. OCR text and product database labels must be reviewed; neither is regulatory evidence by itself. Concentration comparisons cover a bounded set of mappings and require explicit context.

## Documentation

- [Architecture and maintenance](docs/architecture.md)
- [Product scope](docs/product-scope.md) · [Formula evaluation](docs/formula-evaluator.md)
- [Data methodology](docs/methodology.md) · [Data dictionary](docs/data-dictionary.md)
- [API](packages/eu-reg-api/README.md) · [CLI](packages/eu-ingredient-linter/README.md) · [Scanner](packages/scan-to-check/README.md)

For the complete local quality gate, install `requirements-dev.txt` in the same environment and run `make quality PY="$PWD/.venv/bin/python"`. Browser checks require system Chrome; container checks require Docker. Check workflow results separately for the hosted platform matrix.

Hosted snapshot publication and broader regulatory coverage remain deferred. No accounts, payments or recommendations engine are included.

## Licenses

Original code is [MIT](LICENSE). Data and vendored dependencies have separate attribution and reuse requirements; see [LICENSES.md](LICENSES.md). MIT does not relicense upstream data.
