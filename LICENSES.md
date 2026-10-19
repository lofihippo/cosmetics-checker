# Licenses and attribution

## Original code

Original project code is licensed under [MIT](LICENSE). This does not relicense
upstream regulatory data, product records or vendored dependencies. Preserve the
applicable notices when distributing source, wheels, containers or snapshot bundles.

## Regulatory data

### European Commission CosIng

Cosmetics Checker uses official CosIng Annex II–VI exports, normalizing the
records and linking identities while retaining source references and hashes.
Attribution: European Commission, CosIng; the normalized data and assessments
are modifications made by this project, not Commission endorsements.

The [Commission copyright notice](https://commission.europa.eu/legal-notice_en)
permits reuse of EU-owned website content under CC BY 4.0 unless otherwise
indicated, with credit and an indication of changes. Preserve any export-specific
notices and third-party exceptions. [CosIng](https://single-market-economy.ec.europa.eu/sectors/cosmetics/cosmetic-ingredient-database_en)
is informational; an ingredient's presence is not an approval or safety finding.

### EU legal text: REACH entry 70

The supported cosmetic branches of REACH Annex XVII entry 70 use Commission
Regulation (EU) 2024/1328, CELEX 32024R1328, published in the Official Journal
on 17 May 2024. [Official act](http://data.europa.eu/eli/reg/2024/1328/oj).
Attribution: European Union / Official Journal. The bounded machine-readable
mapping and assessments are project adaptations.

Retained document SHA-256:
`2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966`.
Coverage requires the accepted document and compatible mapping in the snapshot.
This is an amending act; source refresh requires checking subsequent amendments
and corrigenda. It is not full REACH coverage and is not an ECHA export.
See the [EUR-Lex reuse notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html)
and any document-specific notices before redistribution.

### ECHA — excluded

ECHA SVHC, Annex XIV and Annex XVII exports are not included. Acquisition and
release guards remain disabled for these sources. ECHA has separate terms;
MIT and the Commission's general reuse policy do not authorize ECHA-data reuse.
The independent EU legal-act mapping above does not enable ECHA acquisition.

### Legacy prototype data

`data/eu_flags.json` contains curated prototype records, including some food
additive references to Regulation (EC) No 1333/2008. It is not the verified
production snapshot and does not establish food-screening support. Preserve
its source citations and the applicable EU source notices. FDA and Proposition
65 overlays are not claimed as implemented release datasets.

## Product data: Open Beauty Facts / Open Food Facts

Product labels are obtained through the provider API and remain distinct from
regulatory evidence. Keep the provider attribution, product link and retrieval
time with reused records. The database is under ODbL 1.0; individual database
contents are under the Database Contents License. Provider product images have
separate Creative Commons Attribution-ShareAlike terms and may contain other
protected elements. A user's own OCR upload does not become provider data.

See the provider's [license guidance](https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/license-be-on-the-legal-side/)
and [Open Beauty Facts terms](https://world.openbeautyfacts.org/terms-of-use).
ODbL attribution and applicable share-alike obligations concern reused data;
calling the API does not relicense this project's original code. This repository
does not distribute a full product database. Do not label a combined dataset MIT.

## Vendored OCR dependencies

Browser OCR assets are bundled under
`packages/scan-to-check/scan_to_check/static/vendor/tesseract/`:

| Component | Pinned version | License |
|---|---|---|
| Tesseract.js | 6.0.1 | Apache-2.0 |
| tesseract.js-core | 6.0.0 | Apache-2.0 |
| English traineddata (`@tesseract.js-data/eng`) | package 1.0.0, model variant 4.0.0 | Apache-2.0 |

The vendor directory includes the Apache license and bundled dependency notices
(`tesseract.min.js.LICENSE.txt`, `worker.min.js.LICENSE.txt` and the core license).
Retain these files, including notices for embedded third-party components.
`vendor-manifest.json` records exact upstream artifact URLs and SHA-256 hashes.
Verify the retained files without downloading:

```sh
python3 tools/vendor_tesseract.py --verify-only
```

Python runtime dependencies retain their own licenses in the installed packages
and retained wheels. `deploy/runtime.lock` pins release-kit runtime versions;
the generated release manifest records wheel hashes. Container base images and
Caddy also retain their upstream licenses. Keep these notices when redistributing
an assembled kit or image; this file is not a replacement for their full terms.
