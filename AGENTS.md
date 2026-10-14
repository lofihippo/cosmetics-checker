# AGENTS.md

1. Never claim "legal" or "safe". A clean result = no EU ban/restriction
   triggered. It is NOT a safety certificate and NOT legal advice.
2. Every flag must include: regulation reference, source URL,
   `data_updated_at`.
3. "Not in dataset" is NOT "clean". Use NULL semantics; never default to OK.
4. Do not scrape ECHA or official sites. Use official export files only.
5. Code is MIT. Data licenses vary (CosIng = EU reuse/attribution, Open Beauty
   Facts = ODbL share-alike + attribution, ECHA = its terms).
6. Prefer explicit alias maps and recorded resolution methods over fuzzy
   guessing.
7. All user-facing text includes "Regulatory information, not legal advice."
8. Keep v1 scope: no accounts, no payments, no recommendations engine.
9. **ECHA is deferred in the current runtime/release.** Do not accept ECHA's portal terms,
   obtain or ingest ECHA exports, or contact ECHA on a normal refresh (ECHA
   sources are `enabled: false`). Surface ECHA as not-included/missing — never
   clean. The approved roadmap now includes cosmetics-relevant REACH Annex XVII,
   separate SVHC information where reuse permits, and Annex XIV where useful;
   see `docs/product-scope.md`. This roadmap does not itself enable acquisition,
   acceptance of terms or distribution. Preserve the current guards until the
   source/reuse path and an explicitly scoped implementation are established.
