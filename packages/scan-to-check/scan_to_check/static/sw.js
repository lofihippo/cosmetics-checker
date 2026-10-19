/* Service worker for the scan-to-check PWA shell.
 *
 *
 * This service worker caches ONLY the application SHELL (the HTML, the OCR
 * controller, the small app JS/CSS and the PWA manifest/icons). It deliberately
 * does NOT cache:
 *   - any /api/... product/check/health response;
 *   - any regulatory findings or raw/processed snapshot data;
 *   - any user image or entered ingredient label.
 * Product lookup and regulatory checking remain online dependencies of the
 * Python backend. When the backend is unavailable, the shell/manual editing may
 * still work but checks/lookups must surface a clear backend-unavailable error
 * and NEVER replay a cached API response as current. No cached response is ever
 * presented as a current finding.
 *
 * OCR assets (the ~27 MiB vendored Tesseract vendor tree) are NOT precached
 * here: for this milestone OCR requires connectivity to load its assets from
 * the server. The shell + manual editing remain available offline.
 *
 * Cache policy:
 *   - ONE release = ONE cache identity. The worker below is served by the app
 *     with the placeholder replaced by a content hash of the concrete shell
 *     assets (HTML, OCR JS, manifest, icons) plus this service-worker logic. So
 *     any release change yields a DISTINCT cache name, and a newly installed
 *     worker always precaches into its own cache -- it can never overwrite the
 *     active worker's cache before consent. On activation we delete ONLY caches
 *     that belong to this application's namespace (the same prefix) and differ
 *     from the current cache; unrelated same-origin caches are preserved.
 *   - The shell is precached on install so the app opens offline. The request
 *     handler serves BOTH the shell route and the shell assets CACHE-FIRST from
 *     the active worker's own cache, so the active controller consistently
 *     serves a coherent HTML/JS set until the user accepts an update. That is
 *     what prevents "new HTML + old JavaScript" on an ordinary reload while a
 *     newer worker is waiting. Assets missing from cache are fetched online and
 *     stored in the current cache.
 *
 * Update flow:
 *   - The worker NEVER calls skipWaiting() on install. A newly installed worker
 *     stays "waiting" until the app explicitly posts a SKIP_WAITING message after
 *     the user accepts the update. Activation runs clients.claim() so the
 *     refreshed page is controlled immediately after the user reloads, but it
 *     does not force-reload: the app registers its reload intent BEFORE posting
 *     the activation message, so an explicit Reload always navigates. A draft,
 *     image or recognition session is preserved until the user accepts.
 *   - Because both HTML and JS are served cache-first from the ACTIVE cache, an
 *     ordinary reload before consent keeps serving the active version's coherent
 *     set. When the accepted worker activates, its distinct cache (populated at
 *     install) serves the new coherent set on the user's Reload.
 *   - The app asset version is independent of the regulatory snapshot timestamp
 *     (data_updated_at).
 */
"use strict";

const CACHE_NAMESPACE = "scan-to-check-static-";
// Filled in by the server with a content hash of the concrete shell assets.
const SHELL_HASH = "__SCAN_TO_CHECK_SHELL_HASH__";
const CACHE = `${CACHE_NAMESPACE}scan-to-check-shell-${SHELL_HASH}`;

// Explicit, versioned static-asset allowlist (application shell only). The app
// is served at "/" (index.html); /index.html is NOT a separate route, so it is
// not precached. Keep in sync with the packaged assets. Do NOT add /api/... here.
const SHELL_ASSETS = [
  "/",
  "/static/ocr.js",
  "/static/scanner.js",
  "/static/scanner.css",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
];

// The single supported shell route. Any other navigation (e.g. /docs, /openapi,
// an unknown route) is passed to the network and NEVER cached as the offline
// home. It is served CACHE-FIRST so the active controller always serves its own
// coherent precached shell (never new network HTML alongside old cached JS).
function isShellRoute(pathname) {
  return pathname === "/";
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
    // No skipWaiting(): the updated worker stays WAITING until the app posts
    // SKIP_WAITING after the user accepts the Reload.
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Delete ONLY obsolete caches owned by THIS application's namespace. Caches
    // that belong to other apps on the same origin are preserved.
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => k.startsWith(CACHE_NAMESPACE) && k !== CACHE)
        .map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

// Cache-first for the shell route (so the active controller always serves its
// own coherent precached shell; fall back to network only when the shell is not
// yet cached). For the explicit static shell assets, serve cache-first then
// network. We NEVER cache or serve cached API responses: any /api/... request
// must go to the network and, if it fails, is reported by the app as backend
// unavailability (not a cached finding).
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never intercept or cache API/backend responses.
  if (url.pathname.startsWith("/api/")) return;

  // Never intercept or cache user images / labels / OCR artifacts.
  if (url.pathname.startsWith("/static/vendor/tesseract/")) return;

  // Only respond to same-origin requests.
  if (url.origin !== self.location.origin) return;

  // Navigation for the explicit shell route: CACHE-FIRST from the active
  // worker's own cache. This is what keeps the active controller serving a
  // coherent HTML/JS set: an ordinary reload before consent returns the precached
  // HTML for the SAME version whose JS is served cache-first, so a waiting newer
  // worker cannot deliver new HTML atop old JS. If the shell is not yet cached
  // (first-ever navigation when the worker just took control), fetch online and
  // store it. Any OTHER navigation (docs, openapi, unknown route) goes straight
  // to the network and is never written to the shell cache.
  if (req.mode === "navigate") {
    if (!isShellRoute(url.pathname)) return;
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match("/");
      if (cached) return cached;
      try {
        const resp = await fetch(req);
        if (resp && resp.ok && resp.type !== "opaqueredirect" &&
            new URL(resp.url).origin === self.location.origin) {
          cache.put("/", resp.clone());
        }
        return resp;
      } catch (e) {
        return Response.error();
      }
    })());
    return;
  }

  // Shell assets (ocr.js, manifest, icons): cache-first within the intended
  // current-version cache only, then network.
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(req);
      if (cached) return cached;
      try {
        const resp = await fetch(req);
        if (resp && resp.ok) {
          cache.put(req, resp.clone());
        }
        return resp;
      } catch (e) {
        return Response.error();
      }
    })());
    return;
  }

  // Everything else (including OCR vendor assets) goes straight to the network.
  return;
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "SKIP_WAITING") {
    // Explicit user-driven update: activate the new worker. The app registers its
    // reload intent BEFORE posting this message, so the controllerchange that
    // follows always triggers the user-chosen navigation.
    self.skipWaiting();
  }
});
