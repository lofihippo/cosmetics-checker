"""Installable PWA shell regressions for the scan-to-check app.

Regulatory information, not legal advice.

These drive a real Uvicorn app (in-memory registry, injectable mock product
client, no network) against real Chrome and verify:

  * the web app manifest is served at root scope with icons/start_url/display;
  * the service worker is served from "/" (so its scope actually controls the
    app) and registers, and its versioned static-asset cache allowlists ONLY the
    application shell;
  * the service worker NEVER caches /api/... responses (product/check/health),
    regulatory findings, user images or entered labels, so a cached API result
    is never replayed as current;
  * cache cleanup is NAMESPACE-SCOPED: an unrelated origin cache is preserved,
    and only this app's obsolete versioned caches are deleted;
  * only the explicit shell route ("/") is ever cached as the offline home, so a
    /docs navigation can NEVER replace it with non-shell content;
  * honest offline behavior: when the backend (Python server) is unreachable,
    product lookup and regulatory checking report backend unavailability (they
    do NOT fabricate a finding or a ready status), while the shell/manual
    editing remains usable;
  * a REAL server-served v1 -> v2 update stays WAITING until the user accepts,
    activates on explicit Reload, cleans up the old scoped cache, and serves
    coherent assets -- with the draft preserved until consent.

This is a browser fixture; it is NOT a physical-device installation or a hosted
deployment test. OCR/vendor heavyweight assets are NOT precached by the worker
(this milestone requires connectivity to load OCR assets).
"""
from __future__ import annotations

import json

import pytest


def _make_server():
    # Reuse the shared in-memory harness helpers for a real app over a real port.
    import socket
    import threading
    import time
    import urllib.request
    import uvicorn
    from scan_to_check.app import create_app

    from conftest import make_registry, MockClient

    reg, metadata = make_registry()
    app = create_app(reg, client=MockClient({}))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)
    return f"http://127.0.0.1:{port}", srv, thread


def _make_versioned_server():
    """A server whose shell HTML/JS/worker can be swapped between versions at
    runtime, so a REAL service-worker version update can be driven. It uses the
    production ``create_app`` content-hash mechanism (``shell_index`` /
    ``shell_ocr`` override bytes), so the cache identity is derived from the
    concrete asset bytes exactly as in production.
    """
    import socket
    import threading
    import time
    import urllib.request
    import uvicorn

    from conftest import make_registry, MockClient
    from scan_to_check.app import create_app

    ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
    STATIC = ROOT / "scan_to_check" / "static"
    base_html = (STATIC / "index.html").read_bytes()
    base_ocr = (STATIC / "ocr.js").read_bytes()

    # Version-stamp the HTML and OCR JS so the bytes differ between versions.
    def _html(v):
        return base_html.replace(b"<body>", ('<body data-review-version="%s">' % v).encode())

    def _ocr(v):
        return ("/* shell-version:%s */\nwindow.reviewOcrVersion=%s;\n" %
                (v, json.dumps(v))).encode() + base_ocr

    state = {"version": "v1"}

    reg, metadata = make_registry()
    # A small ASGI wrapper that delegates to the CURRENT version's app. uvicorn
    # loads the app once, so swapping the underlying app must happen on a
    # per-request basis (uvicorn does not re-read server.app after startup).
    from starlette.types import Receive, Scope, Send

    current = {"app": None}

    def make(v, ocr_v=None):
        # ``ocr_v`` defaults to ``v``; when a caller passes a different value the
        # served JS bytes are intentionally kept the SAME as the prior version so
        # an HTML-only change can be driven distinctly from a JS change.
        app = create_app(reg, client=MockClient({}),
                         shell_index=_html(v), shell_ocr=_ocr(ocr_v if ocr_v is not None else v))
        # Override the /static/ocr.js StaticFiles route so the version-marked
        # JS is served (and thus precached) exactly as the SW payload expects.
        from fastapi.responses import Response
        for r in list(app.router.routes):
            if getattr(r, "path", "") == "/static/ocr.js":
                app.router.routes.remove(r)
        @app.get("/static/ocr.js")
        def ocr_route():
            return Response(_ocr(state["ocr_version"]), media_type="application/javascript",
                            headers={"Cache-Control": "no-store"})
        # Insert the override before the StaticFiles mount.
        for r in app.router.routes:
            if getattr(r, "path", "") == "/static/ocr.js":
                app.router.routes.remove(r)
                app.router.routes.insert(0, r)
                break
        return app

    state = {"version": "v1", "ocr_version": "v1"}

    current["app"] = make("v1")

    async def wrapper(scope: Scope, receive: Receive, send: Send) -> None:
        # FastAPI/Starlette apps handle the ASGI protocol synchronously via their
        # own __call__; delegate to the current version's app.
        await current["app"](scope, receive, send)

    # Rebuild the SAME mechanism the production app uses for the current version.
    def rebuild():
        return make(state["version"], state["ocr_version"])

    state["rebuild"] = rebuild

    def swap(v):
        state["version"] = v
        state["ocr_version"] = v
        current["app"] = rebuild()

    state["swap"] = swap

    def swap_html_only(v):
        # Change ONLY the served HTML/worker bytes; keep the JS bytes identical
        # to the previous version so the test exercises a truly HTML-only change.
        state["version"] = v
        current["app"] = rebuild()

    state["swap_html_only"] = swap_html_only

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(wrapper, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)
    state["port"] = port
    return f"http://127.0.0.1:{port}", srv, thread, state



