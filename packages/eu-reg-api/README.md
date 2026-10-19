# eu-reg-api

Read-only HTTP API, label playground and formula editor. Both checking endpoints
use the shared Python libraries. `/health` reports the loaded snapshot and
coverage; see [product scope](../../docs/product-scope.md) for supported sources.

## Launch

Use the environment and [verified snapshot](../../docs/snapshot-setup.md)
described in the [root setup guide](../../README.md#run-locally).

```bash
EU_REG_SNAPSHOT="$PWD/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/eu-reg-api
```

Open `http://127.0.0.1:8000/`. Set `EU_REG_PORT` to change the port, or run
`python -m eu_reg_api.main` instead of the installed entry point.

The snapshot loads once at startup. Missing or empty snapshots prevent startup.
Each app instance owns its snapshot state; requests do not refresh or mutate it.
The pages use packaged HTML, CSS and JavaScript with no frontend build step.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Ingredient label playground |
| GET | `/formula` | Structured formula editor |
| GET | `/health` | Readiness, snapshot identity, counts and coverage |
| GET | `/v1/substances?q=...` | Search names, aliases and identifiers; empty query returns no matches |
| GET | `/v1/substances/{cas}` | CAS lookup with substance, `is_in_dataset` and metadata |
| GET | `/v1/lists/{list_name}` | Paginated source list |
| GET | `/v1/check?ingredients=...` | Label check |
| POST | `/v1/check-formula` | Structured formula check |
| GET | `/docs`, `/redoc` | Interactive API documentation |

`make openapi` regenerates [openapi.yaml](openapi.yaml).

List names are `annex-ii` through `annex-vi`, `svhc`, `annex-xiv` and
`annex-xvii`. Known sources absent from the snapshot return
`422 coverage_unavailable`; unrecognized names return `404 unknown_list`.
ECHA lists remain excluded. The separate entry-70 mapping does not provide a
full Annex XVII list.

## Response contract

Success responses include metadata with snapshot `data_updated_at`, coverage
and the notice. Errors use `{error: {code, message, detail}, disclaimer, metadata}`,
including request-validation errors. Unknown CAS identifiers return `404`;
ambiguous identifiers return `404 ambiguous_identifier`.

`data_updated_at` comes from the loaded snapshot, not request time. Upstream
`source_updated_at` and provenance `retrieved_at` remain separate. Findings
retain rules, conditions, regulation references, source URLs, resolution methods,
official entry numbers and file-row locators. Annex III `raw.conditions_list`
preserves the source's product types, concentration limits, other conditions
and warnings as `{field, label, value}` fragments.

For label checks, `tokens` and `n_candidates` include all parsed candidates.
`results` and `n_ingredients` count supplementary aliases of the same registry
identity within one label occurrence once. `matched_aliases` and `derived_from`
retain the additional names and parent text. Separate occurrences stay separate.

## Input limits

| Endpoint | Limit | Error |
|---|---|---|
| `/v1/check` | 10,000 characters before parsing | `422` validation error |
| `/v1/check` | 200 parsed candidates | `422 too_many_ingredients` |
| `/v1/check` | At least one parsed ingredient | `422 no_ingredients` |
| `/v1/check-formula` | 200,000 raw bytes; 200 ingredients | `422` |
| `/v1/check-formula` | Valid JSON and formula fields | `422 invalid_json` / `invalid_formula` |

Formula decimals retain their input precision. Missing context stays absent;
optional booleans distinguish not supplied, true and false. Partially filled
rows without an ingredient are rejected. See the [formula reference](../../docs/formula-evaluator.md)
for schemas and supported comparisons.

## Examples

```bash
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/v1/substances/71-43-2'
curl --get 'http://127.0.0.1:8000/v1/check' \
  --data-urlencode 'ingredients=Aqua, Parfum (Lilial), 1,4-dioxane'
curl 'http://127.0.0.1:8000/v1/lists/annex-iii?limit=5&offset=0'
curl -X POST 'http://127.0.0.1:8000/v1/check-formula' \
  -H 'Content-Type: application/json' \
  -d '{"ingredients":[{"ingredient":"Toluene","percent":"20","unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}'
```

## Interpreting results

A rule match may require further context. Conditional positive-list entries are
flagged; absence from a positive list does not itself establish a prohibition.
Unknown ingredients remain unresolved. Numeric comparisons cover supported
mappings and do not establish full product compliance.

Regulatory information, not legal advice.

## Browser and service behavior

The browser clears stale findings after edits and ignores out-of-order responses.
Labels are not persisted to browser storage. Barcode lookup, camera input and
OCR are in the separate [scanner](../scan-to-check/README.md).

HTML uses `default-src 'self'` and `frame-ancestors 'none'`, with local scripts
and no inline-script/eval allowance. Responses include
`X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
`/docs`, `/docs/oauth2-redirect` and `/redoc` are exempt from CSP for their CDN
assets and inline bootstraps. Public hosting and deployment rate limiting are
outside the current release.

## Tests

```bash
python -m pytest packages/eu-reg-api/tests/ -q
make frontend-test
```

Tests use in-memory registries. Browser tests require system Chrome and the
`browser-test` package extra; Playwright is a development dependency. The two
frontend suites cover labels and formulas, including validation, request races,
coverage, citations and narrow-screen layout.
