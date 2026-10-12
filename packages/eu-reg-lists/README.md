# eu-reg-lists

Normalized EU regulatory substance data + a lookup API. This is **Phase 1** —
the data foundation that the `eu-ingredient-linter` (Phase 2) and `eu-reg-api`
(Phase 3) build on top of.

> Regulatory information, not legal advice. A substance with `determination:
> "unknown"` is NOT "clean" and NOT "no restriction" — it is unverified.

## How the data is sourced (honesty)

The pipeline **only ingests official export files** (CosIng Annex exports from
the European Commission; ECHA packages). It does **not** scrape ECHA or any
official website (per AGENTS.md #4).

* `data/scripts/fetch.py` downloads official export files into `data/raw/`.
  Sources that can't be fetched (e.g. ECHA returns 403 from some networks) are
  recorded as **not fetched** — never scraped, never fabricated.
* `data/scripts/normalize.py` reads those exports and produces:
  * `data/processed/eu_reg_lists.jsonl` — one substance per line
  * `data/processed/eu_reg_lists.sqlite` — queryable store
  * `data/processed/metadata.json` — counts, source hashes, `data_updated_at`
* A substance gains a **verified** determination ONLY when a rule is parsed
  from an official export. Curated identities carried from Phase 0 stay
  `determination: "unknown"` with no rules — they are **not** promoted to
  verified regulatory records.

Consequence: in the current build, many recognised names are present as
identities with `determination: "unknown"`, and `count_verified_rules` may be
`0` until CosIng/ECHA exports are actually fetched and parsed. This is the
honest state, not a bug.

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

# check a single ingredient -> CheckResult (honest unknown semantics)
res = erl.check_token("red 40", reg)
print(res.is_in_dataset, res.determination, res.status, res.regulation)
```

`CheckResult` exposes `is_in_dataset`, `determination`, `status`, `annex`,
`regulation`, `source_url`, `data_updated_at`, `conditions`, `resolution`, and
the full `rules` list (a substance may carry multiple rules — cosmetic + food
— all retained).

## Collision handling

Names and aliases map to exactly one substance. If a name/alias is ambiguous
(shared by two substances, or a canonical name colliding with a different
substance's alias), it is rejected as ambiguous and resolves to `None`
(unresolved). This never silently picks a record.

## Data model

* `Substance` — stable identity (id, name, aliases, cas, ec, e_number, ci).
  `determination` is `unknown` unless verified; `rules` holds all applicable
  `Rule`s.
* `Rule` — one determination: `status`, `annex`, `regulation`, `source_url`,
  `source_updated_at`, `conditions`, `resolution`, `effective_date`.

## Licensing
Code is MIT. Data licenses vary (CosIng = EU reuse/attribution, ECHA = its own
terms) — see `LICENSES.md`.
