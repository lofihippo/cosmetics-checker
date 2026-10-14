# Product scope: cosmetic ingredient regulatory screening

Regulatory information, not legal advice.

## Approved direction — October 13, 2026

Help a person check a cosmetic's listed ingredients, understand matches to covered EU prohibitions, restrictions and other regulatory concerns, and see the evidence and limitations. Do not present the result as a personal safety assessment, a full product compliance determination or a laboratory test.

The user approved expanding beyond the current CosIng-only snapshot in this order:

1. **Cosmetics-relevant REACH Annex XVII restrictions.** Prioritize entries with a demonstrable connection to cosmetic ingredients or uses. Identify substance/group, affected use, relevant concentration or physical-form criteria, exceptions and effective/transition dates. A matched substance is not automatically an applicable restriction or an established violation.
2. **A separate SVHC information section, where a permitted source/reuse path is established.** Show Candidate List membership, reason for inclusion, inclusion date and source evidence. Keep it distinct from cosmetic prohibitions and concentration violations. Explain whether the concern relates to human health, the environment or both when the source supports that distinction. Do not invent a consumer risk score.
3. **Annex XIV where useful.** Evaluate its incremental value for the supported cosmetic use cases. Add contextual authorisation information only with source-backed applicability and exemptions. Do not classify membership alone as a cosmetic ban. If it adds no actionable value for the selected consumer cases, document the reason for deferring it.

These are approved work targets, **not implemented coverage**. Current runtime policy still excludes ECHA data. Existing `not_included` reporting must remain truthful until an eligible source is implemented, validated and deliberately enabled.

## Cosmetics versus food

The production release remains cosmetics-focused. The historical prototype contains some curated food examples; these do not constitute a validated food-regulatory engine. Do not apply cosmetic restrictions to foods or infer that a cosmetic result establishes anything about food use.

Retain the scanner's explicit non-cosmetic rejection. Food coverage is a possible separate future scope decision requiring appropriate official food sources, licensing, matching and use-specific evaluation. No general food expansion is authorized by this scope update.

## Acquisition and reuse

Scope approval is not permission to accept portal terms, scrape websites or redistribute material whose reuse rights have not been established. Preserve existing ECHA acquisition and distribution guards while evaluating the source path. No ECHA bulk export is to be acquired under this planning update.

For Annex XVII/XIV, assess official EUR-Lex legislation/downloads as a possible primary source, including current amendments and effective dates. This is an independent sourcing option, not permission to repackage ECHA-derived data under a different source label. EUR-Lex annexes are not a substitute for the SVHC Candidate List.

Before enabling a source, record its exact documents/exports, applicable terms, attribution, commercial API and snapshot redistribution implications, and any unresolved third-party rights. Original code remains MIT; upstream data and dependency licenses remain separate. If permission is necessary, prepare the concrete request/use description; do not send it or accept terms without authorization.

Useful official starting points (verify their current contents during implementation):

