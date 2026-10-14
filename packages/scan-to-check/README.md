# scan-to-check — local barcode → product ingredients → existing checker

Regulatory information, not legal advice.

A local cosmetic ingredient review interface: enter a barcode, retrieve a
community-supplied product label, review or edit the text, and run the shared
`eu_ingredient_linter.ingredient_report` checker. Manual text, camera barcode
input and editable photo OCR are available. The PWA manifest, service worker
and versioned shell cache are implemented; this is not a full offline checker.

The owner tried the desktop examples and an ingredient photo on October 14,
2026 and reported that most features worked. Physical-phone acceptance and
public hosting remain deferred. The local preview services are stopped; the
commands below restart them. Regulatory evidence and product provenance remain
separate, and ECHA coverage is excluded under the current release policy.

## Launch (local)

First install the package (editable, from the repo root). This is required — the
module is not importable from the checkout without installation:

```bash
.venv/bin/python -m pip install -e packages/eu-reg-lists
.venv/bin/python -m pip install -e packages/eu-ingredient-linter
.venv/bin/python -m pip install -e packages/scan-to-check
```

Or, from the repo root, run the shared build target:

```bash
make build
```

Then launch (from the repo root; the app loads the snapshot via
`eu_reg_lists.load`):

```bash
EU_REG_SNAPSHOT="$PWD/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/scan-to-check
```

Or equivalently after installation:

```bash
EU_REG_SNAPSHOT="$PWD/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/python -m scan_to_check.app
```

Open `http://127.0.0.1:8010/`.

### Verify installation from outside the checkout

The installed `scan-to-check` entry point and packaged `static/index.html` must
be resolvable from a non-repo working directory without any special
`PYTHONPATH`:

```bash
cd /tmp
EU_REG_SNAPSHOT="<abs path to data/processed/eu_reg_lists.sqlite>" \
  /path/to/.venv/bin/python -c "import scan_to_check, importlib.resources as r; \
assert r.files('scan_to_check').joinpath('static/index.html').is_file(); print('launch assets ok')"
```

The app reuses the shared `ingredient_report` engine — it does **not** duplicate
matching, tokenization or evaluation, and does not infer concentrations from
ingredient order.

## Behavior

- **Lookup** uses a fixed endpoint
  `https://world.openfoodfacts.org/api/v3/product/{barcode}?product_type=all`
  (bounded timeout/response, descriptive User-Agent). Barcodes are preserved as
  strings (leading zeros). Typed outcomes: found-with-ingredients,
  found-without-ingredients, not-found, unsupported-product-category (only
  `beauty` is checked with cosmetic rules; food/petfood/unknown are excluded),
  malformed response, upstream error. A transient failure never shows an old
  product.
- **Product provenance** (name/brand/barcode, source link, retrieval time, ODbL
  attribution) is kept separate from the **regulatory snapshot** provenance
  (`data_updated_at`). A product entry is community-supplied ingredient evidence,
  not a verified compliance finding.
- **Edit before checking.** The ingredient text is editable; manual paste and
  photo OCR are the fallbacks. Submit the exact reviewed text to `/api/check`,
  which runs the shared engine.
- **Input mode vs product category.** The frontend sends an explicit `mode`
  (`manual` / `ocr` / `lookup`) that describes *how* the text was obtained, kept
  separate from the fetched `product_type` (only ever `beauty` for a checked
  cosmetic). A manual/OCR draft with no fetched product category IS reviewable as
  cosmetic text; an explicit food/petfood/other product category is still rejected
  at `/api/check` so cosmetic rules are never applied to a non-cosmetic.
- **Photo OCR (Tesseract.js, local).** Choose a JPEG/PNG ingredient-label image → it
  is previewed and recognized **locally** by the vendored, **pinned** Tesseract.js
  `v6.0.1` (+ `tesseract.js-core 6.0.0`, `eng` traineddata) under
  `/static/vendor/tesseract/` — no runtime CDN, no image upload, no analytics. The
  recognized text is an **editable draft only**: it is never silently submitted or
  fuzzy-corrected; the user reviews/edits it and then explicitly presses **Check.**
  Bounded input: JPEG/PNG only, ≤ 10 MB, decoded dimensions ≤ 6000×6000;
  corrupt/unsupported/oversized images are rejected clearly (no PDF work).

  A single OCR lifecycle controller (`static/ocr.js`) owns image selection and
  validation, the preview and the recognition job under ONE selection identity.
  Every file change (including an empty/invalid/oversized replacement) retires the
  prior selection/recognition, so a stale completion can never populate a newer
  draft or enable a newer invalid file. Pending worker initialization and the
  resolved worker are owned explicitly: at most one initialization and one
  recognition run at a time, and a late-created worker (from a cancelled/timed-out
  attempt) is disposed without replacing the current owner. Real worker/core/model
  load failures (e.g. a traineddata 404) settle quickly via Tesseract's
  `errorHandler` and fall back to a bounded 30 s timeout, surfacing an actionable
  message with manual fallback + retry and never an uncaught error or an orphan
  worker stack. Empty recognition is reported as **"No text found"** (never a
  successful ingredient extraction). The worker is created lazily and REUSED for a
  retry within a session, then disposed on any session change. Object URLs are
  always released. The Tesseract language model is **not** cached in IndexedDB
  (`cacheMethod: 'none'`); see the caching note below.
- **Honest semantics.** Unknown/unmatched stays unknown; not-in-dataset is
  reported as not clean; no clean/safe/legal certificate. Direct + linked rules,
  conditions, scope, citations, references and snapshot timestamp are shown. OCR
  provenance (`mode: "ocr"`) is separate from any product database provenance.
