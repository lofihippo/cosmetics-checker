# Product scope: cosmetic ingredient regulatory screening

Regulatory information, not legal advice.

## Purpose

Help a person understand matches between a cosmetic's listed ingredients and
covered EU prohibitions, restrictions and conditional uses. Present the source
evidence and unresolved context. The result is not a personal safety assessment,
a full product compliance determination or a laboratory test.

## Current coverage

- CosIng Annex II–VI: prohibitions, restrictions and conditional positive lists.
- REACH Annex XVII entry 70: the explicitly supported D4/D5/D6 cosmetic branches,
  enabled only when the loaded snapshot has the accepted source mapping.
- Formula evaluation: a bounded set of mappings requiring explicit concentration,
  unit, basis, product context and any required assessment date.

The loaded snapshot defines actual coverage. Historical bundles may be narrower.
Entry 70 is not full Annex XVII or REACH coverage. ECHA exports, SVHC information
and Annex XIV remain excluded under the current acquisition/release policy.

## Cosmetics versus food

The production flow is cosmetics-focused. Food examples in the historical
prototype do not constitute a validated food-regulatory engine. Explicit
non-cosmetic products are rejected by the scanner. Cosmetic findings must not be
presented as food-use determinations or as a US-versus-EU legality comparison.

## Evidence and interpretation

Keep separate:

- An unambiguous prohibition finding.
- A scoped or conditional rule whose applicability remains unresolved.
- A supported numeric comparison based on sufficient supplied context.
- Unmatched identities, ambiguous resolution and missing information.

Every flag carries a regulation reference, source URL and snapshot
`data_updated_at`. Source publication dates and retrieval times remain separate.
Official entry numbers are distinct from file-row locators. Preserve source
exceptions, direct rules and group-linked evidence across every interface.

A rule match alone does not establish a violation. Unknown and conditional
allowance do not mean unrestricted or dangerous. A within-threshold comparison
does not resolve other requirements. Do not infer concentration, use, exposure
or undeclared contaminants from a label.

## Source and reuse boundaries

Use official exports or retained official legal documents with recorded hashes,
locators, attribution and applicable reuse conditions. Preserve the accepted
snapshot while validating candidates. Recheck amendments and corrigenda before
claiming a refreshed legal mapping.

ECHA acquisition is disabled. A possible future expansion to cosmetics-relevant
Annex XVII entries, separate SVHC information or useful Annex XIV context does
not itself authorize portal-term acceptance, acquisition or redistribution.
Establish the source/reuse path and a bounded implementation before changing the
release policy. Keep any SVHC listing distinct from a cosmetic prohibition.

Original code is MIT; upstream data and dependencies retain their own terms.
See [LICENSES.md](../LICENSES.md) and [methodology](methodology.md).

## Interface and release boundaries

The CLI, API, reports, playground, formula editor and scanner share backend
interpretation. No separate browser regulatory engine, universal safety score,
accounts, payments or recommendations engine are part of v1. Public hosting,
physical-device acceptance and hosted snapshot publication require separate
validation; their absence must not be hidden by a successful local preview.
