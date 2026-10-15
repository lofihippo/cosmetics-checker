# eu-ingredient-linter

Check cosmetic ingredient labels against the **Annex II (prohibited), Annex III
(restricted), and Annex IV/V/VI (conditional positive lists)** registry produced
by `eu-reg-lists`.

> **Scope:** **Annex II–VI** are covered. Annex III restrictions carry their
> conditions (product types, concentration limits, other restrictions,
> warnings). Label checks surface these conditions; formula checks evaluate only
> the explicitly supported numeric mappings with sufficient context. Annex IV (colourants), V (preservatives) and VI (UV filters) are
> **conditional positive lists**: their presence is evidence of a source-list
> entry, **not** a clean/compliant result — a substance present only in a
> positive list is still a **flagged** (not clean) finding. The ECHA lists
> (SVHC, Annex XIV, Annex XVII) are **deliberately not included** in this
> release (acquisition deferred/disabled); they are reported as
> NOT-included. Their absence is a **coverage gap / scope exclusion, never a
> clean result**.

## Honesty rules (project policy)

- A `determination` of `"unknown"` — whether the token is *not in the dataset*
  or is a *recognised-but-unverified* identity — is **never an affirmative
  "OK"**.
- Every verified rule carries a `regulation`, `source_url`, `source_updated_at`
  and an explicit `resolution`.
- Official **Reference Numbers** (Annex II entry numbers) are preserved
  **separately** from file row positions (`row_reference`), so a citation is
  stable even when physical file layout changes.
- "Regulatory information, not legal advice."

Source-gated REACH Annex XVII entry 70 is also supported when present in the
loaded snapshot; this is not full REACH coverage. See [formula evaluation](../../docs/formula-evaluator.md).

## Install / run

Run commands from the repository root in the virtual environment described in
the [root README](../../README.md). Supply a [verified snapshot](../../docs/snapshot-setup.md); the database is not included in a fresh clone.

```bash
python -m pip install -e packages/eu-reg-lists
python -m pip install -e packages/eu-ingredient-linter
```

### CLI (single label)

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --label "Aqua, Benzene, Benzidine, TotallyFakeIngredient"
```

Prints the loaded snapshot coverage and a per-ingredient verdict
with official Reference Number and row position.

### CLI (batch file — one label per line)

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --batch labels.txt
```

`--json` emits raw JSON instead of the human-readable summary.

### CLI (structured formula JSON)

A structured formula (ingredient + optional concentration + explicit
unit/basis + product context) is evaluated against a **small, bounded set** of
Annex III restrictions that have unambiguous numeric limits. Provide exactly one
of `--label`, `--batch`, `--formula` or `--input`:

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --formula examples/formulas/rust-inhibitor.json
```

### CLI (structured CSV / TSV input)

A single structured file represents ONE formula: one ingredient record per row,
with a required `ingredient` column and optional `percent`, `unit`, `basis`,
`product_type`, `body_area`, `professional_use`, `ready_for_use` and
`assessment_date` columns:

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.tsv --input-format tsv
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/rust-inhibitor.json --input-format json
```

Accepted file schema (`ingredient.csv` / `ingredient.tsv`):

| Column | Required | Notes |
| --- | --- | --- |
| `ingredient` | yes | Exact name or identifier (exact/explicit resolution only). |
| `percent` | no | Decimal concentration, kept as a string until shared `Decimal` validation. **Blank = absent**, never 0. |
| `unit` | no | Concentration unit (`%` supported). Blank = absent. |
| `basis` | no | Concentration basis (`ready_for_use`, `after_mixing`, …). Blank = absent. |
| `product_type` | no | Product context. Blank = absent (never inferred). |
| `body_area` | no | Body-area context. Blank = absent. |
| `professional_use` | no | `true`/`false` (also `yes`/`no`). Blank = absent; any other nonempty text is rejected. |
| `ready_for_use` | no | `true`/`false` (also `yes`/`no`). Blank = absent; any other nonempty text is rejected. |
| `assessment_date` | no | ISO-8601 assessment date (`YYYY-MM-DD`). Blank = absent; a malformed value is rejected. Required for an effective/transition-date boundary. |

Conventions:

- Delimiter: `,` for CSV, `\t` for TSV. Files are read as UTF-8 with a UTF-8 BOM
  stripped. Decimal separator is `.`; quoted fields may contain commas and
  embedded newlines (standard CSV quoting).
