# eu-ingredient-linter

CLI and report formats for cosmetic labels and structured formulas. Uses the
shared `eu-reg-lists` checker and the loaded snapshot's coverage.

## Run

From the repository root, use the environment and verified snapshot described
in the [setup guide](../../README.md#run-locally).

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --label "Aqua, Benzene, TotallyFakeIngredient"

python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite --batch labels.txt

python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --formula examples/formulas/rust-inhibitor.json

python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv
```

Choose one input mode: `--label`, `--batch` (one label per line), `--formula`
(JSON), or `--input` (CSV, TSV or JSON). Each structured file represents one
formula.

## Structured inputs

CSV and TSV use these columns; JSON wraps ingredient records in an
`ingredients` array.

| Field | Meaning |
|---|---|
| `ingredient` | Required exact name or identifier. |
| `percent` | Decimal concentration, 0–100; blank means absent. |
| `unit` | Concentration unit; supported comparisons use `%` by weight. |
| `basis` | Concentration basis, such as `ready_for_use` or `after_mixing`. |
| `product_type` | Product context. |
| `body_area` | Area of application. |
| `professional_use`, `ready_for_use` | `true`/`false` (CSV/TSV also accept `yes`/`no`); blank means absent. |
| `assessment_date` | ISO date (`YYYY-MM-DD`), required for date-dependent rules. |

Files use UTF-8 (BOM accepted) and `.` as the decimal separator. Standard CSV
quoting supports commas and newlines in cells. Concentrations stay as decimal
strings during parsing to preserve precision.

Blank records are ignored. Partially filled rows without an ingredient,
duplicate headers, unsupported columns, malformed values and duplicate resolved
identities are rejected. Missing context stays absent; it is not inferred from
other fields or ingredient order.

See [formula evaluation](../../docs/formula-evaluator.md) for the full schema,
supported rules, required context and verdicts.

## Output

Use `--format text|json|csv|html|github`; the default is text. `--json` is a
shorthand for `--format json`.

```bash
python -m eu_ingredient_linter \
  --registry data/processed/eu_reg_lists.sqlite \
  --input examples/formulas/structured-formula.csv --input-format csv \
  --format html > report.html
```

| Format | Contents |
|---|---|
| Text | Summary, ingredient findings and citations. |
| JSON | Full report, including submitted context and unresolved requirements. |
| CSV | One row per direct or linked rule; `no_rule` rows retain unknown ingredients. Nested values use JSON cells. Metadata rows contain coverage and the notice; batch `item_state` rows record validity. |
| HTML | Standalone printable report with embedded CSS, escaped text and validated links. |
| GitHub | Escaped workflow annotations for direct/linked rules and unknown ingredients. See the [reusable Action](../../.github/actions/eu-ingredient-linter/README.md). |

Reports retain regulation references, source URLs, snapshot `data_updated_at`,
source dates, official entry numbers and file-row locators. Changing format
does not change the evaluation or exit status. CSV columns are defined in
[`reporters.py`](eu_ingredient_linter/reporters.py).

Exit `1` indicates a finding covered by the CLI's failure policy; invalid input
or operational errors exit `2`. Exit `0` can include unresolved findings and
is not a compliance determination.

## Library

```python
from eu_reg_lists import load
from eu_ingredient_linter import ingredient_report, evaluate_formula

registry = load("data/processed/eu_reg_lists.sqlite")
label = ingredient_report("Aqua, Parfum (Lilial)", registry)
formula = evaluate_formula({"ingredients": [
    {"ingredient": "Toluene", "percent": "25", "unit": "%",
     "basis": "ready_for_use", "product_type": "nail_product"}
]}, registry)
```

Label parsing checks exactly resolved parenthetical candidates and “may
contain” sections. Known names keep their punctuation. Supplementary aliases
within one label occurrence count once, with `matched_aliases` and
`derived_from` preserving the input. See [tokenization](../../docs/methodology.md#label-tokenization).

## Interpreting results

A rule match may need context before its applicability can be established.
Unknown identities stay unresolved. Conditional positive-list entries remain
flagged, and a numeric comparison does not resolve warnings or other conditions.
Coverage is reported from the loaded snapshot; see [product scope](../../docs/product-scope.md).

Regulatory information, not legal advice.

## Maintenance

Run `python -m pytest packages/eu-ingredient-linter/tests/ -q` or the root
`make quality` gate. For data updates, follow [methodology](../../docs/methodology.md)
and [snapshot setup](../../docs/snapshot-setup.md).
Code is MIT; [data licenses](../../LICENSES.md) vary by source.
