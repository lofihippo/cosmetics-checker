> Historical README from commit `4a92aac` (October 12, 2026). Retained for project history; phase statuses and commands may be outdated. Use the root README for the current draft overview. Relative links below originally resolved from the repository root.

# Ing Checker

> Regulatory information, not legal advice. A "clean" result means **no EU ban
> or restriction was triggered** — it is **not** a safety certificate. See the
> reusable Action under `.github/actions/eu-ingredient-linter/`.

This monorepo takes the EU's published banned/restricted substance lists and
turns them into developer-facing tooling: data, API, CLI, and scanner.

Repository: [lofihippo/ing-checker](https://github.com/lofihippo/ing-checker).
The existing Python package and CLI names remain unchanged.

It complements consumer sites (like isitbannedineurope.com, which only does
one-at-a-time paste checks) by providing the machine layer underneath.

**Build order (strict):** `eu_lint.py` prototype → `eu-reg-lists` dataset →
`eu-ingredient-linter` package → `eu-reg-api` → `scan-to-check` PWA.

---

## Phase 1 — `eu-reg-lists` (data foundation)

See [`packages/eu-reg-lists/README.md`](packages/eu-reg-lists/README.md).

The normalizing pipeline ingests **official export files only** (no scraping).
It produces `data/processed/eu_reg_lists.sqlite` + `.jsonl` + `metadata.json`.

### Refresh the data
```bash
make refresh-data    # fetch official exports, then normalize
```

### Use the package
```bash
pip install -e packages/eu-reg-lists
python -c "import eu_reg_lists as erl; \
  reg = erl.load('data/processed/eu_reg_lists.sqlite'); \
  print(reg.search('25956-17-6'))"
```

**Honesty:** a recognition of a name does NOT make it a verified rule. Curated
identities carried from Phase 0 keep `determination: "unknown"` with no rules
until a rule is parsed from an official export. `count_verified_rules` may be
`0` until CosIng/ECHA exports are fetched and parsed — reported honestly in
`metadata.json`, never fabricated.


<!-- phase-2 -->
## Phase 2 — `eu-ingredient-linter` (Annex II–VI)

Check cosmetics labels against the **Annex II** (prohibited), **Annex III**
(restricted), and **Annex IV/V/VI** (conditional positive lists) snapshot.
Annex III restrictions carry their conditions (product types, concentration
limits, other restrictions, warnings), surfaced but not evaluated into a
concentration verdict. Annex IV (colourants), V (preservatives) and VI (UV
filters) are **conditional allowances** — their presence is evidence of a
source-list entry, **not** a clean/compliant result; a substance present only in
a positive list is still a **flagged** (not clean) finding. The ECHA lists
remain **missing coverage**, never a clean result. It does not implement
recommendations, accounts, payments, an API, or a PWA (deferred).

```bash
pip install -e packages/eu-reg-lists
pip install -e packages/eu-ingredient-linter

python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --label "Aqua, Benzene, TotallyFakeIngredient"
```

See [`packages/eu-ingredient-linter/README.md`](packages/eu-ingredient-linter/README.md).

**Part B — structured formulas.** The linter also accepts a structured formula
(JSON) and evaluates it against a **small, bounded set** of Annex III
restrictions with unambiguous numeric limits. It does **not** attempt general
legal-text interpretation (complex rules and the Hydroquinone Annex II/III
exception are returned as manual review). Schema, supported rules, and verdict
semantics are documented in
[`docs/formula-evaluator.md`](docs/formula-evaluator.md) and demonstrated by
`examples/formulas/demo_evaluator.py`.

**Snapshot safety.** A failed refresh no longer wipes verified coverage:
fetches are staged and only promoted to the active snapshot after a successful
import; failed attempts are logged in `data/raw/fetch_attempts.json`; the
active raw file + manifest are preserved and exported as a CI artifact. The
committed `data/processed/metadata.json` carries a `snapshot` provenance record
(per-source sha256 + acceptance) as the durable source-of-record pointer.

**Reusable Action / PR gate.** `.github/actions/eu-ingredient-linter/` is a
composite Action that checks an ingredient file against an explicitly supplied,
**fully verified** snapshot and emits GitHub annotations + a cited JSON report.
It reuses the CLI (no second evaluator), derives annotations and the saved
report from one shared evaluation, **validates a current-run report before
applying the finding policy** (a stale/absent report is never published as
current, surfaced via `report-valid`/`report-path` outputs), verifies both bundle
and directory snapshots with the full snapshot verifier, and keeps operational
failures (bad snapshot, invalid input, missing/malformed report, report-write,
unexpected CLI exit) failing under both `fail-on` policies. Inputs are passed
through step `env` as data (never interpolated into shell), and a record with both
direct and linked evidence labels each annotation's origin.
`.github/workflows/eu-ingredient-linter.yml` is a normal (non-privileged)
`pull_request` + `workflow_dispatch` gate that sets `permissions: contents: read`,
acquires a **trusted snapshot** from a caller-set `SNAPSHOT_URL` + `SNAPSHOT_SHA256`
(download → digest check → full bundle verifier, no official export fetch),
uploads the report on findings gated on `report-valid`, and **preserves the
failed job conclusion** (no `continue-on-error` + green echo). See
[`.github/actions/eu-ingredient-linter/README.md`](.github/actions/eu-ingredient-linter/README.md)
for the exact consuming-repo configuration.

---

<!-- phase-3 -->
## Phase 3 — `eu-reg-api` (local, read-only, Annex II + III)

A local FastAPI service that reuses `eu-reg-lists` (search/lookup) and
`eu-ingredient-linter` (`ingredient_report`). It is **read-only** and covers
the **Annex II + Annex III** snapshot; **deployment, public hosting,
deployment rate limiting, the scanner application, and the PWA remain
backlog**. It does not add accounts, payments, or a recommendations engine.

It also serves a **browser playground** (a self-contained HTML/CSS/JS page) at
the root URL, so you can paste a label and inspect findings + citations without
curl. This is a **developer preview**, not the scanner; it has no barcode
scanning, OCR, camera/image upload, product database, or offline caching.

```bash
pip install -e packages/eu-reg-lists
pip install -e packages/eu-ingredient-linter
pip install -e packages/eu-reg-api

EU_REG_SNAPSHOT=data/processed/eu_reg_lists.sqlite eu-reg-api
# browser playground: http://127.0.0.1:8000/
# interactive docs:    http://127.0.0.1:8000/docs
# OpenAPI:             http://127.0.0.1:8000/openapi.json
```

Endpoints: `/` (playground), `/health`, `/v1/substances?q=`,
`/v1/substances/{cas}`, `/v1/lists/{list_name}`, `/v1/check?ingredients=`.
Covered lists are `annex-ii`..`annex-vi` (Annex II banned, Annex III restricted
with conditions, Annex IV/V/VI conditional positive lists); known-but-unavailable
lists (e.g. `svhc`, `annex-xvii`) return `422 coverage_unavailable` — missing
coverage is never shown as an empty list. A `data_updated_at` is the snapshot
build time (never request time) and stays distinct from `source_updated_at` /
`retrieved_at`. Annex III restriction conditions are surfaced but not evaluated
into a concentration verdict; a conditional positive-list (Annex IV/V/VI)
presence is `FLAGGED`, never `clean`.

Grouped source entries (e.g. "Benzoic acid and its sodium salt") are kept as
their own regulatory entries and linked to named member identities by **explicit
source-backed membership** (`member_of` / `linked_rules`): a member query
surfaces the group rule as linked evidence, never merged into the member's
identity, and never assumed to satisfy every group condition. Administrative
placeholder rows ("Moved or deleted") are excluded from active findings. In the
formula evaluator, an Annex II prohibition whose source name carries a scope /
exception qualifier is returned as `scope_unresolved`/`exception_unresolved`
(not an unambiguous ban, no exit 1); Benzene remains `unambiguous` and exits 1.

See [`packages/eu-reg-api/README.md`](packages/eu-reg-api/README.md) and
[`docs/snapshot-setup.md`](docs/snapshot-setup.md) for the offline
export/verify/import that restores a verified snapshot beside a fresh checkout.

<!-- phase-4 -->
## Phase 4 (first slice) — `scan-to-check` (local product lookup)

`packages/scan-to-check/` is the first scanner milestone: a small local flow from
entered or camera-scanned barcode → product ingredient lookup (Open Beauty Facts
/ Open Food Facts read API) → editable ingredient label → the **existing**
checker → cited results. It reuses `eu_ingredient_linter.ingredient_report` (no
duplicate engine) and loads the snapshot via `eu_reg_lists.load`. Camera barcode
capture uses the native `BarcodeDetector` API (capability-detected, local
decoding, manual fallback). Photo OCR uses the vendored, **pinned** Tesseract.js
`v6.0.1` (+ `tesseract.js-core 6.0.0`, `eng` traineddata) served locally under
`/static/vendor/tesseract/`; the recognized text is an **editable draft** that
the user reviews/edits then explicitly checks — it is never silently submitted or
fuzzy-corrected, and OCR provenance is separate from product database
provenance. The OCR lifecycle is owned by a single controller (`static/ocr.js`)
that binds selection/validation/worker/job under one identity and handles real
asset-failure recovery with manual fallback + retry.

It is also an **installable PWA shell**: a root-scoped `/manifest.webmanifest`
and `/sw.js` cache only the application shell (never API/regulatory responses,
user images or labels) and never precache the ~27 MiB OCR vendor tree. Product
lookup and regulatory checking still require the online Python backend; offline,
the shell/manual editing remain usable but checks/lookups report backend
unavailability and do **not** replay cached results or fabricate findings. See
[`packages/scan-to-check/README.md`](packages/scan-to-check/README.md).

Install the package first (required — the module is not importable from the
checkout without installation), then launch:

```bash
make build                                   # installs eu-reg-lists, ...-linter, eu-reg-api, scan-to-check
EU_REG_SNAPSHOT="$PWD/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/scan-to-check
# http://127.0.0.1:8010/
```

Product identity/ingredients are community-supplied evidence with its own ODbL
attribution and retrieval time, kept separate from the regulatory snapshot
timestamp. Cosmetic conclusions are only applied to `beauty` products; food /
petfood / other are explicitly excluded at lookup AND at the `/api/check`
endpoint. Barcodes are preserved as strings (leading zeros), malformed/upstream
responses are validated, upstream display fields are rendered as safe text, and
stale lookups/checks/edits/scans are invalidated so a delayed response cannot
overwrite newer state. See
[`packages/scan-to-check/README.md`](packages/scan-to-check/README.md).

> **Snapshot provision.** `data/raw/` and `data/processed/*.sqlite` / `*.jsonl`
> are gitignored; they must be supplied to a new checkout (or regenerated with
> `make refresh-data`). `data/processed/metadata.json` is committed for
> provenance review, but a metadata hash alone does **not** reconstruct the
> dataset.

For a new checkout, use the [offline snapshot setup](docs/snapshot-setup.md):
`make snapshot-export`, transfer the bundle, then `make snapshot-verify` and
`make snapshot-import`. This preserves the exact snapshot and provenance without
fetching or rebuilding it. Imports go into a new directory and do not replace
the active dataset. It does not yet automate weekly snapshot publication.

---

## Phase 0 — `eu_lint.py` prototypeA single-file prototype that checks a pasted ingredient label against a small,
curated EU-restriction dataset. It is **not** the authoritative 1,700+
substance corpus — Phase 1 (`eu-reg-lists`) replaces it with official export
files.

### Honesty rules baked in
- A clean result means every ingredient resolved to a verified determination
  and no EU ban/restriction/condition was triggered. Not a safety
  certificate; not legal advice.
- **"Not in dataset" is NEVER "clean".** Unknown tokens are reported with
  `is_in_dataset: false`, `determination: "unknown"` and NULL
  `status`/`annex`/`regulation`. Absence of a row is never defaulted to OK.
- **Identity-only substances are NEVER "clean".** A recognised name with no
  verified EU regulation (e.g. `water`, `glycerin`) is kept in the dataset only
  to record its identity and is reported with `determination: "unknown"`, not
  as an affirmative "no restriction".
- Substance identity is separate from regulatory rules. A substance may carry
  multiple rules (e.g. both a food rule and a cosmetic rule); all are retained
  and surfaced, never silently overwritten.
- Every flag carries a `regulation` citation, `source_url`, and
  `data_updated_at`.
- Tokenizer uses exact alias matching only — no fuzzy guessing. Recognised
  names containing punctuation (e.g. `FD&C Red 40`, `1,4-dioxane`) are
  protected from being split.
- A conditional rule (`allowed_with_conditions`) prevents a `clean` result and
  produces the flag exit code, because its concentration/use condition must be
  evaluated.
- All user-facing text includes "Regulatory information, not legal advice."

### Run it

```bash
# Pasted label -> JSON
python3 eu_lint.py "water, red 40, titanium dioxide, bht" --market eu --format json

# File of labels -> JSON / CSV / table
python3 eu_lint.py examples/demo_labels/us_cereal.txt --market eu --format json
python3 eu_lint.py examples/demo_labels/us_cereal.txt --market eu --format csv
python3 eu_lint.py "water, glycerin" --market eu --format table
```

**Exit codes:** `0` no flag (no EU ban/restriction/conditional rule was
triggered, though some ingredients may be unverified), `1` a flag was
triggered (banned / restricted / conditional rule present), `2` usage error.
"Not in dataset" and identity-only (unverified) substances never set a
non-zero exit — but they are never reported as clean and are surfaced
explicitly via `n_unverified`.

### Labels
- `--market eu` (only `eu` in Phase 0)
- `--format table|json|csv`
- `--dataset path` to override the curated dataset file

### Output semantics
| Field | Meaning |
|-------|---------|
| `status` | `banned` / `restricted` / `allowed_with_conditions` / `null` (unverified) |
| `determination` | `banned`/`restricted`/`allowed_with_conditions` (verified) or `unknown` |
| `rules` | every applicable rule (a substance may carry multiple: food + cosmetic) |
| `is_in_dataset` | `true` if the name resolved to a known substance, `false` if unknown |
| `n_flagged` | count of substances triggering a ban/restriction/condition |
| `n_unverified` | not-in-dataset + identity-only (recognised but no verified EU rule) |
| `clean` | `true` only if **every** ingredient resolved AND **no** ban/restriction/condition triggered |
| `no_ingredients` | `true` if parsing yielded no tokens (empty/whitespace/`;;;`) → never clean |
| `verdict` | `CLEAN` / `FLAGGED` / `NOT-FULLY-VERIFIED` / `NO-INGREDIENTS` |

> Empty or fully-stripped input is never `clean`. If the tokenizer produces no
> ingredients (whitespace, lone separators, a parenthetical with no name),
> `clean` is `false`, `verdict` is `NO-INGREDIENTS`, and the CLI exits `2`.
> A conditional rule (`allowed_with_conditions`, e.g. Annex V preservative or
> Annex VI UV filter) prevents a `clean` result and produces the flag exit
> code, because the applicable concentration/use must still be evaluated.
> Detecting the rule does not itself establish a violation — it is flagged for
> evaluation, not auto-declared non-compliant.

---

## Quick demos

The Streamlit demo (`app.py`) is a paste-label checker. In a later phase this
powers the `scan-to-check` PWA.

```bash
pip install -r requirements-dev.txt
streamlit run app.py
```

A minimal `eu_lint.py` sample run:

```bash
$ python3 eu_lint.py "water, red 40, titanium dioxide, bht" --market eu --format json
# (see JSON above — red 40, titanium dioxide and BHT flagged; water
#  resolved as unrestricted)
```

---

## Getting started (this phase)
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_eu_lint.py
```

## Local quality gate + orchestration

Regulatory information, not legal advice.

One command runs the intended release checks and **cannot turn a failure into
success**. `make lint` previously masked every Ruff failure as "ruff not
installed"; it now distinguishes a missing tool (hard error with an install hint)
from real findings (exit non-zero, diagnostics preserved).

```bash
make quality        # ONE command: full test suite + core coverage + lint + mypy
make lint           # scoped ruff check on maintained source; FAILS on findings
make coverage       # parser/normalizer/checker core coverage gate (tools/quality_gate.py)
```

`make quality` triggers `tools/quality_gate.py check`, which runs every required
stage (full pytest suite, named-core coverage, scoped ruff lint, mypy type
check), reports each stage's exit status, and returns non-zero if ANY required
stage fails. A missing stage tool (ruff/mypy) is a hard error (exit 2), never a
successful skip. Stages that remain red because of pre-existing, documented debt
(e.g. mypy) are reported truthfully rather than hidden or relabelled optional.

`make coverage` measures line coverage over a **transparent, explicitly named**
parser/normalizer/checker denominator that includes the actual data-pipeline
writers (`data/scripts/cosing_parser.py`, `echa_parser.py`, `normalize.py`) plus
the registry/checker and linter normalization/evaluation modules. It runs the
real collector under Coverage.py and consumes the public `coverage json` report,
so required source that the tests do NOT execute still contributes its real
statement count as 0% coverage (never silently dropped from the denominator),
and a non-zero pytest result (failed tests, collection error, interruption, no
tests) always fails the gate regardless of the coverage percentage. It writes
per-file and aggregate evidence as JSON and **never rounds an under-90% result
into a pass** — if the aggregate is below the target (0.90 by default) the gate
exits non-zero and prints the exact per-file shortfall. Non-finite/out-of-range
targets and missing/unanalysable required source are rejected. Use `--out` to
save the machine-readable evidence:

```bash
.venv/bin/python tools/quality_gate.py coverage --out /tmp/coverage.json
```

The denominator is not gamed by excluding the data normalizer; the collector
tests and denominator files are recorded in the emitted JSON. Full-package
coverage is available with `--full` (recursive source discovery over the shipped
source, excluding generated build copies), so a narrowed core result is never
presented as whole-repo coverage.

The acceptance runner `examples/acceptance_demo.py` is independent of gitignored
`.local-reference` artifacts and enforces its own results: it writes generated
inputs and retained cited CLI reports to a caller-selected `--out-dir` (or a
run-owned temp dir), runs the installed-package CLI with an explicit environment
(no special `PYTHONPATH`) and outside the source checkout where appropriate, and
returns non-zero when any required timing/correctness/provenance/exit condition
is false. Country evidence uses an exact `en:united-states` string tag, and
recorded replays retain the original provider retrieval timestamp while
reporting the replay execution time separately.

### Known remaining typing debt

`make quality`'s mypy stage currently reports **37 errors across 10 maintained
source files** (mostly `union-attr`/`arg-type` on optional lookups in
`eu_ingredient_linter/formula.py`, `eu-reg-lists/registry.py` and the
`data/scripts` pipeline). This is pre-existing and intentionally outside this
bounded quality-milestone cleanup; the gate keeps the stage red and truthful
rather than hiding it. The scoped ruff lint (E4/E7/E9/F over the maintained
source) is clean. See the progress/handoff reports for the full inventory and
the next repair batch.

## Repo layout
```
eu-compliance/
├── README.md
├── LICENSE (MIT)                · LICENSES.md
├── eu_lint.py   · app.py        · requirements-dev.txt
├── AGENTS.md
├── data/
│   ├── eu_flags.json            (curated seed, Phase 0)
│   └── scripts/build_eu_flags.py
├── packages/
│   ├── eu-reg-lists/            (Phase 1: normalized data + lookup API)
│   └── eu-ingredient-linter/    (Phase 2: Annex-II label/batch linter + CLI)
├── .github/actions/eu-ingredient-linter/  (reusable CI Action + Docker runner)
├── tests/test_eu_lint.py
└── examples/demo_labels/         (us_cereal / us_sunscreen / us_lotion)
```

## Licensing
- **Code:** MIT (see `LICENSE`).
- **Data:** varies by source. CosIng is EU public-sector reuse (attribution);
  ECHA has its own terms; Open Beauty Facts is ODbL v1.0. See `LICENSES.md`.
  Phase 1 documents each upstream source in detail.