- [EUR-Lex reuse notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html?locale=en)
- [ECHA legal notice](https://echa.europa.eu/legal-notice)
- [Commission REACH authorisation overview](https://single-market-economy.ec.europa.eu/sectors/chemicals/reach/authorisation_en)

## User-facing evidence contract

Keep separate:

- A prohibition entry match, with scope and exceptions.
- A restriction/conditional-use entry match whose applicability or compliance is not yet evaluated.
- A supported violation comparison based on sufficient supplied context.
- SVHC listing information and its source-backed reason.
- Authorisation context, if implemented.
- Unmatched identities, unresolved scope and insufficient information.

A flag must include regulation reference, source URL and snapshot `data_updated_at`. Preserve retrieval/publication dates separately from snapshot time. A future restriction must not appear as currently effective; historical checks must identify their assessment date. Product-category, concentration, form or use information that is absent stays unknown, not assumed. Surface linked/group evidence without collapsing distinct identities.

Apply the same interpretation in the library, ordinary label/scanner path, formula evaluator, API and reporters. In particular, reconcile existing label-path headlines with scoped/cross-annex exceptions before presenting a definitive prohibition to users. Labels alone do not establish concentration, exposure or undeclared contaminants. Neither unknown nor a conditional allowance means dangerous or unrestricted.

## Bounded delivery order

1. Inventory cosmetics-relevant Annex XVII candidates and source/reuse paths. Deliver a small prioritised matrix with use relevance, overlap with existing coverage, required context, dates, rights status and expected user benefit. Assess SVHC sourcing and Annex XIV usefulness alongside it.
2. Once a usable source path is established, implement **one** relevant Annex XVII entry end to end using the shared engine, isolated candidate snapshot and exact citations. Demonstrate applicable, non-applicable, missing-context and transition-date cases. Do not build speculative generic parsers before seeing the actual official source.
3. Expand the validated pattern to the selected restrictions; add the separately labelled SVHC section if its source path is permitted. Add Annex XIV only when the relevance assessment supports it.
4. Preserve local release/device preparation and complete later hosted/device acceptance under the existing authorization boundaries. Regulatory expansion does not authorize deployment, purchases or portal acceptance.

Keep a recoverable accepted snapshot and verify that existing CosIng evidence survives additions. Coverage claims and release policy change only when the corresponding capability is actually ready. No second checking engine, broad framework migration, universal safety score, accounts, payments or recommendations engine.

## Completion and estimates

The earlier CosIng-only completion forecast does not include this added regulatory work. Re-estimate the new milestone after the source/reuse and applicability inventory; do not promise full REACH coverage or full EU safety assessment from this addition.

## Annex XVII / SVHC / Annex XIV source assessment — 2026-10-13

**Independent review correction, October 13:** The following assessment is retained as a historical record of the agent's attempted routes, not a current blanket acquisition blocker. A public official-government-hosted OJ PDF of Regulation 2024/1328 was subsequently downloaded successfully (entry 70, D4/D5/D6). Source currency, amendments and reuse still need verification before ingestion. The earlier candidate matrix also has incorrect entry mappings; it must not drive implementation without correction. Backend scope counts were improved, but prominent playground/scanner interpretation still needs completion. See `.local-reference/progress-independent-annex-xvii-2026-10-13.md` for independent evidence and the next bounded milestone. No new REACH coverage is currently implemented.

Part A source-and-applicability inventory and the Part C single-entry implementation were assessed. **No Annex XVII entry was implemented end to end** because no eligible official source could be acquired without bypassing an access control (see blocker below). The audit findings:

- **Annex XVII (REACH restrictions)** — the authoritative EUR-Lex legal-text endpoints
  (`/legal-content/EN/TXT/HTML|XML/?uri=CELEX:02006R1907-...`) return **HTTP 202 with an AWS WAF JavaScript challenge** (an access control). The independent Publications Office (`publications.europa.eu/resource/...`, CELLAR/ELI resource URIs) returned `404`/`400` for the consolidated REACH act, and the reachable CELLAR SPARQL endpoint exposes the procurement/tender graph, not the REACH legal text. Retrieving Annex XVII would require solving/bypassing the WAF — prohibited under the boundaries (no scraping or bypassing access controls). There is **no existing REACH Annex XVII data** in the repository (the only EUR-Lex file is the cosmetics Regulation 1223/2009). ECHA's `echa_annex_xvii_restrictions.csv` returns **HTTP 403** and is under the deferred-ECHA guard (AGENTS.md #9), so it is not an eligible path.
- **SVHC Candidate List** — no permitted source path established without accepting ECHA portal terms / acquiring a bulk export. Remains deferred and `not_included`; not implemented.
- **Annex XIV (authorisation)** — assessed as low incremental value for the supported cosmetic label cases (authorisation is a supply-chain/in-company obligation, not a label-derivable restriction, and requires source-backed applicability/exemptions). Reasoned deferral, not implemented.

Part B (consistent user interpretation) was implemented and verified — see `docs/methodology.md` and the shared `eu_reg_lists/checker.py`/`reporter` changes. It does not add any Annex XVII or ECHA coverage.

### Concrete acquisition blocker (Annex XVII)

> **Updated 2026-10-13 (this milestone):** The blocker is substantially resolved
> for **entry 70 only** via an independent, public official-government-hosted OJ
> PDF of Commission Regulation (EU) 2024/1328 (CELEX 32024R1328). The earlier
> blanket "no eligible official source" conclusion is superseded. See
> `.local-reference/corrected-annex-xvii-inventory-2026-10-13.md` and
> `docs/methodology.md`. Entry 70's cosmetic branches are now implemented as a
> source-bound evaluator; the accepted snapshot is preserved and no ECHA export
> was enabled. Full Annex XVII / REACH coverage is NOT implemented.

The above historical "no Annex XVII entry was implemented" text and the two
options below predate the entry-70 source verification and are retained as a
historical record of the EUR-Lex WAF/ECHA route assessment, not as the current
state for entry 70.

For implementing the FIRST cosmetics-relevant Annex XVII entry end to end, one
of the following must be resolved (each requires external authorization/access):

1. **A machine-readable, non-WAF EUR-Lex/Publications-Office resource** for the
   consolidated REACH Act (Annex XVII) whose reuse terms are confirmed for
   access, normalisation, snapshots and derived-evidence redistribution; or
2. **An officially licensed/downloadable Annex XVII dataset** (e.g. a validated
   ECHA or Commission export) with an established reuse basis and explicit
   authorization to leave the deferred-ECHA guard — currently not granted.

**Resolved for entry 70:** an independent public official OJ PDF (Regulation
2024/1328) was retained and verified (SHA-256
`2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966`), with the
canonical act identifier (CELEX/ELI) recorded separately from the retrieval URL.
EU public-sector reuse with attribution is the documented basis. Entry 70's
cosmetic branches are implemented; the consolidated entry-70 text must be
re-verified for later amendments before a subsequent refresh (recorded as a
consolidation-check obligation).

Until a general Annex XVII source is established, entry 70 is the only
Annex XVII entry covered; other entries remain `not_included`/unsupported and
are never presented as available or clean. No speculative Annex XVII parser was
built, and no ECHA source was enabled.

