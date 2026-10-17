# Methodology — eu-reg-lists (Phase 1)

How the EU substance data is sourced, normalized, and made honest.

## Sources

We ingest **official export / downloadable files only** — we do **not** scrape
ECHA or any official website.

| Source | What it provides | How we get it |
|--------|------------------|---------------|
| European Commission **CosIng** | Annex II–VI cosmetic substance lists | official export (`.txt`/`.csv`; annex declared in the file preamble) into `data/raw/cosing-annex*/` |
| **ECHA SVHC Candidate List** | REACH candidate substances | **NOT included** (deferred; acquisition disabled) |
| **ECHA Annex XIV** | Authorisation List | **NOT included** (deferred; acquisition disabled) |
| **ECHA Annex XVII** | Restrictions | **NOT included** (deferred; acquisition disabled) |
| **EUR-Lex** Regulation (EC) No 1223/2009 | authoritative regulation text | legal-text XML (`data/raw/eurlex-1223/`) |
| **EUR-Lex / Publications Office** Commission Regulation (EU) 2024/1328 | REACH Annex XVII **entry 70** (D4/D5/D6) | public official OJ PDF (independent EU act; distinct from ECHA). Implemented as a source-bound evaluator, **not** a snapshot source list. |

ECHA lists are **deliberately excluded** from this release. We do not accept
ECHA's portal terms, and no ECHA export is downloaded or ingested. ECHA coverage
is surfaced as a coverage gap / "not included" — never a clean result.

### Annex XVII entry 70 (bounded)

REACH Annex XVII **entry 70** is implemented as a narrow, source-bound evaluator
from Commission Regulation (EU) 2024/1328 (CELEX 32024R1328 / ELI
`http://data.europa.eu/eli/reg/2024/1328/oj`). Only the **cosmetic** branches are
supported: paragraph 3(a) **wash-off** cosmetics (D4/D5, 0,1 % by weight, from
31 January 2020) and paragraph 3(b) **other cosmetic products** (0,1 % by weight,
from 6 June 2027). A matched D4/D5/D6 identity surfaces as an entry-70 finding
that is **identified-but-unevaluated** on a label-only check (no
concentration/product-type/assessment date), **never a category violation**.
Unsupported uses (dry cleaning, devices, medicinal, industrial, paragraph 6
residue derogations) return `unsupported_use`; missing context stays
`missing_context`. This is **not** full Annex XVII or REACH coverage, and it
never replaces the independent CosIng determination.

**Source gating (release contract).** Entry 70 is evaluated and advertised as
covered **only** when the loaded snapshot carries an accepted, compatible
source/mapping record under the `reach-annex-xvii-entry70` source kind in
`verified_sources` (document SHA-256 + mapping version + supported branches must
match). An empty or historical CosIng-only snapshot therefore neither advertises
nor evaluates entry 70. The retained official OJ PDF (document SHA-256
`2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966`) is the
source; it is an amending act, so the consolidated entry-70 text must be
re-verified for later amendments/corrigenda before a subsequent refresh
(recorded as a consolidation-check obligation). ECHA exports remain excluded.

**Source currentness (checked-as-of 2026-10-13).** The entry 70 treatment is
built from Commission Regulation (EU) 2024/1328 (CELEX `32024R1328`), the
amending act published in OJ L, 17 May 2024. The checked-as-of scope is the
verified amending-act text as retained; no later amending act, corrigendum or
consolidation was itself downloaded and re-verified as part of the retain-today
check. Official-document relationship review on the checked-as-of date records
**Italian, German and French corrigenda** for this act as present on EUR-Lex
(the English text and the retained mapping were not shown to be changed thereby;
this is recorded as a note, not an assurance). A future-date assessment example
that uses the verified 3(b) effective date (6 June 2027) is a scenario under the
verified amending-act text, **not** evidence that no future amendment can occur.
The supported assessment-date scope is therefore the text of the retained
amending act; before a subsequent refresh the consolidated entry-70 text must be
re-verified for any later amending act or corrigendum and the consolidated
CELEX revision identifier recorded. This check is **not** performed by a
reminder string; it is the recorded reviewed-documents record above.


**Applicability semantics.** The cosmetic product type must be explicit:
generic/ambiguous text (`cosmetic`, bare `other`) does not establish whether the
product is washed off and stays unresolved (`missing_context`), rather than
assumed to be a leave-on/other product. Only explicit `wash-off` / `leave-on` /
`other_cosmetic` / `non-wash-off` product types drive a branch. D6 is not
covered by paragraph 3(a) (wash-off, D4/D5 only); a D6 in a cosmetic product
falls under paragraph 3(b), which this evaluator supports only when the caller
supplies an explicit non-wash-off product type — the wash-off/D6 case is a
documented exclusion. Weight basis is percent-by-weight; the threshold operator
is `>=`; the assessment date (ISO-8601, required for the transition boundary) is
supplied through the submitted context and is never silently ignored.

**Concentration basis (marketed / finished product).** The paragraph 1 market
trigger compares the substance "as ... 0,1 % by weight" in the mixture the
cosmetic product places on the market. A supported comparison therefore requires
the concentration to be expressed on the **marketed / finished (ready-for-use)**
basis, percent by weight. A missing or incompatible basis (e.g. `raw_material`,
or a `ready_for_use=false` flag) does **not** establish the marketed cosmetic
concentration and stays unresolved (`missing_context`) — it is never silently
converted or assumed. Only the marketed basis drives a supported numeric
comparison.


