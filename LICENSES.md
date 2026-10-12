# LICENSES.md

This repository documents the license of **code** and of each **data source**.

---

## Code

The software in this repository (including `eu_lint.py`, `app.py`, and the
`tests/` directory) is licensed under the **MIT License**. See `LICENSE`.

### Vendored browser dependencies (scan-to-check photo OCR)

The photo-OCR flow vendors pinned third-party browser assets locally so
recognition runs without a runtime CDN. These files are **not** MIT-licensed and
carry their own licenses:

- **Tesseract.js** `6.0.1` (`static/vendor/tesseract/tesseract.min.js`,
  `worker.min.js`) — **Apache-2.0**. Apache-2.0 requires preservation of the
  NOTICE/license text; the bundled `tesseract.min.js.LICENSE.txt`,
  `worker.min.js.LICENSE.txt` and `LICENSE-APACHE-2.0.txt` are included with the
  source and the built wheel.
- **tesseract.js-core** `6.0.0` (`static/vendor/tesseract/core/*.wasm.js`) —
  **Apache-2.0** (a compiled Emscripten build of **Tesseract OCR**, which itself
  is Apache-2.0). The matching `core/LICENSE-APACHE-2.0.txt` is bundled.
- **eng.traineddata** (`@tesseract.js-data/eng` **package** `1.0.0`, Tesseract
  OCR **model variant** `4.0.0`) — **Apache-2.0** under the Tesseract OCR
  project. The `eng.traineddata.gz` is the `4.0.0` LSTM model variant; the data
  **package** version is `1.0.0`. These two numbers are intentionally distinct
  (the npm package version versus the bundled Tesseract model variant).

The exact versions, upstream artifact URLs, models and SHA-256 values for every
vendored file are recorded in
`static/vendor/tesseract/vendor-manifest.json`. The reproducible
acquisition/verification command is `tools/vendor_tesseract.py` (`--verify-only`
checks the already-vendored tree without downloading). Redistribution must retain
the relevant Apache-2.0 license/NOTICE text.
Apache-2.0 license/NOTICE text.

---

## Data

The data shipped in this repository is **not** covered by the MIT license.
Data licenses vary by source and are documented below. This file will grow
each phase as the pipeline ingests official exports (Phase 1 onwards).

### EU CosIng (Cosmetic Ingredient Database)

- **Source:** European Commission CosIng Annex II–VI exports.
- **License:** EU public-sector reuse policy. Requires **attribution** to the
  European Commission and cites the source.
- **Status:** Used for cosmetic substance annex data. Phase 1 fetches the
  official export files (NOT scraped).

### ECHA (European Chemicals Agency)

- **Source:** SVHC Candidate List, Annex XIV (Authorisation), Annex XVII
  (Restriction) packages.
- **License:** ECHA's own terms of use. Must cite ECHA and follow its
  reproduction policy.
- **Status:** Used via official package downloads / export files. Systematic
  scraping is prohibited and is **not** used in this project.

### Open Beauty Facts / Open Food Facts

- **Source:** product database (Phase 4 barcode mode).
- **License:** Open Database License **ODbL v1.0** (share-alike +
  attribution).
- **Status:** Consumed via the public API for product ingredient labels in
  Phase 4.

### Food additive data (Regulation (EC) No 1333/2008)

- **Source:** EUR-Lex (EU law) — E-number authorisation details used for
  "food vs cosmetic" gaps.
- **License:** EU public-sector reuse; requires attribution.

### US FDA data (optional geographic overlays)

- **Source:** 21 CFR 189 / 21 CFR 172.
- **License:** US government work (public domain).
- **Status:** Optional overlay for the EU-vs-US gap matrix (Phase 1).

### California Proposition 65

- **Source:** State of California, OEHHA.
- **License:** Public state data; cite as source.
- **Status:** Optional overlay (Phase 1).

---

> **Attribution requirement:** Any redistribution of the combined dataset must
> retain attribution to the above sources and behave as a peer of ODbL /
> EU-reuse-licensed data, not as a plain MIT-licensed dataset. This is
> reinforced in `AGENTS.md` ground rule #5.
