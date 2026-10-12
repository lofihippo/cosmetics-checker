# eu-reg-api — local, read-only EU regulatory substance API (Phase 3)

> Regulatory information, not legal advice. A result is a determination about
> whether an ingredient is present in the **Annex II** snapshot; it is **not**
> a safety or compliance certificate. Missing coverage (ECHA lists) is
> reported explicitly and is never presented as complete coverage.

A local read-only REST API over the existing **Annex II–VI** snapshot. It
reuses [`eu-reg-lists`](../eu-reg-lists) (search/lookup) and
[`eu-ingredient-linter`](../eu-ingredient-linter) (`ingredient_report`) — it
does **not** re-derive matching, tokenization, regulatory classification, or
reconciliation. It covers **Annex II (prohibited), Annex III (restricted), and
Annex IV/V/VI (conditional positive lists)**; ECHA remains missing coverage.

## Install & launch

Install the three local packages (the API depends on the other two):

```bash
pip install -e packages/eu-reg-lists
pip install -e packages/eu-ingredient-linter
pip install -e packages/eu-reg-api
```

Launch the local server, pointing it at the **real** processed snapshot:

```bash
EU_REG_SNAPSHOT=data/processed/eu_reg_lists.sqlite \
  eu-reg-api
# or, with an env override:
EU_REG_PORT=8000 EU_REG_SNAPSHOT=data/processed/eu_reg_lists.sqlite \
  python -m eu_reg_api.main
```

The **browser playground** is served by the API at the root URL:

```bash
open http://127.0.0.1:8000/
```

It is a single self-contained HTML/CSS/JS page that talks to the same-origin
`/health` and `/v1/check` endpoints; no separate frontend build is needed. It
shows the loaded snapshot and coverage, lets you paste a label and run the
checker, and expands source/citation details per finding. Interactive API docs
remain at `/docs` and `/redoc`.

The preview screenshots below are captured from the live app (see
`.local-reference/playground-*.png` in a local checkout): a flagged label
(`Aqua, Benzene, TotallyFake123`) with expanded citations, and an
unverified label (`Aqua, Glycerin`) that is correctly shown as not-fully-verified
and never clean.

The snapshot is loaded **once at startup** through `eu_reg_lists.load`. If the
path is missing or contains no substances, startup **fails loudly** — the
curated Phase-0 seed or an empty registry is never silently substituted.

Each `FastAPI` app owns its **own** snapshot state (on `app.state.snapshot`,
resolved per request via a dependency); it is **not** stored in a module
global, so two apps built from different registries remain fully isolated.

### Snapshot for a new checkout

The gitignored regulatory data must be supplied to a fresh checkout. See
[`docs/snapshot-setup.md`](../../docs/snapshot-setup.md) for the offline
export/verify/import commands that restore a verified Annex-II snapshot beside
a fresh code checkout, then set `EU_REG_SNAPSHOT` to the restored SQLite path.

### Snapshot provision note

`data/raw/` and `data/processed/*.sqlite` / `*.jsonl` are **gitignored** and
must be supplied to a new checkout (e.g. from the project's release artifacts or
by running `make refresh-data` fresh). `data/processed/metadata.json` is
committed for provenance review, but a metadata hash alone does **not**
reconstruct the dataset — the raw/processed artifacts are the source of truth.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Browser playground (self-contained HTML/CSS/JS) |
| GET | `/formula` | Browser formula editor (same-origin, reuses `POST /v1/check-formula`) |
| GET | `/health` | Service readiness + loaded snapshot identity/content_hash/coverage/counts |
| GET | `/v1/substances?q=...` | Search by name/alias/exact CAS/EC/E-number/CI (bounded; empty = no matches) |
| GET | `/v1/substances/{cas}` | Resolve a CAS identifier (envelope + `is_in_dataset`; 404 structured if unknown/ambiguous) |
| GET | `/v1/lists/{list_name}` | Bounded pagination over a known source list |
| GET | `/v1/check?ingredients=...` | Check ingredients, delegating to `ingredient_report` |
| POST | `/v1/check-formula` | Check a structured formula (JSON body), delegating to the corrected `evaluate_formula` |
| GET | `/docs`, `/redoc` | Interactive API docs |

Interactive docs served at `/docs` (Swagger UI) and `/redoc` (ReDoc). A
generated `openapi.yaml` is checked into this directory (`make openapi`
regenerates it from the live schema).

### Accepted list names for `/v1/lists/{list_name}`

The lists **covered** in this snapshot are `annex-ii` (prohibited),
`annex-iii` (restricted), and `annex-iv`, `annex-v`, `annex-vi` (conditional
positive lists). The remaining sources are **known but unavailable** — they
return `422 coverage_unavailable`, not an empty list (so missing coverage is
never mistaken for complete coverage): `svhc`, `annex-xiv`, `annex-xvii`. An
unrecognized name returns `404 unknown_list`.

## Response contract