- Decimal concentrations are **kept as strings** until shared `Decimal`
  validation, so `25.0000000000000001` survives parsing exactly.
- Blank optional context (including `percent`) is **absent**, never zero and
  never inferred.
- Fully blank records are ignored; a blank `ingredient` on a row that carries
  other data (a *partially completed unnamed row*) is rejected.
- Duplicate headers, unsupported columns, malformed row shapes, malformed
  booleans and malformed numeric strings are rejected with the record location.
- Duplicate **resolved** identities (a name plus its own CAS/alias) are rejected,
  never silently combined.
- Invalid input exits `2` with no traceback and no partial success report;
  invalid records are never silently skipped.

> Multi-product grouping within a single structured file is deferred (one file =
> one formula). The existing newline-label `--batch` mode remains supported for
> multiple products.

### CLI output formats (`--format text|json|csv|html|github`)

`--format` renders an existing report without reclassifying ingredients; it
never changes the evaluation or the exit status. `--json` is a preserved
shorthand for `--format json` (combining `--json` with a non-json `--format`
is a conflict).

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv \
  --format csv > report.csv
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv \
  --format html > report.html
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv \
  --format github
```

- **CSV**: a deterministic, flat row schema (see `CSV_COLUMNS` in
  `eu_ingredient_linter/reporters.py`). Each direct rule and each linked group
  rule is its own row; an ingredient with no rules gets a `no_rule` row so
  unknown/unverified states stay present. Every evidence row carries the
  regulation reference, source URL, snapshot `data_updated_at`, official
  reference, file-row locator and provenance, with source publication time and
  retrieval time kept distinct. Nested/list values are serialised as JSON in
  quoted cells so no evidence is lost. Coverage and the disclaimer are emitted
  as machine-readable rows with `report_kind="meta"`. Batch items also emit a
  per-item `item_state` row, so a saved CSV stays self-describing about invalid /
  empty items even without the process exit code.
- **HTML**: a standalone printable document with embedded CSS and no external
  runtime dependency. Shows snapshot/coverage, a summary, each ingredient's
  direct and linked evidence, unresolved requirements, citations and the
  disclaimer. Escapes user/source text and validates link schemes; preserves
  unknown and unresolved states and per-item batch validity.
- **github**: emits GitHub workflow-annotation commands (`::error`/`::warning`/
  `::notice`) for direct and linked evidence, with correct escaping so untrusted
  ingredient/source text cannot inject a new command. Severity is a presentational
  policy, not a regulatory classification; annotations are file-level (they never
  use a regulatory source `row_reference` as the user's input line). See the
  reusable Action at `.github/actions/eu-ingredient-linter/`.
- The report carries the raw submitted context (`submitted_context`) separately
  from any canonical interpretation; absent vs `false` vs `true` are preserved
  distinctly and it is never reconstructed from the rule's required context.
  Source scope/exception text (e.g. Hydroquinone's Annex II qualification) and
  rule conditions are retained by CSV/HTML.
- Unknown/unverified ingredients (`determination == "unknown"`) and numerical
  within-limit results are **never** presented as "clean/compliant". Exit `0` is
  not a compliance certificate.

The formula JSON schema:

```json
{
  "ingredients": [
    {"ingredient": "Sodium nitrite", "percent": 0.1, "unit": "%",
     "basis": "ready_for_use", "product_type": "rust_inhibitor"},
    {"ingredient": "Toluene", "percent": 25, "unit": "%",
     "basis": "ready_for_use", "product_type": "nail_product"}
  ]
}
```

- `ingredient` — an exact name or identifier (exact/explicit resolution only,
  never fuzzy). Each ingredient's resolution method is recorded.
- `percent` — optional decimal, parsed **exactly** (no binary-float round-trip).
  **Rejected** if a boolean, non-finite, malformed string (e.g. `"20%%%"`), or
  outside 0–100. Missing concentration is allowed but stays **unevaluated**.
- `unit` — the explicit concentration unit. Only a percent-by-weight basis is
  supported by the bounded evaluator; unsupported units are never converted or
  assumed.
- `basis` — the concentration basis (`ready_for_use`, `after_mixing`, …). A
  missing or mismatched basis is **not assumed** and leaves the numeric
  comparison unresolved.
- `product_type` / `body_area` / `professional_use` / `ready_for_use` — the
  formula context. A missing/unsupported/negated/contradictory product context
  leaves the numeric comparison unresolved (exact canonical matching only, no
  substring/fuzzy inference).

A numeric comparison is performed **only** when the supplied unit, concentration
basis and necessary product context are each present and match the rule's
canonical value (via an explicit, documented alias map). Unsupported or
contradictory supplied context is returned unevaluated with a clear reason;
`numeric_comparison` is `null` in those cases.

Duplicate ingredient entries, and duplicate **resolved** identities (a name plus
its own CAS or alias), are rejected — never silently combined.

**Verdicts** (returned as separate fields from rule status / context / numeric
comparison):

| verdict | meaning |
| --- | --- |
| `exceeds_supported_limit` | the supplied concentration is **above** the rule's numeric cap for a supported restriction |
| `within_supported_numeric_limit` | the supplied concentration is **at or below** the rule's numeric cap |
| `requires_context` | unit / basis / product context is missing, unsupported, or mismatched — **not evaluated** |
| `requires_manual_review` | a complex rule (e.g. multi-branch thioglycolic acid), a cross-annex exception (e.g. Hydroquinone), a recognised-but-unverified identity, or a rule with no supported mapping |
| `unknown_ingredient` | the ingredient could not be resolved — **never "clean"** |

> **Important:** `within_supported_numeric_limit` is **only a numeric
> comparison against the rule's cap**. It is NOT a "clean" / "compliant" /
> "unrestricted" result, and it is NOT a claim that the rule's warnings, pH,
> professional-use or other conditions are satisfied — those are always
> surfaced in `unresolved_requirements`. Every evaluation retains all identified
> rules (Annex II, unsupported/multiple Annex III) with their citations and
> provenance, independently of any numeric support. A numeric comparison never
> produces an overall "clean/compliant" outcome.

**Supported rules** (each bound to an explicit source reference and a
conditions fingerprint; if the source conditions change the fingerprint
invalidates the mapping and the rule is returned as `requires_manual_review`
rather than silently reusing an old interpretation):

| Ref | Substance | Limit | Required product context |
| --- | --- | --- | --- |
| 17 | Sodium nitrite | ≤ 0.2 % | Rust inhibitor |
| 49 | Selenium sulfide | ≤ 1 % | Antidandruff shampoos |
| 185 | Toluene | ≤ 25 % | Nail products |
| 48 | Silver nitrate | ≤ 4 % | Colouring eyelashes/eyebrows |
| 3 | Oxalic acid (esters/alkal. salts) | ≤ 5 % | Hair products (+ professional use) |
| 46 | 6-Methylcoumarin | ≤ 0.003 % | Oral products |

Hydroquinone (Annex III entry 14, 0.02 % after mixing) is deliberately **not**
numerically evaluated: it carries an Annex II exception and an after-mixing
basis — it is returned as `requires_manual_review` unless the full exception
logic is supported. Thioglycolic acid (entry 2a) is a multi-branch, "as
thioglycolic acid", pH + professional-use rule and is **not** interpreted
(returned `requires_manual_review`).

### Library (formula)

```python
from eu_reg_lists import load
from eu_ingredient_linter import evaluate_formula

