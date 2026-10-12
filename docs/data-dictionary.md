# Data Dictionary — eu-reg-lists (Phase 1)

## `data/processed/eu_reg_lists.sqlite`

### Table `substances`

| Column | Type | Meaning |
|--------|------|---------|
| `id` | TEXT PK | stable substance id (slug) |
| `name` | TEXT | canonical INCI / display name |
| `aliases` | TEXT (JSON) | list of aliases / E-numbers / CI numbers / common names |
| `cas` | TEXT | CAS registry number, or NULL |
| `ec` | TEXT | EC number, or NULL |
| `e_number` | TEXT | EU food-additive E-number, or NULL |
| `ci` | TEXT | Colour Index number, or NULL |
| `determination` | TEXT | `unknown` / `allowed_with_conditions` / `restricted` / `banned` |
| `sources` | TEXT (JSON) | source ids that populated this substance |
| `member_of` | TEXT (JSON) | list of explicit source-backed group memberships (group_id, official_reference, annex, source_kind, source_file, row_reference, member_name) |

`determination == "unknown"` means the substance is recognised but carried no
verified rule from an official export. It is NOT "clean" / "no restriction".

`member_of` records that this identity is a **named member** of a group source
entry (e.g. `Sodium benzoate` → "Benzoic acid and its sodium salt"). It links to
the **group's source identity**, not to shared identifiers: it never merges the
member into the group or aliases the member's identity. The group's own rules are
surfaced by the checker as `linked_rules`.

### Table `rules`

| Column | Type | Meaning |
|--------|------|---------|
| `substance_id` | TEXT | FK -> substances.id |
| `status` | TEXT | `banned` / `restricted` / `allowed_with_conditions` |
| `annex` | TEXT | `Annex II`..`VI` / `SVHC` / `Annex XIV` / `Annex XVII` / `Food` |
| `regulation` | TEXT | human-readable regulation citation |
| `source_url` | TEXT | URL for the source |
| `source_updated_at` | TEXT | upstream pub/update date, or NULL if unknown |
| `conditions` | TEXT | concentration caps / use conditions / warnings |
| `resolution` | TEXT | how the status was derived (e.g. `cosing-annex-annex-ii`) |
| `effective_date` | TEXT | date a restriction takes effect, or NULL |
| `raw` | TEXT (JSON) | raw source row for audit |

### Table `metadata`

Key/value: `data_updated_at`, `count_substances`, `count_verified_rules`,
`count_identity_only`, `verified_sources` (with source hashes), `version`,
`note`.

## `data/processed/eu_reg_lists.jsonl`

One JSON object per line, matching the `Substance` structure above (rules as a
nested list). `determination` is `unknown` for identity-only substances.

## `data/processed/metadata.json`

`count_substances`, `count_verified_rules`, `count_identity_only`,
`data_updated_at`, `verified_sources` (per-source URL + file hashes), `version`,
and an honesty `note`.

## `CheckResult` (`eu_reg_lists`)

| Field | Meaning |
|-------|---------|
| `ingredient` | the token that was checked |
| `is_in_dataset` | True if the token resolved to a known substance identity |
| `determination` | `unknown` (not-found or verified-less) or a verified status |
| `status` | headline status (strongest rule), or NULL when unverified |
| `annex` / `regulation` / `source_url` / `data_updated_at` | citation of the strongest rule |
| `conditions` | caps / use conditions |
| `rules` | ALL applicable rules (may be >1) |
| `resolution` | how the headline rule was derived |
| `membership` | explicit source-backed group memberships of this identity |
| `linked_rules` | the group's own rules surfaced as *linked* evidence (never merged); a member with only linked rules is still a conditional/restricted finding, never clean |

## `FormulaEvaluation` / `FormulaEvaluationModel`

Adds (beyond `CheckResult`):
| Field | Meaning |
|-------|---------|
| `prohibition_finding` | `unambiguous` (definitive ban), `exception_unresolved` / `scope_unresolved` (scope/exception applicability unresolved, never a definitive prohibition), or None |
| `unresolved_requirements` | every unevaluated condition of the retained rules (including positive-list conditions), so a multi-list result preserves the complete evidence |
| `numeric_comparison` | `exceeds_supported_limit` / `within_supported_numeric_limit` / `None` |
| `membership` | explicit source-backed group memberships (source group identity, official reference, annex, source kind/file/row, member_name) |
| `linked_rules` | the group's rules surfaced as *linked* evidence (never merged into the member's own `rules`); a member with linked evidence is NOT "no regulatory evidence" |

## `data/processed/metadata.json` `reconcile`

`matched_by_identifier`, `matched_by_name`, `new_created`, `quarantined`, plus:
| Key | Meaning |
|-----|---------|
| `skipped_admin_rows` | administrative placeholder rows (e.g. "Moved or deleted") excluded from active findings, with source file / row / reference / annex |
| `count_skipped_admin` | count of the above |
| `member_links` | explicit group→member associations (separate from rule counts) |
| `count_member_links` | count of the above |
| `unresolved_memberships` | member names that resolve to no / several identities (recorded, never picked) |
| `count_unresolved_memberships` | count of the above |

## Conditional positive lists (Annex IV / V / VI)

Rules parsed from Annex IV (colourants), V (preservatives) and VI (UV filters)
carry `status == "allowed_with_conditions"` and `annex` of `Annex IV` / `Annex V`
/ `Annex VI`. They are **conditional allowances**, not an OK / clean / compliant
determination:

* `conditions` / `raw.conditions_list` preserve the source's product-type,
  concentration, other, and warning fragments **verbatim** and are **not**
  auto-satisfied.
* A substance present only in a positive list remains a **flagged / conditional
  finding** (the linter's `flagged` set includes `allowed_with_conditions`);
  it is never reported as `clean`.
* Presence is not a general positive-list compliance engine, and **absence from
  a list is not a prohibition** (a raw ingredient name does not establish its
  intended function, scope, or exceptions).
