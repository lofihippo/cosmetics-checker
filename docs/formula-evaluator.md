# Structured formula evaluation (bounded Annex III evaluator)

The `eu-ingredient-linter` library and CLI accept a structured formula (an
ingredient plus optional concentration and explicit context) and evaluate it
against a **small, bounded set** of real Annex III restrictions that have
unambiguous numeric limits.

This is **not** a general legal-text interpreter. It deliberately does **not**
evaluate complexes such as thioglycolic acid, "as NH3" free-base equivalents,
after-mixing limits, aggregate limits, multiple product branches, or cross-annex
exceptions — those are returned as `requires_manual_review` / `requires_context`
rather than guessed.

## Library API

```python
from eu_reg_lists import load
from eu_ingredient_linter import evaluate_formula

reg = load("data/processed/eu_reg_lists.sqlite")
report = evaluate_formula({"ingredients": [
    {"ingredient": "Toluene", "percent": 25, "unit": "%", "basis": "ready_for_use",
     "product_type": "nail_product"}]}, reg)
```

A numeric comparison is performed **only** when the concentration **unit**,
**basis** and **product context** are each present and match the rule's canonical
value (via an explicit, documented alias map — never substring/prefix/fuzzy
matching). Missing, unsupported or contradictory unit/basis/product context
leaves `numeric_comparison` null and returns `requires_context` /
`requires_manual_review`. Missing basis is never assumed.

## Input schema

```json
{
  "ingredients": [
    {
      "ingredient": "Sodium nitrite",   // required: exact name or identifier
      "percent": 0.1,                    // optional; decimal, 0-100 (exact)
      "unit": "%",                       // explicit concentration unit (required for numeric)
      "basis": "ready_for_use",          // concentration basis (required for numeric)
      "product_type": "rust_inhibitor",  // product context (required for a product-scoped rule)
      "body_area": null,                 // optional
      "professional_use": null,          // optional boolean
      "ready_for_use": null              // optional boolean
    }
  ]
}
```

Validation contract (rejected, never coerced):
- `formula` must be a JSON object with a non-empty `ingredients` list; each
  ingredient must be a JSON object.
- `ingredient` must be a non-empty name or exact identifier.
- `percent`, when present, is parsed **exactly** (no binary-float round-trip) and
  must be a finite decimal in `[0, 100]`. **Booleans, non-finite (`NaN`/`inf`),
  malformed strings (e.g. `"20%%%"`) and out-of-range values are rejected.** A
  JSON number and its exact string equivalent compare identically.
- `unit` / `basis` / `product_type` / `body_area` are optional strings.
- `professional_use` / `ready_for_use` are optional booleans.
- Duplicate ingredient entries and duplicate **resolved** identities (a name plus
  its own CAS or alias) are **rejected** — never silently combined.
- Missing concentration is allowed but stays **unevaluated**.

## Output schema

Each `evaluations[]` entry returns separate fields so a numeric comparison is
never conflated with a compliance claim, and all identified regulatory evidence
is preserved independently of numeric support:

```json
{
  "ingredient": "Sodium nitrite",
  "resolved_name": "Sodium nitrite",
  "resolution": "exact_name_or_alias",       // how it resolved (exact only)
  "is_in_dataset": true,
  "determination": "restricted",
  "rule_status": "restricted",
  "annex": "Annex III",
  "applicable_context": "Rust inhibitor",
  "numeric_comparison": "within_supported_numeric_limit",   // null if not assessed
  "submitted_value": "0.1",
  "unit": "%",
  "basis": "ready_for_use",
  "cap": "0.2%",
  "unresolved_requirements": ["..."],         // warnings/other/professional-use not evaluated
  "reasons": ["..."],
  "verdict": "within_supported_numeric_limit",
  "rules": [ ... ],                            // ALL identified rules (Annex II/III, provenance)
  "source_rule": {"reference_number": 17, "name": "...", "status": "restricted",
                  "annex": "Annex III", "regulation": "...", "source_url": "...",
                  "data_updated_at": "...", "limit": "0.2", "limit_unit": "%",
                  "basis": "ready_for_use"},
  "provenance": {"official_reference": 17, "row_reference": "...",
                 "source_file": "...", "source_sha256": "...", "retrieved_at": "...",
                 "source_url": "...", "source_updated_at": null,
                 "regulation": "...", "data_updated_at": "...",
                 "conditions_fingerprint": "...", "conditions_list": [...]}
}
```

The report carries report-level snapshot metadata and coverage:

```json
{"data_updated_at": "...", "registry_size": 2198,
 "coverage": {"available": ["cosing-annex-ii", "cosing-annex-iii"],
              "missing": ["cosing-annex-iv", "cosing-annex-v", "cosing-annex-vi",
                          "echa-annex-xiv", "echa-annex-xvii", "echa-svhc"],
              "has_annex_ii": true, "note": "..."},
 "disclaimer": "Regulatory information, not legal advice."}
```