If a source cannot be reached (e.g. a CosIng endpoint returns 403 from some
networks), the
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
   `banned / restricted / listed / allowed_with_conditions` ONLY when a rule is
   parsed from an official export. `listed` denotes REACH Candidate List (SVHC)
   listing information requiring contextual review — never an automatic
   cosmetic prohibition or a numeric concentration violation.
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
is retained for the supported, explicit formula mappings described in
[formula evaluation](formula-evaluator.md).

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
  `banned > restricted > listed > allowed_with_conditions > unknown`.

These lists **provide source coverage and evidence** — it is **not** a general
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

## What "not verified" / "not included" means

A high `count_identity_only` and `count_verified_rules == 0` is the correct,
honest output when the official cosmetic exports have not been fetched
and parsed successfully. It is NOT a defect; it reflects that the curated seed
(Phase 0) is **recognised-names-only** and must not be promoted to
authoritative regulatory records without source evidence.

ECHA lists are **deliberately not included** in this release (acquisition
disabled). Their absence is surfaced in the coverage report as `not_included`
(distinct from ordinary `missing` coverage gaps) and is **never** treated as a
clean result.

## Consistent prohibition/restriction interpretation (label path = formula path)

A matched prohibition entry is **not** automatically a demonstrated product
violation, and a scoped/conditional source entry is **not** an unconditional
ban. The shared `eu_reg_lists.checker` is the single interpretation engine;

it exposes:

* `CheckResult.evaluation` — `prohibition_identified` (unambiguous ban) /
  `restriction_identified` / `scope_unresolved` (scoped/exception ban) /
  `listing_identified` / `unrestricted_identified`.
* `CheckResult.prohibition_finding` — `unambiguous` (definitive ban) /
  `scope_unresolved` (source scope/exception applicability unresolved). Added
  to the label/batch path so it carries the same distinction as the formula
  evaluator instead of surfacing an unconditional-sounding prohibition.

The scoped/exception phrase list (`ANNEX2_SCOPE_PHRASES`) and the
`annex2_prohibition_finding` / `banned_rule_is_unambiguous` helpers live ONLY
in `eu_reg_lists.checker`; the formula evaluator (`eu_ingredient_linter.formula`)
DELEGATES to them, so the two paths never drift into independent interpretation
engines. The phrases are a bounded syntactic check of explicit qualifiers in the
source `Chemical name / INN` — never an interpretation of arbitrary legal text.

Consequences in the ordinary label path:

* An **unambiguous** Annex II ban (e.g. Benzene) is flagged and counted in
  `n_banned`; the formula evaluator's `prohibition_finding == "unambiguous"`
  also triggers exit 1.
* A **scoped / cross-annex** Annex II entry (e.g. hydrolysis of CI 44045
  "... when used as a substance in hair dye products", or Hydroquinone's
  "... with the exception of entry 14 in Annex III") is still **flagged** as
  evidence BUT is not counted as a definitive prohibition (`n_banned` excludes
  it) and its evaluation is `scope_unresolved`.
* A **conditional positive-list** rule (e.g. Sodium benzoate's linked Annex V
  rule) is a conditional/restriction finding, never a demonstrated violation.
* An **unverified identity** (Aqua, Glycerin) is never presented as dangerous or
  as unrestricted — it is unresolved.

The full source evidence (per-rule status, official reference, `source_row`
scope text, conditions, provenance) is preserved separately from the
user-facing interpretation; a source prohibition is never erased just because
its applicability is unresolved.


## Label tokenization

Regulatory information, not legal advice.

The CLI, GitHub Action, API and scanner use the same label tokenizer and
checking engine. Commas, semicolons, newlines and unprotected slashes split only
outside balanced `()` / `[]`. Unmatched brackets are literal text and do not
hide the rest of a label. Exact known names containing commas, ampersands or
slashes are protected as whole phrases, longest match first; unknown slashed
text retains the earlier split behavior. There is no fuzzy matching.

`Parfum (Lilial)` retains `parfum` and checks `lilial` as a supplementary
candidate. Nested bracket content and inner lists are checked too; leading
`contains`, `contient` and `incl.` are stripped inside brackets. Only candidates
that resolve unambiguously in the loaded registry enter the results and counts.
Unresolved translations/descriptors add no extra unknown ingredients; they are
not evidence of an unrestricted ingredient. A label consisting only of brackets
uses their content as primary input, so unknown content remains unresolved.
Empty brackets add no ingredient.

Each result gains an optional `derived_from` field containing its normalized
bracketed parent span (null for primary ingredients). Existing `tokens` remain
strings and all other report fields retain their meanings. Recognized full
bracketed names are retained, with their inner candidates also checked; `(nano)`
and `[nano]` forms are never reduced to the bare substance. For example, the
curated seed recognizes `aqua (water)` as a whole alias. Botanical descriptors
otherwise leave the outer name (`Camellia Sinensis Extract`).

`may contain`, `peut contenir`, `+/-`, `[+/-]` and `(+/-)` introduce ordinary
checked ingredients, including CI colorants. They do not suppress findings.
Percentages inside or outside brackets are removed for label matching; they
are not used to infer a formula concentration.

The root `eu_lint.py` and its Streamlit `app.py` consumer are **deprecated
Phase-0 demonstrations**, retained for historical compatibility. Their legacy
tokenizer is not the supported label-checking path and can omit parenthetical
ingredients. Use `python -m eu_ingredient_linter` or the API/scanner instead.
