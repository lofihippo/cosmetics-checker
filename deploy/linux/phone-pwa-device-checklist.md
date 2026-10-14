# Phone / PWA preview preparation (preview only, no device acceptance)

Regulatory information, not legal advice.

This prepares a way to preview the `scan-to-check` PWA on a phone over HTTPS.
It is **preparation only**: no device has been exercised, no camera/install/
update/OCR/edit/check/cancel/offline behavior has been run on hardware, and no
OS/device certificate trust was changed. The local TLS validation uses a
**temporary CA** supplied explicitly and keeps the listener loopback-only.

## What the PWA needs

- A **secure context** (HTTPS, or `localhost`/loopback as the browser
  exception) for `navigator.serviceWorker` registration, `camera.getUserMedia`,
  and PWA install/update. A plain `http://` origin on a phone is **not** a
  secure context, so the service worker and camera will not work.
- The `scan-to-check` origin must serve `/`, `/sw.js`, `/manifest.webmanifest`,
  and `/static/**`. Its service worker has scope `/`, so it needs the origin
  root (it intentionally does NOT intercept `/api/**` or the OCR vendor tree).
- OCR assets (`/static/vendor/tesseract/**`, ~27 MiB) are served from the
  installed wheel — no runtime CDN. Loading them needs connectivity; the shell
  + manual editing remain available offline.

## Reference topology (same as the public-host reference)

```text
phone --(HTTPS, trusted cert)--> reverse proxy (TLS) --> scan-to-check (8010)
                                                      --> eu-reg-api   (8000)
```

Use the **same** reverse proxy/TLS configuration as `deploy/linux/` — one proxy
that terminates TLS and routes `scan.EXAMPLE` -> 8010 and `api.EXAMPLE` -> 8000.
Keep the two origins separate so the scanner service worker's root scope does
not overlap the API playground.

## Local HTTPS validation (already run)

The proxy/TLS config and both origins were exercised locally with an explicit
temporary CA on the loopback interface (no machine trust change):

```bash
# Build the Linux kit, then run the loopback HTTPS validation:
RELEASE_KIT=/tmp/linux-out/release-kit deploy/linux/validate_local_tls.sh
```

That validates: secure HTTPS on both origins, `scan /sw.js` (root-scoped service
worker), `scan /manifest.webmanifest`, the label/check endpoints, the ECHA
not-included `404`, the `422` payload-shape error, and unchanged snapshot bytes.

## Exact commands for the phone step (later, with your device)

These run only on a real device against a real, trusted HTTPS origin after the
host/domain decisions are made. They are provided now so the later step is
exact, but they are **not** executed here.

1. From the phone browser, open `https://scan.EXAMPLE/`.
2. The page will offer to install the PWA (browser menu -> "Add to Home screen"
   / "Install app"). Confirm the PWA meets install criteria (`/manifest`,
   `/sw.js`, icons).
3. Allow camera access when the scan flow asks; deny it in a separate pass to
   confirm the app degrades with a clear message and never blocks.
4. Scan a real product barcode (or upload a label photo) -> OCR -> review/edit
   the ingredient list -> tap the explicit check.
5. Test cancellation mid-scan/OCR (no stalled/hung state).
6. Enable airplane mode / disable the network -> confirm a clear offline/error
   message; never a false "clean" result.
7. On a later deploy, reload the PWA and accept the update; confirm the new
   content appears after the user-triggered reload (the service worker does not
   `skipWaiting()` on install).

## Device checklist (recorded as required input, not assumed)

Record these, then run the checklist. Every result stays **unrun** until done.

| Item | Value (record it) |
|---|---|
| Device 1 OS / version | (e.g. iOS 18.x / Android 15) |
| Device 1 browser | (e.g. Safari / Chrome) |
| Device 1 camera works | (confirm a real camera + permission) |
| Device 2 (if any) | (OS / browser / camera) |
| Host / domain | (the HTTPS origin that will serve `scan.EXAMPLE`) |
| e.g. region | (for data-redistribution + latency) |

## Device acceptance checklist (all UNRUN)

| Check | Requirement | Result |
|---|---|---|
| HTTPS access | both origins served over valid TLS from a trusted cert | **unrun** |
| PWA install | `scan-to-check` installable from the browser menu | **unrun** |
| PWA update | service worker updates and new content appears after deploy | **unrun** |
| Permission denial | deny camera; app degrades with a clear message | **unrun** |
| Real barcode scan | camera reads a real barcode -> ingredients | **unrun** |
| Photo OCR + edit | snapshot/edit ingredients after OCR; then explicit check | **unrun** |
| Cancellation | user cancels scan/OCR; no stalled/hung state | **unrun** |
| Honest offline error | no network: clear offline/errors; never a false clean result | **unrun** |
| ECHA exclusion | every surface reports ECHA not-included/gap, never clean | **unrun** |

## Required user decisions before the real device step

1. **Available phone/browser** (OS + browser + camera) to run the checklist on.
2. **Intended host/provider or existing server** for the two services.
3. **Region / domain / budget** (CosIng is EU-reuse; Open Beauty Facts is ODbL
   share-alike; confirm the data may be redistributed there under the terms, and
   verify current provider pricing — do not assume a stale free tier).

None of these are performed in this preparation task.

Regulatory information, not legal advice.