- **Request safety.** A monotonic request id invalidates stale lookups/checks and
  edits, so a delayed success/error can never overwrite newer state. The camera
  and OCR flows each use their own session token spanning acquisition/recognition
  and every continuation.
- **Accessibility.** Enter looks up; `Ctrl/Cmd+Enter` checks; the UI works on a
  narrow viewport.

## PWA shell (installable)

`scan-to-check` is an **installable PWA shell** served by the same FastAPI app:

- **Manifest** (`/manifest.webmanifest`, served at root scope) with a 192/512
  icon set (including a `maskable` variant), `start_url: /`, `scope: /`, and
  `display: standalone`. Install behavior is documented: after the backend is
  running, the browser offers "Install app".
- **Service worker** (`/sw.js`, served at `/` with `Service-Worker-Allowed: /` so
  its scope controls the whole app). It caches ONLY the application shell
  (index.html, `ocr.js`, the manifest and icons) in a single versioned cache
  (`scan-to-check-static-scan-to-check-shell-v1`). It **never** caches
  `/api/...` product/check/health responses, regulatory findings, user images or
  entered labels, and **never** precaches the ~27 MiB vendored Tesseract vendor
  tree (OCR needs connectivity to load its assets for this milestone). It only
  records the explicit shell route (`/`) on navigation and never writes arbitrary
  non-shell pages (e.g. `/docs`, JSON, errors) as the offline home. On
  `activate`, only obsolete caches **owned by this app's namespace** are deleted;
  unrelated same-origin caches are preserved.
- **Update flow.** A pending update is surfaced as a non-blocking banner and
  stays **waiting** until the user clicks Reload (the worker does NOT call
  `skipWaiting()` on install). The app registers its reload intent *before*
  posting the activation message, so the `controllerchange` always triggers a
  real navigation; it handles first install, an already-waiting registration and
  the already-active activation race. The in-progress draft is preserved until the
  user accepts, and no stale event forces a surprise reload. The app asset version
  (the SW cache version) is **independent** of the regulatory snapshot
  `data_updated_at`.
- **Honest offline behavior.** The shell and manual/OCR editing remain usable
  offline, but product lookup and regulatory checking still need the Python
  backend. When the backend is unreachable the UI reports **backend unavailable**
  and never fabricates a finding or a ready status, and no cached API response is
  replayed as current. This is **not** a full offline regulatory checker and no
  physical-device installation is claimed by the tests.

## Attribution & license

Product data comes from **Open Beauty Facts / Open Food Facts**, licensed under
the **Open Database License (ODbL) 1.0**. The UI links to the source product and
to the data license and attributes the project. Attribution is **rendered by
mode**: a **lookup** product shows its real Open Food/Beauty Facts attribution,
while **manual/OCR** input shows a local-input note (it must NOT claim its data
came from Open Food/Beauty Facts). A separate general project/database credit
remains. See `LICENSES.md`. The product database is a separate data source from
the EU regulatory registry (CosIng/ECHA), which has its own licenses. Product
data is not persisted by default.

### Browser persistence / caching note

- The application **does not** persist user-selected images, entered ingredient
  labels, or OCR results.
- The PWA service worker caches a **static application shell** (versioned) so the
  app opens offline; it does **not** cache any API/regulatory response or user
  content.
- Tesseract's default browser behavior can cache the language model in
  **IndexedDB**; this app explicitly disables that (`cacheMethod: 'none'`), so no
  traineddata is written to browser storage. Language-model caching is therefore
  distinct from image/text persistence: neither is done here.

### Vendored OCR assets

The photo-OCR flow vendors pinned Tesseract.js assets **locally** so no runtime
CDN is needed. Exact versions, upstream artifact URLs and SHA-256 values are
recorded in `static/vendor/tesseract/vendor-manifest.json`; the matching
distribution notices are bundled. Versions and licenses are also recorded in
`LICENSES.md`:

| Asset | Version | License | Source |
|---|---|---|---|
| `tesseract.min.js`, `worker.min.js` | 6.0.1 | Apache-2.0 | `npm tesseract.js@6.0.1` |
| `core/*.wasm.js` | 6.0.0 | Apache-2.0 | `npm tesseract.js-core@6.0.0` |
| `eng.traineddata.gz` | 4.0.0 (model variant) | Apache-2.0 | `@tesseract.js-data/eng@1.0.0` |

These are served at `/static/vendor/tesseract/`. The worker/core are loaded from
the vendored directory (`workerPath`, `corePath`, `langPath` all point at the
local `/static/vendor/tesseract/` tree). The acquisition/verification command is
`tools/vendor_tesseract.py` (download + hash-check, or `--verify-only`). The real
engine is driven against a generated readable label PNG in the browser tests (a
fixture demonstration, distinct from a physical-device or hosted run).

> **Why v6.0.1?** v5.1.1 was upgraded to v6.0.1 because upstream v6 fixed a
> long-standing memory-growth issue and reduced default output work (only `text`
> is requested — no unused blocks/HOCR/TSV). This is a real dependency upgrade on
> the evidence of the upstream release; it is NOT a claim that upgrading fixes the
> app's lifecycle bugs (those are owned by `ocr.js` and are tested separately).

## Tests

```bash
.venv/bin/python -m pytest packages/scan-to-check/tests/ -q
```

These exercise the real app with an injectable lookup client (no network) plus
offline tests of the real client's parsing, and the real shared evaluation engine
against the loaded snapshot. A one-off live demonstration against `3560070791460`
was performed locally.
