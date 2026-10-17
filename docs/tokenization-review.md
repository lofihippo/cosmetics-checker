# Label-tokenization regression evidence

Regulatory information, not legal advice.

Results use the retained curated seed (`data/eu_flags.json`), not newly acquired regulatory data. Tokens below are separated with ` · `. The original tokenizer was captured before edits.

| Input | Before | After | Banned findings before → after |
|---|---|---|---|
| `Aqua, Parfum (Lilial), Glycerin` | `aqua · parfum · glycerin` | `aqua · parfum · lilial · glycerin` | 0 → 1 |
| `Aqua (Water), Fragrance (contains Zinc Pyrithione)` | `aqua · fragrance` | `aqua (water) · water · fragrance · zinc pyrithione` | 0 → 1 |
| `Camellia Sinensis (Leaf, Root) Extract, Glycerin` | `camellia sinensis (leaf · root) extract · glycerin` | `camellia sinensis extract · glycerin` | 0 → 0 |
| `Acrylates/C10-30 Alkyl Acrylate Crosspolymer` | `acrylates · c10-30 alkyl acrylate crosspolymer` | `acrylates · c10-30 alkyl acrylate crosspolymer` | 0 → 0 |
| `Ammonium Acryloyldimethyltaurate/VP Copolymer` | `ammonium acryloyldimethyltaurate · vp copolymer` | `ammonium acryloyldimethyltaurate · vp copolymer` | 0 → 0 |
| `Aqua, Glycerin. May contain [+/-]: CI 77491, CI 77891` | `aqua · glycerin. may contain [+ · -]: ci 77491 · ci 77891` | `aqua · glycerin · ci 77491 · ci 77891` | 0 → 0 |
| `Water (Aqua (Eau)), Glycerin` | `water) · glycerin` | `water · aqua · eau · glycerin` | 0 → 0 |

The seed does **not** contain either polymer name. The required unknown-slash fallback therefore still splits those two rows. With each exact polymer name supplied as a registry phrase, the new tokenizer returns the complete name as one token; the old tokenizer splits it. Both cases are covered in the regression tests. No polymer identity or regulatory determination was fabricated.

The seed recognizes `aqua (water)` as a full alias and `water`, `aqua`, and `eau` as names/aliases. Full bracketed identities are retained, and resolved inner candidates are shown separately with `derived_from`; unrecognized supplementary descriptors are omitted from counts.

Local timing for a 10,000-character repeated label and 3,000 comma-containing protected phrases: approximately **0.90 s before**, **0.013 s after** (single-run comparison, not a cross-machine guarantee). A 2,000-level nested label is also covered without Python recursion.

No existing test assertions were changed. The saved OpenAPI schema was regenerated for the additive `derived_from` field. The historical Phase-0 prototype is explicitly deprecated and retains its legacy tokenizer.
