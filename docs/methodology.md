# Methodology — eu-reg-lists (Phase 1)

How the EU substance data is sourced, normalized, and made honest.

## Sources

We ingest **official export / downloadable files only** — we do **not** scrape
ECHA or any official website (AGENTS.md #4).

| Source | What it provides | How we get it |
|--------|------------------|---------------|
| European Commission **CosIng** | Annex II–VI cosmetic substance lists | official export (`.txt`/`.csv`; annex declared in the file preamble) into `data/raw/cosing-annex*/` |
| **ECHA SVHC Candidate List** | REACH candidate substances | official package into `data/raw/echa-svhc/` |
| **ECHA Annex XIV** | Authorisation List | official package into `data/raw/echa-annex-xiv/` |
| **ECHA Annex XVII** | Restrictions | official package into `data/raw/echa-annex-xvii/` |
| **EUR-Lex** Regulation (EC) No 1223/2009 | authoritative regulation text | legal-text XML (`data/raw/eurlex-1223/`) |

If a source cannot be reached (e.g. ECHA returns 403 from some networks), the
fetch script records it as **not fetched / rejected**; the pipeline does **not**
fabricate data or scrape around it. Downloaded content is validated before
acceptance (`validate_content`): an HTML error page or empty body is rejected,
never accepted as data.

### Snapshot safety (a failed refresh never wipes coverage)

Downloads are staged at `data/raw/.incoming/<source>/` and validated there. They
are promoted to the active snapshot (`data/raw/<source>/`) **only after** a
successful import (see `fetch.commit_source`). A failed/rejected refresh is
recorded in `data/raw/fetch_attempts.json` and does **not** overwrite the active
`fetch_manifest.json` `accepted` status. The active raw file and manifest are
preserved and exported as a CI artifact; `data/processed/metadata.json` carries
a committed `snapshot` provenance record (per-source sha256 + acceptance) as the
durable source-of-record pointer.

## Normalization

`data/scripts/normalize.py` reads the raw exports and produces:
* `data/processed/eu_reg_lists.sqlite` — normalized relational store
  (`substances`, `rules`, `metadata` tables)
* `data/processed/eu_reg_lists.jsonl` — one substance per line
* `data/processed/metadata.json` — counts, source hashes, `content_hash`,
  `data_updated_at`, and a `snapshot` provenance record

Official **Reference Numbers** (Annex entry numbers) are preserved in each
rule's `raw.official_reference` **separately** from the file row position
(`raw.row_reference`), so a citation is stable even when physical file layout
changes.

## Identity reconciliation (no silent drops / no wrong attachment)

Export rows are matched to curated identities by **CAS then EC**, then by a
verified-name mapping, then by curated name/alias.

* A row whose CAS/EC **conflicts** with the substance it would otherwise match
  is **quarantined** (recorded in `metadata["reconcile"]["quarantined"]`),
  never silently re-attached to the wrong identity.
* A synonym with matching identifiers attaches to the existing substance
  (e.g. `Butylphenyl methylpropional` → `Lilial`). Aliases are preserved.
* A row with no matching identity creates a new substance carrying the verified
  rule.

### Placeholder identifiers are not identity evidence

The real CosIng export uses `-`, `- / -`, `-/-`, `- -`, `- -- -` and similar
calligraphy to mean "not specified / not applicable". These shared placeholders
are **not** identifiers and are normalised to **absent** (NULL) before indexing
and reconciliation — they never enter the CAS/EC lookup index, so they can no
longer collide and cause false quarantines.

### Identifier syntax normalisation (bounded)

The official exports join several identifiers in a single cell and use
formatting that is **not** identity evidence. `normalize._id_split` splits a
CAS/EC cell only on the explicit separators actually present (`/`, `;`), strips
a trailing separator (`38083-17-9;` → `38083-17-9`) and a trailing bracketed
source marker (`13463-67-7[1]` → `13463-67-7`). Multi-value cells are reduced to
a canonical delimiter-joined list of cleaned tokens (`65-85-0 / 532-32-1`).
**This never splits on a space or hyphen that belongs to one identifier**, and
never invents a token from free text.

The Annex IV `Colour index Number / Name of Common Ingredients Glossary` cell
mixes CI numbers with glossary names. Only the explicitly-designated `CI <digits>`
tokens are taken as Colour Index evidence (`_ci_extract`); the descriptive cell
is **never** treated as a CI identifier (so `TITANIUM DIOXIDE/CI 77891` yields
CI `77891`, not the whole cell).

### Explicit source-backed group membership

A source entry may denote a **group of members** (e.g. Annex V ref 1 "Benzoic
acid and its sodium salt" naming `BENZOIC ACID; SODIUM BENZOATE`) or a **form**
with several identifiers (e.g. "Iron Oxide red", "Titanium dioxide (nano)"). Such
a row is:

* **kept as its own regulatory entry** (a distinct substance with the group's
  rule and raw multi-identifier evidence), never merged into a single
  chemical identity;
* **linked to member identities** by the **explicit member-name tokens** in the
  **authoritative glossary columns only** (`Name of Common Ingredients Glossary`
  for Annex III/V/VI, the Annex IV combined colour-index/glossary column). The
  auxiliary `Identified INGREDIENTS or substances e.g.` column is **not** used to
  decide group membership — it lists related names/CI identifiers, may restate
  the entry's own name, and is not authoritative member enumeration.
  A link is recorded on the member as `member_of` and bound to the **specific
  source rule** it came from (`source_key`), so a membership does not inherit an
  unrelated rule stored on the same record.
* surfaced on the member via the checker's `membership` + `linked_rules` fields
  and preserved through the formula evaluator. Group membership identifies
  **relevant evidence**; it does not merge identifiers or establish that every
  group condition applies or is satisfied.

A **single-member, single-identifier** row is an ordinary substance, not a group
(so it never links to itself). **Self-links are excluded** (a member token that
resolves to the group's own id is skipped and recorded as unresolved). A
**form-qualified name** (e.g. `Zinc oxide (nano)`) is a distinct form and is not
collapsed into the bare-form identity even when the identifier coincides.

We never assume the CAS/EC/name lists align by position; only an explicit
member-name token is used to link a member. Nano and non-nano forms remain
distinct even when they share a CAS number, and a broad "Iron oxides" identity is
not collapsed into its specific red/yellow/black entries.

### Administrative rows are excluded from active findings

A source row that is an explicit administrative placeholder (the CosIng
"Moved or deleted" marker) is **not** a substance entry. It is recorded in
`metadata["reconcile"]["skipped_admin_rows"]` (with its source file, row, reference
and annex) but produces **no** active substance, prohibition, restriction or
conditional allowance. Administrative rows are reported **separately** from
quarantines; only conservative explicit markers are recognised (an unusual
substance name is never treated as administrative).

### Genuine conflicts are still rejected

Identifier-overlap is **candidate evidence, not proof** of the same identity:
* A row matching a simple single-identifier substance by name/CAS but carrying a
  **contradictory EC/CAS** is a genuine conflict → **quarantined** (ambiguity
  rejection), never silently re-attached.
* A row matching a **group/form/multi-identifier** entry by overlapping
  identifiers is form-sharing, not same identity → retained as its own entry and
  the proposed association is recorded as **unresolved** (never merged, never
  dropped).

### Glossary aliases

The export's `Name of Common Ingredients Glossary` column is added as a
narrowly-sourced alias on the substance (e.g. `OXALIC ACID` → the oxalic-acid
entry), so a formula may resolve a substance by its explicit glossary INCI name.
Placeholder glossary values are ignored.

### Candidate-export source-validation fallback

A candidate staged export whose self-declared annex contradicts its configured
source annex (e.g. an Annex II file staged under `cosing-annex-iii`) is **not**
promoted: the parser raises an explicit `AnnexMismatchError`, the failure is
recorded in `metadata["source_validation_failures"]`, and the pipeline falls
back to the accepted **active** snapshot instead of aborting the refresh. Only
this expected error is caught; unrelated programming errors still propagate.

## Honesty rules applied

1. **Identity ≠ determination.** A recognised name (from the curated Phase 0
   seed) is stored with `determination: "unknown"` and no rules. It is NOT
   asserted to be "no restriction".
2. **Verified only from exports.** A substance gains `determination` one of
   `banned / restricted / allowed_with_conditions` ONLY when a rule is parsed
   from an official export.
3. **Rules kept, not collapsed.** A substance may carry multiple rules (e.g. a
   cosmetic Annex V rule AND a food rule). All are retained; the strongest is
   the headline.
4. **Explicit resolution.** Each rule records a `resolution` (e.g.
   `cosing-annex-annex-ii`, `echa-svhc`).
5. **Ambiguity is rejected.** A name/alias mapping to more than one substance
   (or colliding with a different substance's canonical name) resolves to
   `None`.
6. **Traceable provenance.** Every rule carries an `raw` block with the exact
   `source_file`, `source_sha256`, `row_reference`, `fetched`/`accepted`, and
   `retrieved_at`. `metadata["verified_sources"]` records per-source file
   hashes and retrieval status.

## Annex III restrictions and conditions

Annex III entries are restrictions, not prohibitions. Each restricted rule
carries:

* A compact `conditions` summary string (backward-compatible) and a structured
  `raw.conditions_list`: an ordered list of `{field, label, value}` fragments
  for `product_types`, `max_concentration`, `other`, and `warnings`.
* The **complete source row** (`raw.source_row`) so unrecognised fields are
  never lost as the export schema evolves.
* An official Reference Number preserved separately from the file-row locator.

These condition fragments are preserved **verbatim** from the official export and
are **not** parsed into a concentration verdict — an Annex III match means a
restriction requires evaluation, not that a limit was exceeded. The source data
needed for a future structured-formula milestone is retained.

A substance subject to **both** an Annex II prohibition and an Annex III
restriction keeps a **banned** headline (the strongest rule wins), while the
Annex III restriction is retained as a separate, distinct rule. This is
order-independent.

## Annex IV / V / VI conditional positive lists

Annex IV (colourants), V (preservatives) and VI (UV filters) are **conditional
positive lists**. Their presence is **evidence of a source-list entry**, not a
compliance determination. The pipeline treats them explicitly:

* A rule parsed from Annex IV/V/VI carries `status:
  "allowed_with_conditions"` (never "clean"/"OK"). Its `conditions` /
  `raw.conditions_list` preserve the source's product-type, concentration,
  other, and warning fragments **verbatim** — they are **not** auto-satisfied.
* **Absence from a positive list is not a prohibition.** A raw ingredient name
  does not establish its intended function, applicable scope, or relevant
  exception, so this pipeline does **not** infer a violation from a
  non-match.
* A substance present only in a positive list is **still a flagged /
  conditional finding** (the linter's `flagged` set includes
  `allowed_with_conditions`), and is **never** reported as `clean`. The
  browser wording makes this explicit: "allowed with conditions (not clean)".
* Cross-list rules are retained. A substance may carry several rules from
  Annex IV, V, and VI (and II/III); each is a separate rule with its own
  citation. An Annex II prohibition or Annex III restriction remains the
  strongest headline (e.g. a compound in both Annex II and IV keeps a `banned`
  headline plus the Annex IV conditional rule).
* `determination` uses the strongest rule across all of a substance's rules:
  `banned > restricted > allowed_with_conditions > unknown`.

This milestone **adds source coverage and evidence** — it is **not** a general
positive-list compliance engine. The six Annex III numeric mappings are
unchanged; for a substance with only a positive-list rule the formula evaluator
returns `requires_manual_review` / `requires_context` and retains all rule
evidence and unresolved requirements without inventing a numeric verdict.

## Refresh detection

`metadata["content_hash"]` is a deterministic, timestamp-independent hash of
the normalized substance facts + source file hashes. On identical inputs it is
unchanged, so the CI refresh workflow only publishes when the data truly
changed — not merely because `data_updated_at` moved.

`metadata["content_hash_version"]` records which schema contract produced the
hash. `compute_content_hash(substances, sources, hash_version=…)` accepts the
known contracts (0–3), so a snapshot is re-verifiable under its own historical
contract and an unknown version is rejected rather than silently reinterpreted
(see `docs/snapshot-setup.md`).

## What "not verified" means

A high `count_identity_only` and `count_verified_rules == 0` is the correct,
honest output when the official cosmetic/REACH exports have not been fetched
and parsed successfully. It is NOT a defect; it reflects that the curated seed
(Phase 0) is **recognised-names-only** and must not be promoted to
authoritative regulatory records without source evidence.