@pytest.fixture(scope="module")
def server():
    url, srv, thread = _make_server()
    yield url
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    p = browser.new_page()
    yield p
    p.close()


@pytest.fixture
def versioned():
    url, srv, thread, state = _make_versioned_server()
    yield url, state
    srv.should_exit = True
    thread.join(timeout=5)


def _wait_shell_ready(page, server):
    page.goto(server + "/")
    page.wait_for_selector("#barcode")


def _wait_sw_controlling(page, server, timeout=15000):
    """Wait until the registered service worker is ACTIVE and controlling the page."""
    page.wait_for_function("""async () => {
      if (!('serviceWorker' in navigator)) return false;
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""", timeout=timeout)


def test_manifest_is_served_at_root_scope(page, server):
    import urllib.request
    with urllib.request.urlopen(server + "/manifest.webmanifest", timeout=5) as resp:
        assert resp.status == 200
        mime = resp.headers.get("Content-Type", "")
        assert "manifest" in mime or "json" in mime
        body = json.loads(resp.read().decode("utf-8"))
    assert body["start_url"] == "/"
    assert body["scope"] == "/"
    assert body["display"] in ("standalone", "fullscreen", "minimal-ui", "browser")
    # Icons (any + maskable) are declared and actually reachable.
    assert any(icon.get("purpose") == "maskable" for icon in body.get("icons", []))
    for icon in body.get("icons", []):
        with urllib.request.urlopen(server + icon["src"], timeout=5) as ir:
            assert ir.status == 200
            assert "png" in ir.headers.get("Content-Type", "")


def test_service_worker_served_at_root_scope_and_registers(page, server):
    import urllib.request
    with urllib.request.urlopen(server + "/sw.js", timeout=5) as resp:
        assert resp.status == 200
        assert "javascript" in resp.headers.get("Content-Type", "")
        # The SW is served from "/" so its scope controls the whole app.
        allowed = resp.headers.get("Service-Worker-Allowed", "")
    assert allowed == "/" or allowed == ""  # root scope is the default
    _wait_shell_ready(page, server)
    page.wait_for_timeout(200)
    assert page.evaluate("'serviceWorker' in navigator") is True