- **`data_updated_at`** is the snapshot build/update time from the loaded
  metadata — resolved once at startup, **never request time**, and consistent
  across every ingredient result, the report envelope, and error responses.
- **`source_updated_at`** (upstream publication/update, may be `None`) and
  **`retrieved_at`** (in `raw` provenance) are kept **separate** and never
  substituted for `data_updated_at`.
- Every success response carries a `metadata` block (`data_updated_at`,
  `coverage`, `disclaimer`). Error responses are also structured:
  `{error: {code, message, detail}, disclaimer, metadata}` — including
  FastAPI request-validation errors.
- A `/v1/substances/{cas}` lookup returns a typed **envelope**: `substance`
  (with rules/provenance), `is_in_dataset: true`, and `metadata`. Unknown
  identifiers return a structured `404`; ambiguous identifiers return
  `404 ambiguous_identifier` and are **never** auto-resolved.
- Substance/rule results preserve `status`, `determination`, `is_in_dataset`,
  `resolution`, `conditions`, `annex`, `regulation`, `source_url`,
  `data_updated_at`, `source_updated_at`, `official_reference` (separate from
  `row_reference`), and source-file/hash provenance where available.
- **Annex III conditions.** Each restricted rule carries a compact
  `conditions` summary string (backward-compatible) plus a structured
  `raw.conditions_list` — an ordered list of `{field, label, value}` fragments
  for `product_types`, `max_concentration`, `other`, and `warnings`. These are
  preserved verbatim from the source and are **not** parsed into a
  concentration verdict; they require evaluation and never claim a limit was
  exceeded.

### Ingredient input limits

- `/v1/check` enforces a raw character-length limit of **10,000 chars** on
  `ingredients` (via FastAPI query validation) **before** tokenization/linting,
  so a very long single token cannot bypass the token limit. Oversized input
  returns a structured `422`.
- A separate documented limit of **200 ingredient tokens** is also enforced
  after parsing; exceeding it returns `422 too_many_ingredients`.
- Empty, whitespace-only, and tokenless input still return `422 no_ingredients`.
- `/v1/check-formula` enforces a body-size limit of **200,000 bytes** and a
  limit of **200 formula ingredients**; exceeding either returns a structured
  `422`. The raw JSON is parsed with `parse_float=Decimal` so decimal input
  precision is preserved exactly; validation is delegated to the shared
  `eu_ingredient_linter.evaluate_formula` (never a second evaluator). Invalid
  input returns `422 invalid_formula` / `invalid_json`.

### Formula editor (`/formula`)

A same-origin, developer-preview form that builds a structured formula and
submits it to `POST /v1/check-formula`. It does **not** re-implement the
evaluator — the API stays authoritative. Behavior and honesty contracts:

- **Three-state optional booleans.** `professional_use` and `ready_for_use` each
  present all three choices — not supplied (absent/null), Yes (true), No
  (false). The editor preserves exactly what was supplied and never infers
  context in the browser.
- **New/reset rows start with context not supplied.** Unit, basis, product
  context, body area and both booleans default to absent rather than a guessed
  `ready_for_use`. Explicitly loaded examples populate their documented context.
- **Partially completed rows are rejected.** A row with any supplied field but
  no ingredient name/identifier blocks submission with a clear row-specific
  error; only a wholly empty row is ignored.
- **Concentration is sent as its original decimal string**; blank is
  absent/null, never zero.
- A numeric "within supported numeric limit" is **only** a numeric comparison,
  never an OK/clean result; unresolved requirements stay prominent.
- Generated `openapi.yaml` request-body ingredient schema is inlined so every
  `$ref` resolves against the document root (no nested `#/$defs` reference).

## Curl examples

```bash
# Benzene CAS lookup: 71-43-2
curl "http://127.0.0.1:8000/v1/substances/71-43-2"

# Label: Aqua + Benzene + an unknown ingredient
curl "http://127.0.0.1:8000/v1/check?ingredients=Aqua,%20Benzene,%20TotallyFake123"

# 1,4-dioxane (punctuation-aware)
curl "http://127.0.0.1:8000/v1/check?ingredients=1,4-dioxane"

# Annex-II listing (bounded)
curl "http://127.0.0.1:8000/v1/lists/annex-ii?limit=5&offset=0"

# Annex-III listing (restricted, with conditions)
curl "http://127.0.0.1:8000/v1/lists/annex-iii?limit=5&offset=0"

# A restricted Annex III ingredient (Ammonia) -> conditions surfaced
curl "http://127.0.0.1:8000/v1/check?ingredients=Ammonia"

# Missing-coverage list (known but unavailable -> 422)
curl "http://127.0.0.1:8000/v1/lists/svhc"

# Structured formula (POST): a supported Annex III restriction, with
# unit + basis + product context so the numeric comparison is assessed.
curl -X POST "http://127.0.0.1:8000/v1/check-formula" \
  -H "Content-Type: application/json" \
  -d '{"ingredients":[{"ingredient":"Toluene","percent":20,"unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}'

# Structured formula with unresolved context (missing basis/product) -> numeric_comparison null.
curl -X POST "http://127.0.0.1:8000/v1/check-formula" \
  -H "Content-Type: application/json" \
  -d '{"ingredients":[{"ingredient":"Toluene","percent":20,"unit":"%"}]}'

# Invalid input (empty/tokenless -> 422)
curl "http://127.0.0.1:8000/v1/check?ingredients=%3B%3B%3B"

# Health / snapshot metadata
curl "http://127.0.0.1:8000/health"
```