### Verdicts

| verdict | meaning |
| --- | --- |
| `exceeds_supported_limit` | supplied concentration is **above** the rule's numeric cap |
| `within_supported_numeric_limit` | supplied concentration is **at or below** the rule's numeric cap (**numeric comparison only**) |
| `requires_context` | product type / basis / other context is missing or mismatched — **not evaluated** |
| `requires_manual_review` | complex rule, cross-annex exception, or no supported mapping |
| `unknown_ingredient` | ingredient could not be resolved — **never "clean"** |

> `within_supported_numeric_limit` is **only a numeric comparison against the
> rule's cap**. It is NOT "clean" / "compliant" / "unrestricted" and it is NOT a
> claim that the rule's warnings, pH, professional-use or other conditions are
> satisfied — those remain `unresolved_requirements` / `requires_context`.

## Supported rules

Each rule is bound to its source reference **and** a conditions fingerprint
(derived from the exact structured conditions). If the snapshot's stored
conditions change (and no longer match the canonical template), the mapping is
**invalidated** and the rule is returned as `requires_manual_review` rather than
silently reusing an old interpretation.

| Ref | Substance | Cap | Required product context | Notes |
| --- | --- | --- | --- | --- |
| 17 | Sodium nitrite | 0.2 % | Rust inhibitor | nitrosamine "other" not evaluated |
| 48 | Silver nitrate | 4 % | Colouring eyelashes/eyebrows | warnings not evaluated |
| 49 | Selenium sulfide | 1 % | Antidandruff shampoos | warnings not evaluated |
| 185 | Toluene | 25 % | Nail products | warnings not evaluated |
| 3 | Oxalic acid (esters/alkal. salts) | 5 % | Hair products | **professional use** required (unevaluated) |
| 46 | 6-Methylcoumarin | 0.003 % | Oral products | labelling thresholds not evaluated |

### Deliberately unsupported (always `requires_manual_review`)

- **Hydroquinone** (Annex III entry 14, 0.02 % after mixing): carries an
  Annex II exception and an after-mixing basis. The null case is manual review
  unless the full exception logic is supported (it is not).
- **Thioglycolic acid and its salts** (entry 2a): multi-branch product types,
  "as thioglycolic acid" basis, pH + professional-use conditions — no attempted
  general legal-text interpretation.
- Any Annex III rule without a supported mapping (returned manual review).

## CLI

```bash
python -m eu_ingredient_linter --registry data/processed/eu_reg_lists.sqlite \
  --formula examples/formulas/rust-inhibitor.json
```

`--json` emits the raw JSON report.

CLI exit behavior:

- **2** — invalid input (malformed JSON, invalid field value, missing required
  field, unsupported/malformed concentration, or a numeric value that cannot be
  parsed exactly). The error is printed to stderr; **no Python traceback** is
  emitted.
- **1** — a supported limit was **exceeded** (`exceeds_supported_limit`) or an
  ingredient carries an **unambiguous prohibition** finding (e.g. a pure Annex
  II ban such as Benzene). An unambiguous prohibition is flagged explicitly
  (`prohibition_finding == "unambiguous"`) and is **never inferred** from the
  retained headline `determination` alone.
- **0** — otherwise. Unresolved cases (`requires_context`,
  `requires_manual_review`, `unknown_ingredient`,
  `within_supported_numeric_limit`) remain explicitly unresolved regardless of
  the exit code.

> **Exit 0 is not "clean" or "compliant".** A Hydroquinone formula that retains
> its Annex II evidence but whose cross-annex exception applicability is
> unresolved (`prohibition_finding == "exception_unresolved"`) returns exit 0
> with `requires_manual_review` — the prohibition/exception is intentionally
> unresolved, not cleared. Likewise `within_supported_numeric_limit` is a numeric
> comparison only and never an affirmative OK.

### Scoped Annex II prohibitions are not unconditional bans

An Annex II rule whose source name carries a **scope/exception qualifier**
(e.g. `... when used as a substance in hair dye products`, or an `exception of`
Annex III entry) is **not** classified an unconditional ban. A bounded check
(`_annex2_scope_finding`) inspects only the source name for a small, documented
set of scope phrases; it does **not** interpret arbitrary legal text. Such a
prohibition is returned with `prohibition_finding == "scope_unresolved"` (or
`exception_unresolved`), retained as evidence with its scope text, and does
**not** trigger exit 1. Benzene (an unconditional ban) keeps
`prohibition_finding == "unambiguous"` and exits 1. A narrower scope is never
used as evidence that another use is permitted.

Existing label/batch exit behavior is unchanged.

## Runnable example

```bash
python examples/formulas/demo_evaluator.py
```

Demonstrates below/at/above a limit, missing context, mismatched basis, a
complex rule, a cross-annex exception, an unknown ingredient, and a missing
concentration.

## Honesty

"Regulatory information, not legal advice." A numeric "within limit" is never a
safety certificate or compliance determination.
