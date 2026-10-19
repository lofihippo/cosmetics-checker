# eu-reg-lists

Normalized EU regulatory substance data and a lookup API, shared by the linter,
HTTP API and scanner. Coverage depends on the loaded snapshot.

## Data sources

The pipeline imports official exports into a versioned snapshot. ECHA acquisition
is disabled; normal refreshes do not contact it. Coverage depends on the loaded
snapshot. See [methodology](../../docs/methodology.md) and
[product scope](../../docs/product-scope.md).

`data/scripts/fetch.py` stages exports under `data/raw/`.
`data/scripts/normalize.py` produces SQLite, JSONL and metadata under
`data/processed/`. Identities without verified rules keep
`determination: "unknown"`.

## Install

```bash
pip install -e packages/eu-reg-lists
```

## Usage

```python
import eu_reg_lists as erl

reg = erl.load("data/processed/eu_reg_lists.sqlite")

# search by CAS / INCI / E-number / alias
reg.search("25956-17-6")      # CAS
reg.search("Red 40")          # INCI name
reg.search("e129")            # E-number

# Check a single ingredient
res = erl.check_token("red 40", reg)
print(res.is_in_dataset, res.determination, res.status, res.regulation)
```

`CheckResult` exposes `is_in_dataset`, `determination`, `status`, `annex`,
`regulation`, `source_url`, `data_updated_at`, `conditions`, `resolution`, and
the full `rules` list.

## Collision handling

Names and aliases map to exactly one substance. If a name/alias is ambiguous
(shared by two substances, or a canonical name colliding with a different
substance's alias), it is rejected as ambiguous and resolves to `None`
(unresolved).

## Data model

* `Substance` — stable identity (id, name, aliases, cas, ec, e_number, ci).
  `determination` is `unknown` unless verified; `rules` holds all applicable
  `Rule`s.
* `Rule` — one determination: `status`, `annex`, `regulation`, `source_url`,
  `source_updated_at`, `conditions`, `resolution`, `effective_date`.

## Licensing
Code is MIT. Data licenses vary (CosIng = EU reuse/attribution, ECHA = its own
terms) — see [LICENSES.md](../../LICENSES.md).

## Shared label checks

`check_label(text, registry)` uses the same bracket-aware tokenizer and counts
as the linter, API and scanner. Known punctuation and percentage-bearing names
are protected before annotation cleanup. Supplementary aliases within one label
span count once per exact registry identity; `matched_aliases` retains the other
names and `derived_from` records the parent text. `tokens` / `n_candidates`
retain all candidates; `results` / `n_ingredients` reflect deduplication.
An explicit `tokenize_fn` remains supported for custom tokenization.

Regulatory information, not legal advice.