## API agreement with the linter

`/v1/check` delegates directly to `eu_ingredient_linter.ingredient_report`, so
its `verdict`, counts, and per-ingredient results agree exactly with the CLI
and library. Verified in tests (`test_check_agrees_with_ingredient_report`).

## Known limitations

- **Annex II–VI covered; ECHA remains missing.** Annex II (prohibited),
  Annex III (restricted) and Annex IV/V/VI (conditional positive lists) are
  covered; ECHA SVHC/Annex XIV/Annex XVII remain **missing coverage**, never
  clean. The GitHub Action, additional original Phase-2 formats, and broader
  regulatory coverage remain **backlog** items.
- **Annex IV/V/VI are conditional positive lists, not a compliance engine.**
  Their presence is evidence of a source-list entry; a substance present only
  in a positive list is **flagged**, never `clean`. Absence from a positive
  list is **not** a prohibition (a raw ingredient name does not establish its
  intended function, scope, or exceptions). Conditions are preserved but not
  auto-satisfied.
- An Annex III match is a **restriction that requires evaluation**. The
  playground shows each rule's conditions (product types, concentration
  limits, other restrictions, warnings) but does **not** build a general
  concentration evaluator in this milestone; it never claims a limit was
  exceeded. The source information needed for a future structured-formula
  milestone is preserved.
- The browser playground is a **developer preview**, not the scanner
  application. It has **no** barcode scanning, OCR, camera access, image
  uploads, product-database integration, accounts, payments, recommendations,
  analytics, telemetry, or PWA/service-worker/offline-cache behavior. It does
  **not** persist labels to browser storage.
- Local, read-only: no deployment, no public hosting, no
  deployment-specific rate limiting.

## Tests

```bash
python -m pytest packages/eu-reg-api/tests/ -q
```

These use an isolated in-memory registry and FastAPI's `TestClient`; they do
not download regulatory data or modify the workspace snapshot. Coverage
includes: the substance-envelope contract, app isolation (two apps built from
different registries stay separate), pre-tokenization character-length
rejection, the token-count limit, structured error metadata, and an OpenAPI
contract check. One test (`test_requests_leave_snapshot_unchanged`) asserts
that API calls do not mutate snapshot bytes.

### Browser-playground regression tests

`packages/eu-reg-api/tests/test_playground_frontend.py` drives the real
playground with Playwright (system Chrome) against a Uvicorn app built from an
in-memory registry. It covers the request/input lifecycle (manual edits and
examples retire the in-flight request's generation so an abort-ignoring
response cannot re-render stale results; older responses cannot overwrite newer
ones; the Check button is restored on invalidation), honest health-failure
handling (HTTP 503, malformed JSON, malformed 200, network failure never show
"Ready"), and the unknown-ingredient wording (not-found never claims no
regulation applies). The race tests use a controllable `window.fetch` that
ignores `AbortSignal` and resolves promises explicitly, so correctness is
proven independently of cancellation and of sleep timing.

A second frontend suite,
[`test_formula_editor_frontend.py`](../packages/eu-reg-api/tests/test_formula_editor_frontend.py),
drives the `/formula` page the same way and covers the three-state context
handling, partially-completed-row validation, decimal-string preservation, the
request/response lifecycle (edits/add/remove/examples retiring pending requests;
A/B responses resolved out of order keep the newer result; stale rejection and
old finally callbacks do not override; network/API errors leave no stale
results), and keyboard/focus/narrow-screen layout — again using an abort-ignoring
controllable `window.fetch` so ordering is proven without sleeps.

Install and run:

```bash
pip install -e "packages/eu-reg-api[browser-test]"
make frontend-test     # runs BOTH the label-playground and formula-editor suites
```

The `frontend-test` target runs
[`test_playground_frontend.py`](../packages/eu-reg-api/tests/test_playground_frontend.py)
and
[`test_formula_editor_frontend.py`](../packages/eu-reg-api/tests/test_formula_editor_frontend.py).
To run just one:

```bash
python -m pytest packages/eu-reg-api/tests/test_playground_frontend.py -q
python -m pytest packages/eu-reg-api/tests/test_formula_editor_frontend.py -q
```

`playwright` is a **dev-only** dependency (the `browser-test` extra); it is
never a runtime dependency. Its tests drive an installed Chromium via
`channel="chrome"` (system Chrome/Chromium) and do not download a browser
binary. They do not touch the workspace snapshot.