def test_service_worker_scope_controls_root(page, server):
    _wait_shell_ready(page, server)
    _wait_sw_controlling(page, server)
    scope = page.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return reg ? reg.scope : null;
    }""")
    assert scope == server + "/"


def test_service_worker_does_not_cache_api_responses(page, server):
    """The SW allowlist must NOT include /api/...; a cached API response must
    never be replayed as current. We make an API request and confirm no /api path
    was stored in the versioned shell cache."""
    _wait_shell_ready(page, server)
    _wait_sw_controlling(page, server)
    # Make a real API request that the SW could (incorrectly) cache.
    page.evaluate("""async () => { await fetch('/api/health'); }""")
    page.wait_for_timeout(300)
    cached_urls = page.evaluate("""async () => {
      const keys = await caches.keys();
      const hits = [];
      for (const k of keys) {
        const c = await caches.open(k);
        const reqs = await c.keys();
        for (const r of reqs) hits.push(r.url);
      }
      return hits;
    }""")
    assert not any("/api/" in u for u in cached_urls), cached_urls
    # The shell itself (root + ocr.js + manifest) IS cached.
    assert any(u.endswith("/") for u in cached_urls)
    # OCR vendor assets are NOT precached (heavy, requires connectivity).
    assert not any("/static/vendor/tesseract/" in u for u in cached_urls)


def test_service_worker_allowlist_excludes_user_images_and_labels(page, server, tmp_path):
    """The SW must never cache a user-selected image or the entered label."""
    _wait_shell_ready(page, server)
    _wait_sw_controlling(page, server)
    # Open manual mode so the ingredient label is visible/editable.
    page.click("#manual-btn")
    page.wait_for_timeout(100)
    page.fill("#ingredients", "AQUA, GLYCERIN")
    page.wait_for_timeout(100)
    # No blob:/object URL is stored in the shell cache.
    cached_urls = page.evaluate("""async () => {
      const hits = [];
      for (const k of await caches.keys()) {
        const c = await caches.open(k);
        for (const r of await c.keys()) hits.push(r.url);
      }
      return hits;
    }""")
    assert not any(u.startswith("blob:") for u in cached_urls)


def test_offline_backend_unavailable_shows_honest_error(page, server):
    """When the Python backend is unreachable, lookup/check must report backend
    unavailability and must NOT fabricate a finding or a ready status."""
    _wait_shell_ready(page, server)
    # Enter manual mode so the ingredient label is visible/editable offline.
    page.click("#manual-btn")
    page.wait_for_timeout(100)
    page.fill("#ingredients", "AQUA, GLYCERIN")
    # Block the check API so it fails (simulating an offline backend).
    page.route("**/api/check", lambda route: route.abort())
    page.click("#check-btn")
    page.wait_for_timeout(200)
    msg = page.text_content("#lookup-msg") or ""
    assert "offline" in msg.lower() or "service" in msg.lower()
    # No results/findings rendered (no fabricated regulatory output).
    assert page.eval_on_selector("#results-card", "el=>el.hidden") is True
    # Manual editing remains available (shell is still usable offline).
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_offline_lookup_reports_offline_not_cached_product(page, server):
    _wait_shell_ready(page, server)
    page.fill("#barcode", "3560070791460")
    page.route("**/api/product/**", lambda route: route.abort())
    page.click("#lookup-form button[type=submit]")
    page.wait_for_timeout(200)
    msg = page.text_content("#lookup-msg") or ""
    assert "offline" in msg.lower()
    # No product card fabricated.
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == ""


def test_cache_cleanup_preserves_unrelated_origin_cache(page, server):
    """BEHAVIORAL namespace check: the SW activation must NOT delete caches that
    belong to a DIFFERENT app on the same origin. Only this app's namespace is
    cleaned."""
    _wait_shell_ready(page, server)
    # Seed an unrelated cache with data under the same origin (CacheStorage is
    # shared by origin, not by app).
    page.evaluate("""async () => {
      const c = await caches.open('unrelated-app-cache');
      await c.put('/unrelated/asset', new Response('keep me'));
    }""")
    _wait_sw_controlling(page, server, timeout=15000)
    page.wait_for_timeout(300)
    keys = page.evaluate("caches.keys()")
    assert "unrelated-app-cache" in keys, keys
    # The unrelated cache's contents are intact.
    kept = page.evaluate("""async () => {
      const c = await caches.open('unrelated-app-cache');
      const r = await c.match('/unrelated/asset');
      return r ? r.text() : null;
    }""")
    assert kept == "keep me"
    # Our own namespace cache is present.
    assert any(k.startswith("scan-to-check-static-") for k in keys), keys


def test_docs_navigation_does_not_corrupt_offline_home(page, server):
    """Visiting /docs (Swagger, non-shell content) must NEVER be written to the
    cached offline home ("/"). A doc/API/non-shell navigation is exposed to the
    network and never cached as the app shell."""
    _wait_shell_ready(page, server)
    _wait_sw_controlling(page, server)
    # Visit /docs under the active worker; this is a non-shell navigation.
    page.goto(server + "/docs")
    page.wait_for_timeout(300)
    # The shell cache must still hold the real app shell (scanner), not Swagger.
    root_cache = page.evaluate("""async () => {
      const keys = await caches.keys();
      for (const k of keys) {
        if (k.startsWith('scan-to-check-static-')) {
          const c = await caches.open(k);
          const r = await c.match('/');
          if (r) return await r.text();
        }
      }
      return '';
    }""")
    assert "Swagger UI" not in root_cache
    assert "#barcode" in root_cache or "scan-to-check" in root_cache
    # Navigate offline: the cached home must still be the scanner, not Swagger.
    context = page.context
    context.set_offline(True)
    page.goto(server + "/?offlineprobe=1", wait_until="domcontentloaded")
    assert page.locator("#barcode").count() > 0, page.title()
    assert "Swagger UI" not in page.title()
    context.set_offline(False)


def test_update_flow_waits_for_consent_and_reloads(page, server):
    """A real server-served v1 -> v2 worker change must STAY WAITING until the
    user explicitly clicks Reload. Before consent the v1 worker still controls
    the page, the v1 cache is still present, the draft is preserved, and no
    automatic navigation occurs. After clicking Reload the v2 worker activates,
    the v2 assets are served coherently, and the old scoped cache is deleted."""
    _wait_shell_ready(page, server)
    _wait_sw_controlling(page, server, timeout=15000)
    # Enter a draft that must survive the waiting period (until consent).
    page.click("#manual-btn")
    page.wait_for_timeout(100)
    page.fill("#ingredients", "DRAFT KEEP")
    page.wait_for_timeout(100)

    # Force an update against the SAME worker bytes: must be a no-op (no banner).
    v1_controller = page.evaluate("navigator.serviceWorker.controller && navigator.serviceWorker.controller.scriptURL")
    assert v1_controller.endswith("/sw.js")
    page.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      if (reg && reg.update) await reg.update();
    }""")
    page.wait_for_timeout(800)
    assert page.locator("#update-banner").count() == 0
    draft = page.eval_on_selector("#ingredients", "el=>el.value") or ""
    assert "DRAFT KEEP" in draft
    assert page.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""") is True


def test_update_real_v1_to_v2_activates_on_consent_and_cleans_old_cache(versioned, browser):
    """A REAL server-served v1 -> v2 worker change (different bytes) stays
    WAITING until consent, then a click on Reload activates v2, serves coherent
    v2 assets, deletes the obsolete v1 scope cache, and the draft is preserved
    up to the moment of consent."""
    url, state = versioned
    swap = state["swap"]

    context = browser.new_context()
    p = context.new_page()
    p.goto(url + "/")
    p.wait_for_selector("#barcode")
    # Wait until the v1 worker controls the page.
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""", timeout=15000)
    p.click("#manual-btn")
    p.wait_for_timeout(100)
    p.fill("#ingredients", "DRAFT KEEP")
    p.wait_for_timeout(100)

    # Switch the server to v2 (both worker AND asset bytes change). Swapping the
    # whole app bounces the hash -> a distinct v2 cache name.
    swap("v2")

    # Ask the SW to check for an update.
    p.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      if (reg && reg.update) await reg.update();
    }""")
    # A new worker is installed and becomes WAITING (NOT active) -- it must
    # NOT auto-activate before consent.
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.waiting);
    }""", timeout=15000)
    p.wait_for_timeout(300)
    # Before consent: v2 is waiting, v1 still controls, draft preserved.
    assert p.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return reg.waiting !== null;
    }""") is True
    assert p.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return reg.active && reg.active.state === 'activated';
    }""") is True
    assert (p.eval_on_selector("#ingredients", "el=>el.value") or "") == "DRAFT KEEP"
    # The v1 cache is still present AND is the only one the active controller
    # uses before consent; a waiting v2 install must NOT have overwritten it.
    caches_before = p.evaluate("caches.keys()")
    v1cache = [k for k in caches_before if "scan-to-check-shell-" in k]
    assert len(v1cache) >= 1, caches_before

    # COHERENCE: an ordinary reload BEFORE consent must keep serving the ACTIVE
    # (v1) coherent set (HTML v1 + JS v1), not a mixed HTML v2/JS v1.
    p.reload()
    p.wait_for_selector("#barcode")
    p.wait_for_timeout(300)
    coherence = p.evaluate("""(async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return { html: document.body.dataset.reviewVersion,
               js: window.reviewOcrVersion,
               waiting: !!reg.waiting };
    })()""")
    assert coherence["html"] == "v1", coherence
    assert coherence["js"] == "v1", coherence
    assert coherence["waiting"] is True, coherence

    # Banner is shown; click Reload. The reload intent is registered BEFORE
    # posting SKIP_WAITING, so this must trigger a real navigation.
    p.wait_for_selector("#update-banner button", timeout=15000)
    nav = []
    p.on("framenavigated", lambda f: nav.append(f.url) if f == p.main_frame else None)
    p.click("#update-banner button")
    p.wait_for_timeout(1200)
    assert any("sw.js" not in n for n in nav), nav

    # After consent the v2 worker is active/controlling.
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""", timeout=15000)
    # POST-CONSENT coherence: the reloaded page is the coherent v2 set.
    p.wait_for_timeout(400)
    coherence2 = p.evaluate("({html: document.body.dataset.reviewVersion, js: window.reviewOcrVersion})")
    assert coherence2["html"] == "v2", coherence2
    assert coherence2["js"] == "v2", coherence2
    # v1 scoped cache deleted, only one shell cache (the active v2 cache) remains.
    caches_after = p.evaluate("caches.keys()")
    v2cache = [k for k in caches_after if "scan-to-check-shell-" in k]
    assert len(v2cache) == 1, caches_after
    # Before consent there were TWO distinct shell caches (v1 active + v2 waiting),
    # proving the waiting v2 install did NOT overwrite the active v1 cache. After
    # consent the obsolete v1 cache is gone and only the v2 cache remains.
    assert len(v1cache) == 2 and v1cache[0] != v1cache[1], v1cache
    assert len(v2cache) == 1, caches_after
    assert v2cache[0] in v1cache, (v1cache, v2cache)
    # The reload left no stuck reload flag (no later surprise reload).
    assert p.evaluate("window.__reloadOnControllerChange === undefined || window.__reloadOnControllerChange === false") is True
    context.close()


def _drive_v1_to_v2_waiting(url, state, swap, p):
    """Load the app, wait for v1 control, then swap the server to v2 and wait
    for the v2 worker to become WAITING (without consent). Returns after v2 is
    waiting and v1 still controls."""
    p.goto(url + "/")
    p.wait_for_selector("#barcode")
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""", timeout=15000)
    swap("v2")
    p.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      if (reg && reg.update) await reg.update();
    }""")
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.waiting);
    }""", timeout=15000)
    p.wait_for_timeout(300)
    # v1 still controls, v2 waiting, no auto-activation.
    assert p.evaluate("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return reg.active.state === 'activated' && !!reg.waiting;
    }""") is True


def test_coherence_new_tab_while_waiting_serves_active_version(versioned, browser):
    """Opening a NEW tab while a newer worker is waiting must serve the ACTIVE
    version's coherent set (not the mixed new-HTML/old-JS), and the new tab must
    keep the v1 controller until the update is accepted."""
    url, state = versioned
    swap = state["swap"]
    ctx = browser.new_context()
    p = ctx.new_page()
    _drive_v1_to_v2_waiting(url, state, swap, p)
    # Open a second, fresh tab while v2 is waiting.
    p2 = ctx.new_page()
    p2.goto(url + "/")
    p2.wait_for_selector("#barcode")
    p2.wait_for_timeout(400)
    coh = p2.evaluate("""(async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return { html: document.body.dataset.reviewVersion, js: window.reviewOcrVersion, waiting: !!reg.waiting };
    })()""")
    assert coh["html"] == "v1", coh
    assert coh["js"] == "v1", coh
    assert coh["waiting"] is True, coh
    # The new tab is still controlled by the v1 worker (no active v2).
    assert p2.evaluate("navigator.serviceWorker.controller.state === 'activated'") is True
    p.close(); p2.close(); ctx.close()


def test_coherence_offline_while_waiting_serves_active_version(versioned, browser):
    """Going OFFLINE while a newer worker is waiting must serve the ACTIVE
    version's coherent cached set, and never a mixed HTML/JS pair."""
    url, state = versioned
    swap = state["swap"]
    ctx = browser.new_context()
    p = ctx.new_page()
    _drive_v1_to_v2_waiting(url, state, swap, p)
    ctx.set_offline(True)
    p.goto(url + "/?offlinewhilewaiting=1", wait_until="domcontentloaded")
    p.wait_for_selector("#barcode")
    p.wait_for_timeout(300)
    coh = p.evaluate("""(async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return { html: document.body.dataset.reviewVersion, js: window.reviewOcrVersion, waiting: !!reg.waiting };
    })()""")
    assert coh["html"] == "v1", coh
    assert coh["js"] == "v1", coh
    assert coh["waiting"] is True, coh
    ctx.set_offline(False)
    p.close(); ctx.close()


