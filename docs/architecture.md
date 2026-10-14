# Architecture and maintenance

Regulatory information, not legal advice.

This guide describes the source in the GitHub draft. The later local cosmetics-checker working tree is not synchronized by this documentation update. Do not assume that a local milestone or test result applies to this checkout.

## How a check works

```text
Official exports -> normalizer -> SQLite / JSONL / metadata
                                      |
                                 shared registry
                                      |
                               label / formula checker
                                      |
                    CLI reports, API, playground, scanner
```

The scanner supplies text to the checker. Barcode lookup retrieves a community product record; browser OCR produces an editable draft from a photo. Neither source is a regulatory determination. The user reviews the text before submitting it.

## Code map

| Location | Responsibility |
|---|---|
| `data/scripts/` | Fetching, parsing, identity reconciliation, snapshot verification and isolated refresh |
| `packages/eu-reg-lists/` | Substance/rule models, identifiers, aliases, source metadata and registry lookup |
| `packages/eu-ingredient-linter/` | Tokenization, label/batch/formula evaluation, argparse CLI and report rendering |
| `packages/eu-reg-api/` | FastAPI/Pydantic contracts, Uvicorn server, OpenAPI, label playground and formula editor |
| `packages/scan-to-check/` | Product-provider client, separate FastAPI server, camera input, OCR and PWA assets |
| `.github/actions/` and `.github/workflows/` | Optional checks for ingredient-file changes and snapshot/quality automation |
| `eu_lint.py`, `app.py` | Historical single-file prototype and Streamlit demo using curated data |

Both servers call the Python libraries. The scanner does not need to send a second HTTP request to the developer API. Their JavaScript presents results and manages input; it should not implement a separate regulatory interpreter.

## Regulatory evidence

The registry retains multiple rules for one substance and keeps explicitly linked group evidence separate from member identities. Exact identifiers and aliases are preferable to fuzzy guesses. Ambiguous identity matches must stay unresolved.

CosIng lists have different purposes: prohibition, restriction and conditional positive lists are not interchangeable. Formula checks use explicit concentration, unit, basis and product context for a bounded set of mappings. Unsupported conditions remain unresolved; a successful numeric comparison is not an overall compliance verdict.

Reports preserve regulation references, source URLs, snapshot `data_updated_at` and available/missing coverage. Build time, retrieval time and upstream publication time are different facts. A source's physical row position is also distinct from its official reference number.

The curated prototype is historical and does not establish production food coverage. This draft lacks the later local source-gated REACH entry-70 integration and ECHA release-policy guard. Source/reuse review is needed before any acquisition or distribution; do not run broad refreshes merely to start a demo.

## Snapshots and automation

Raw and processed data files are separate from the source checkout. `snapshot.py` provides export, verification and import; bundles carry hashes and provenance. A digest detects changes but does not independently authenticate the publisher. Use a trusted bundle compatible with this code, and import it into a new directory.

The staged fetch/normalize and isolated-refresh paths are intended to preserve a usable baseline when an update fails. Review changes before promotion. The refresh workflow's existence is not evidence that hosted publication is configured or working.

The reusable Action runs the CLI in a container against an explicit snapshot and produces annotations plus a report. A pull request can trigger that check when an ingredient file changes. GitHub source hosting, GitHub automation and public app hosting are three different things; no public service is provided by this repository alone.

## Browser, OCR and PWA

The frontends use plain HTML/CSS/JavaScript. Request generation guards prevent stale responses from restoring results after an edit. Camera and OCR work use session ownership and cleanup rather than relying on cancellation alone.

Tesseract assets are vendored with recorded versions, hashes and notices. A version-specific Worker compatibility adapter handles an initialization-failure case. Preserve its tests and reassess it when upgrading the dependency.

The service worker uses a content-derived shell version, explicit cache boundaries and user-consented updates. It does not turn backend checks, product lookup or OCR into a fully offline application. Physical-device installation and camera behavior require separate acceptance.

## Working on this draft

Read `AGENTS.md` and package instructions first. Reproduce a concrete issue, preserve accepted snapshot bytes, and keep shared interpretation in the Python engine. Run relevant tests after code changes; test installed assets outside the checkout when packaging changes.

Some historical package documentation records earlier milestones. Resolve conflicting status statements against the code and loaded snapshot, not the order in which the notes were appended. The original root README is preserved under `docs/history/` rather than deleted.

Avoid `make clean` as routine housekeeping: it removes processed data. Stop only owned preview processes, not every listener on a port. Do not commit private notes, downloaded exports or local snapshot bundles merely to make a fresh clone runnable.

Before a release, reconcile the newer local application work with this repository, run its quality gate on the intended platform, and document the exact supported snapshot. Hosting, rate limits, device acceptance and ongoing source updates then need their own evidence.