reg = load("data/processed/eu_reg_lists.sqlite")
report = evaluate_formula({"ingredients": [
    {"ingredient": "Toluene", "percent": 25, "unit": "%",
     "basis": "ready_for_use", "product_type": "nail_product"}]}, reg)
# report["evaluations"][0]["verdict"] -> "within_supported_numeric_limit"
#                                 ["numeric_comparison"] -> the same
#                                 ["unresolved_requirements"] -> warnings, etc.
```

### Library

```python
from eu_reg_lists import load
from eu_ingredient_linter import ingredient_report, batch_report

reg = load("data/processed/eu_reg_lists.sqlite")
report = ingredient_report("Aqua, Benzene, TotallyFakeIngredient", reg)
# report["verdict"] -> "FLAGGED" | "NOT-FULLY-VERIFIED" | "NO-INGREDIENTS"
# report["coverage"] -> {available: [..], missing: [..], has_annex_ii: bool}
```

## Regenerating the data

Run `make refresh-data` (or `python data/scripts/fetch.py` then
`python data/scripts/normalize.py`). A **failed refresh no longer wipes
coverage**: fetches are staged and only promoted to the active snapshot after a
successful import; failed attempts are logged separately in
`data/raw/fetch_attempts.json`.

## License / data

Code is MIT. Data licenses vary by source (CosIng EU reuse+attribution, ECHA
its own terms). See `LICENSES.md` and `docs/data-dictionary.md`.
