# Architecture and maintenance

Regulatory information, not legal advice.

This describes the cosmetics-checker local-preview implementation, now carried forward from the initial Ing Checker import. The short entry point is the root README; package READMEs contain interface details.

## Data flow

```text
Official exports / retained legal document
    -> staged import + identity reconciliation
    -> verified SQLite + JSONL + metadata snapshot
    -> eu-reg-lists registry / shared assessments
    -> eu-ingredient-linter label and formula reports
    -> CLI, FastAPI, browser playground, scanner
```

The scanner adds an input step: a barcode retrieves a community product record, or local OCR extracts an editable draft. The user reviews the text and explicitly submits it to the same checking engine. It does not infer concentrations from ingredient order.

## Packages and boundaries

- `packages/eu-reg-lists`: dataclass models, exact identity/alias resolution, direct and group-linked rules, coverage and the entry-70 evaluator. SQLite and JSONL are alternative representations of the same normalized snapshot.
- `packages/eu-ingredient-linter`: tokenization, label/batch reports, structured CSV/TSV/JSON inputs, formula evaluation and text/JSON/CSV/HTML/GitHub reporters. The CLI uses standard-library argparse. Reporters present assessments; they do not reinterpret legislation.
- `packages/eu-reg-api`: FastAPI/Pydantic request and response contracts, Uvicorn service, OpenAPI, static label and formula interfaces. It reads the loaded snapshot; requests do not refresh data.
- `packages/scan-to-check`: a separate FastAPI service calling the shared libraries directly. It is not an HTTP client of `eu-reg-api`. It owns the product-provider adapter and serves plain HTML/JavaScript, native camera barcode detection where available, Tesseract OCR and the PWA shell.
- `eu_lint.py` and `app.py`: retained prototype and Streamlit demo. Their curated seed is not the authoritative production dataset. Food examples there do not make the scanner a food checker.

This is a small Python monorepo, not a distributed service architecture. Two server entry points serve different audiences; both use the same libraries and snapshot. No React/Next.js, pandas, DuckDB or Typer migration is needed merely because those appeared in the original proposal.

## Identity and regulatory evidence

An identity can hold several rules. Names, aliases and CAS/EC/CI identifiers are resolved explicitly; ambiguity is unresolved or quarantined rather than silently assigned. Group records retain their source identity and link to named members; nano/form distinctions remain separate.

CosIng Annex II–VI records preserve source conditions, exceptions, official reference numbers and physical row locators. Conditional allowance is not unconditional approval. A source record's headline status is distinct from the contextual assessment shown to the user.

Formula evaluation uses Decimal, explicit units, concentration basis, product context and, for date-dependent mappings, an assessment date. Unsupported interpretations remain manual review or missing context. Known warnings and linked rules survive even when a numeric comparison is possible. Entry 70 is a bounded source-backed addition, not full Annex XVII coverage; its compatible source mapping must be present in the loaded snapshot before evaluation is enabled.

Snapshot build time (`data_updated_at`), source publication/update dates and retrieval time have separate meanings. Historical snapshots must never acquire a new freshness claim merely by being reopened.

## Import, refresh and distribution

`data/scripts/fetch.py` stages downloads and records attempts. The normalizer parses the actual export schema, reconciles identities and preserves accepted data when a replacement fails. ECHA acquisition remains disabled under the current release policy.

`snapshot.py` verifies file hashes, provenance, normalized content identity and SQLite/JSONL parity. Versioned content-hash contracts keep historical bundles verifiable. Hash integrity alone does not authenticate a publisher; the bundle and expected digest still need a trusted source.

`refresh_isolated.py` restores a baseline into a run-owned tree and builds a candidate separately. No-change selects the immutable baseline. Rule removals and semantic changes are reviewable; source evidence and failure audits are retained. Local candidate preparation is implemented; durable hosted publication remains separate work.

## Browser lifecycle, OCR and PWA

The browser renders backend assessments using safe text operations. A generation/session identity guards every asynchronous continuation so old requests cannot restore results after an edit. Cancellation releases resources; identity checks protect against transports that ignore cancellation.

Tesseract.js 6.0.1, core 6.0.0 and English model assets are vendored with hashes and notices. Images are processed in the browser, then only reviewed text is checked. A narrowly scoped, version-coupled Worker adapter handles initialization failures that otherwise leak native workers. Re-test that adapter when upgrading Tesseract; it is not a generic concurrency abstraction.

The service worker caches an explicit app shell under a content-derived version. It does not cache API findings, labels, images or the OCR vendor tree. An update waits for user consent so HTML and JavaScript remain one coherent version. The PWA is not a fully offline regulatory checker.

## CLI, Actions and release kits

The CLI supports people doing batch checks and scripts consuming reports. A GitHub Action wraps that same CLI in Docker, verifies an explicit snapshot, escapes annotations, validates a current-run report and publishes it atomically. Operational failures remain failures even when the caller permits ingredient findings. A pull request is simply the occasion for checking a changed ingredient file; it is not required for ordinary app use.

`deploy/build_deployment.py` creates platform-specific kits using ordinary wheels and a pinned runtime wheelhouse. Generated installers validate artifacts and the target interpreter/platform before replacing a runtime, and roll back on failure. Launchers resolve their snapshot relative to the installed kit. Docker is used for reproducibility and container checks, not required for the everyday local browser preview. GitHub hosts source and automation; public app hosting has not been completed.

## Maintenance

1. Read `AGENTS.md` and the current product scope; preserve user edits and the accepted snapshot.
2. Reproduce a concrete problem before changing the engine. Keep legal interpretation in the shared backend.
3. For a source change, verify current official text, amendments and reuse conditions, build an isolated candidate and retain the accepted baseline.
4. Run focused regressions, then the relevant quality gate when code changes. Do not rerun the entire suite for prose-only edits.
5. Verify installed wheels outside the checkout when changing packaging or assets.
6. Distinguish local tests, user feedback, physical-device checks and hosted runs in reports.

Do not use `make clean` for casual housekeeping: its current target deletes processed regulatory data. Keep backups of accepted snapshots before changing source data. Stop only processes whose ownership is known, never all listeners on a familiar port.

Public hosting requires separate device testing, rate limiting and trusted snapshot delivery. The current release does not provide durable hosted refresh publication.