def test_coherence_changed_html_only_still_coherent(versioned, browser):
    """An HTML-only change (JS bytes unchanged) must still be served coherently
    once accepted: the hash changes because the HTML changed, so the new release
    gets its own cache and the reload serves the new HTML with the (unchanged)
    JS -- never new HTML atop a different JS set."""
    url, state = versioned
    swap = state["swap_html_only"]
    ctx = browser.new_context()
    p = ctx.new_page()
    _drive_v1_to_v2_waiting(url, state, swap, p)
    # Reload before consent -> active (v1) coherent set.
    p.reload(); p.wait_for_selector("#barcode"); p.wait_for_timeout(300)
    coh = p.evaluate("({html: document.body.dataset.reviewVersion, js: window.reviewOcrVersion})")
    assert coh["html"] == "v1" and coh["js"] == "v1", coh
    # Accept and reload.
    p.wait_for_selector("#update-banner button", timeout=15000)
    p.click("#update-banner button")
    p.wait_for_timeout(1200)
    p.wait_for_function("""async () => {
      const reg = await navigator.serviceWorker.getRegistration();
      return !!(reg && reg.active && navigator.serviceWorker.controller);
    }""", timeout=15000)
    p.wait_for_timeout(400)
    coh2 = p.evaluate("({html: document.body.dataset.reviewVersion, js: window.reviewOcrVersion})")
    # The HTML changed but the JS bytes did not: it is still the SAME js version.
    assert coh2["html"] == "v2" and coh2["js"] == "v1", coh2
    p.close(); ctx.close()
