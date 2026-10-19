# scan-to-check

Review cosmetic ingredient labels from a barcode lookup, a photo or manual
entry, then submit them to the shared checker.

## Launch

Follow the [root setup guide](../../README.md#run-locally) to install the packages
and restore a verified snapshot, then run:

```bash
EU_REG_SNAPSHOT="$PWD/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/scan-to-check
```

Open `http://127.0.0.1:8010/`. Set `PORT` to change the port.
`python -m scan_to_check.app` is the equivalent module entry point.

## Input methods

- **Barcode lookup:** retrieves a community product record from
  `https://world.openfoodfacts.org/api/v3/product/{barcode}?product_type=all`.
  Leading zeros are preserved. Cosmetic products can be checked; food and other
  categories are rejected. Missing labels and lookup failures allow manual input.
- **Camera:** uses the browser's `BarcodeDetector` when available. Frames are
  processed locally. Manual barcode entry remains available.
- **Photo OCR:** accepts JPEG/PNG up to 10 MB and 6000 × 6000 pixels. Tesseract
  runs locally; images are not uploaded. Review and correct the extracted text
  before submitting. Empty recognition and engine failures offer retry/manual
  entry; engine initialization times out after 30 seconds.
- **Manual:** paste or type a label. Enter submits a barcode lookup;
  Ctrl/Cmd+Enter checks ingredients.

Edits and mode changes invalidate pending results. OCR and camera sessions
release their resources when replaced or cancelled. Parenthetical aliases
appear on one result card with their source label and additional matched names.

## `/api/check`

The endpoint passes reviewed text to `ingredient_report`. It reads at most
200,000 raw bytes before JSON decoding, regardless of Content-Length. JSON
nesting is limited to 32 levels and ingredient text to 10,000 characters.
Malformed JSON, excessive nesting and non-finite numbers return
`422 invalid_json`; oversized bodies return `422 request_too_large`.

The body must be an object containing `ingredients`. Optional `product` is an
object or null. Its `mode` is `manual`, `ocr`, `lookup` or null; `product_type`
is `beauty`, `cosmetic` or null. Missing category is allowed for manual/OCR text.
Other explicit categories and malformed field types are rejected.

## Coverage and attribution

Product names, brands, label text and retrieval times come from the community
product database. Regulatory findings use the loaded EU snapshot, with their
own citations and `data_updated_at`. See [product scope](../../docs/product-scope.md)
for coverage and unresolved cases.

Lookup results link to Open Food Facts / Open Beauty Facts and the ODbL 1.0
license. Manual and OCR text are identified as local input. Code is MIT;
[data and dependency licenses](../../LICENSES.md) remain separate.

Regulatory information, not legal advice.

## PWA and storage

The manifest supplies standalone display and 192/512-pixel icons. The service
worker caches a versioned app shell: the page, application scripts/styles,
manifest and icons. It excludes API responses, user labels, images and OCR
vendor assets. It removes only obsolete caches belonging to this app.

An update waits for the user to choose Reload. The shell version is independent
of the regulatory snapshot date. Offline, a cached page can open and accept
edits, but lookup, checking and initial OCR asset loading require the running
backend. The app reports backend unavailability instead of replaying findings.

Images, entered labels and OCR text are not persisted. Tesseract language-model
storage in IndexedDB is disabled with `cacheMethod: 'none'`. Physical-device
acceptance and public hosting remain pending.

## OCR assets and headers

Tesseract.js 6.0.1, core 6.0.0 and the English model are served from
`static/vendor/tesseract/`. Versions, source URLs, SHA-256 hashes and licenses
are recorded in `vendor-manifest.json` and [LICENSES.md](../../LICENSES.md).
Use `tools/vendor_tesseract.py --verify-only` to verify the retained files.
See [architecture](../../docs/architecture.md#browser-lifecycle-ocr-and-pwa)
for worker ownership and the version-specific failure adapter.

HTML uses `default-src 'self'` and `frame-ancestors 'none'`. OCR allows
`'wasm-unsafe-eval'`, local/blob workers and connections; image previews allow
local, blob and data URLs. Scripts and styles are packaged files.
Responses include `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer`. Built-in documentation routes are exempt from
CSP for their CDN assets and inline bootstraps.

## Tests

```bash
.venv/bin/python -m pytest packages/scan-to-check/tests/ -q
```

Tests use fixture registries and an injectable product client. Browser tests
require system Chrome and cover request races, camera/OCR lifecycle, actual
vendored OCR, result evidence and PWA updates. For package changes, also verify
installed wheels outside the checkout as described in [architecture](../../docs/architecture.md#maintenance).
