# Ing Checker

Tools for screening cosmetic ingredient labels against EU regulatory data. Use a command-line report, an HTTP API, or a local browser interface with product lookup and editable photo OCR.

**Regulatory information, not legal advice.** A matched restriction may need concentration or product context before it can be evaluated. An unknown ingredient is not automatically unrestricted, and no result is a safety certificate.

## Project status

This repository contains an **early development snapshot**, initially imported on October 12, 2026. The maintainer's local `cosmetics-checker` project has progressed beyond this version. This documentation cleanup does not publish those later application changes or establish a tested release.

This draft includes CosIng Annex II–VI import/checking, bounded formula evaluation, a CLI, a FastAPI playground and a scanner/PWA shell. Actual coverage depends on the verified snapshot you load. ECHA data is not part of the accepted working dataset; the draft still contains acquisition code, so do not treat its fetch commands as approval to obtain or redistribute ECHA material. Later local REACH entry-70 and release-kit work is **not included here**.

Food examples in the historical prototype are not a validated food-screening product. Public hosting and physical-device acceptance are not complete.

## Local setup

A fresh clone does **not** contain the regulatory SQLite/JSONL or raw exports. Obtain a trusted snapshot compatible with this code and follow [snapshot setup](docs/snapshot-setup.md) to verify and restore it. Committed metadata alone is not a usable dataset.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e packages/eu-reg-lists \
  -e packages/eu-ingredient-linter -e packages/eu-reg-api \
  -e packages/scan-to-check

export EU_REG_SNAPSHOT="/absolute/path/to/restored/eu_reg_lists.sqlite"
.venv/bin/eu-reg-api
```

Open `http://127.0.0.1:8000/` for the label playground, `/formula` for formulas, or `/docs` for API documentation. Run the scanner in a second terminal using the same snapshot:

```sh
EU_REG_SNAPSHOT="/absolute/path/to/restored/eu_reg_lists.sqlite" \
  .venv/bin/scan-to-check
```

Open `http://127.0.0.1:8010/`. Review extracted or retrieved ingredients before checking. Stop each server with Ctrl-C in its own terminal. Product lookup needs network access; the PWA shell is not a full offline checker.

For a command-line check:

```sh
.venv/bin/eu-ingredient-linter --registry "$EU_REG_SNAPSHOT" \
  --label "Aqua, Benzene" --format json
```

## Documentation

- [Architecture and maintenance](docs/architecture.md)
- [CLI and report formats](packages/eu-ingredient-linter/README.md)
- [API and playground](packages/eu-reg-api/README.md)
- [Scanner, OCR and PWA](packages/scan-to-check/README.md)
- [Data methodology](docs/methodology.md) and [formula evaluation](docs/formula-evaluator.md)
- [Reusable GitHub Action](.github/actions/eu-ingredient-linter/README.md)

Development commands are in `Makefile`; tests require additional Python, Chrome and Docker tooling. No passing hosted CI or current local-project test count is claimed for this draft. The [original README](docs/history/README-initial-import.md) is retained as historical context.

## Licenses

Original code is [MIT](LICENSE). Upstream datasets and vendored dependencies have separate terms and attribution requirements; see [LICENSES.md](LICENSES.md). MIT does not relicense those materials.
