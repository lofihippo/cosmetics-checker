"""Local barcode -> product lookup -> editable label -> existing checker app.

Regulatory information, not legal advice.

This is a small local vertical slice (the first scanner milestone), NOT a full
PWA. It:

  * looks up a barcode via the bounded Open Beauty Facts read adapter
    (``obf.py``) and never does regulatory matching in frontend code;
  * lets the user review/edit the product's ingredient text (manual paste is the
    fallback when lookup fails);
  * submits the exact reviewed text to the EXISTING shared evaluation engine
    (``eu_ingredient_linter.ingredient_report``) -- no duplicate
    matching/tokenization/evaluation, no concentration inference from order;
  * keeps product provenance separate from regulatory provenance/timestamp.

The snapshot is a SUPPORTED registry loaded once (``eu_reg_lists.load``); the
app never fetches/normalizes/mutates it. Unknown/unmatched ingredients stay
unknown; no clean/safe/legal certificate is ever emitted.

This module reuses the proven request-generation/validation pattern. The client
is injectable so tests never hit the network. It serves a single-page static UI
plus two JSON endpoints: ``/api/lookup/<barcode>`` and ``/api/check``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from eu_ingredient_linter import DISCLAIMER, ingredient_report
from eu_reg_lists import Registry, load
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .obf import OpenBeautyFactsClient, validate_barcode

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_INGREDIENT_TEXT_LENGTH = 10_000

# PWA manifest content type (served at root scope so it is discoverable).
MANIFEST_MEDIA_TYPE = "application/manifest+json"

# Placeholder inside sw.js replaced with the computed shell-asset hash at serve
# time. The hash (CACHE_VERSION) ties the cache identity to the concrete shell
# assets (HTML, JS, manifest, icons) plus the service-worker logic itself, so a
# new release always installs into a DIFFERENT cache and a waiting install can
# never overwrite the active worker's cache.
SHELL_HASH_PLACEHOLDER = "__SCAN_TO_CHECK_SHELL_HASH__"

# The files whose bytes make up the application-shell version. sw.js is handled
# separately (its logic is hashed via the template so a logic change also yields
# a new cache name without a circular hash-within-hash).
_SHELL_HASH_FILES = (
    "index.html",
    "ocr.js",
    "scanner.js",
    "scanner.css",
    "manifest.webmanifest",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
)


def _shell_asset_bytes(static_dir: Path, rel: str, overrides: Optional[Dict[str, bytes]]) -> bytes:
    if overrides and rel in overrides:
        return overrides[rel]
    return (static_dir / rel).read_bytes()


def compute_shell_hash(static_dir: Path = STATIC_DIR,
                       overrides: Optional[Dict[str, bytes]] = None) -> str:
    """Return a short content hash identifying this application-shell release.

    It covers the cached shell assets (HTML, OCR JS, manifest, icons) and the
    service-worker logic template, so any change to any of them produces a new
    cache identity. ``overrides`` lets a test substitute in-memory asset bytes.
    """
    import hashlib
    h = hashlib.sha256()
    for rel in _SHELL_HASH_FILES:
        h.update(_shell_asset_bytes(static_dir, rel, overrides))
    sw_template = (static_dir / "sw.js").read_text(encoding="utf-8")
    h.update(sw_template.encode("utf-8"))
    return h.hexdigest()[:16]


def create_app(registry: Registry, client: Optional[OpenBeautyFactsClient] = None,
               metadata: Optional[dict] = None,
               shell_index: Optional[bytes] = None,
               shell_ocr: Optional[bytes] = None) -> FastAPI:
    app = FastAPI(
        title="scan-to-check (local)",
        description="Local barcode -> product ingredients -> existing checker.",
        version="0.1.0",
    )
    # Swagger/ReDoc use CDN assets and inline bootstraps; explicitly exempt
    # their built-in routes from CSP, while retaining the other headers.
    @app.middleware("http")
    async def browser_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if (response.headers.get("content-type", "").startswith("text/html")
                and request.url.path not in ("/docs", "/docs/oauth2-redirect", "/redoc")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'wasm-unsafe-eval'; "
                "worker-src 'self' blob:; "
                "connect-src 'self' blob:; "
                "img-src 'self' blob: data:; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'; "
                "form-action 'self'; "
            )
        return response

    app.state.client = client or OpenBeautyFactsClient()
    app.state.snapshot = registry
    app.state.metadata = metadata or {}

    def _overrides() -> Dict[str, bytes]:
        o: Dict[str, bytes] = {}
        if shell_index is not None:
            o["index.html"] = shell_index
        if shell_ocr is not None:
            o["ocr.js"] = shell_ocr
        return o

    def _disclaimer() -> str:
        return DISCLAIMER

    def _snapshot_ts() -> Optional[str]:
        return getattr(app.state.snapshot, "data_updated_at", None)

    @app.get("/")
    def index():
        if shell_index is not None:
            from fastapi.responses import Response
            return Response(shell_index, media_type="text/html",
                            headers={"Cache-Control": "no-cache"})
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/sw.js")
    def service_worker():
        # The service worker MUST be served from the root scope ("/") so it
        # controls the whole app (including the root navigation), not just
        # /static/. Served with a JS content type and no-cache headers so a
        # deployed update is picked up promptly. The cache identity is bound to
        # the actual shell assets via a content hash computed fresh each serve,
        # so a release change always yields a distinct, non-overwriting cache.
        from fastapi.responses import Response
        sw = STATIC_DIR.joinpath("sw.js").read_text(encoding="utf-8")
        h = compute_shell_hash(STATIC_DIR, _overrides())
        body = sw.replace(SHELL_HASH_PLACEHOLDER, h)
        return Response(body,
                        media_type="application/javascript",
                        headers={"Cache-Control": "no-cache",
                                 "Service-Worker-Allowed": "/",
                                 "X-Shell-Hash": h})

    @app.get("/manifest.webmanifest")
    def manifest():
        # The web app manifest is served at root scope so it is discoverable by
        # the browser regardless of the page URL.
        from fastapi.responses import Response
        return Response(STATIC_DIR.joinpath("manifest.webmanifest").read_text(encoding="utf-8"),
                        media_type=MANIFEST_MEDIA_TYPE,
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "disclaimer": _disclaimer(),
            "data_updated_at": _snapshot_ts(),
            "registry_size": len(app.state.snapshot),
        }

    @app.get("/api/product/{barcode}")
    def product(barcode: str):
        """Look up a product by barcode (preserves leading zeros).

        Returns a typed ProductResult. Malformed input -> 422. On a transient
        upstream failure the result has ``status == upstream_error`` (200) and
        NO product; the UI shows the failure and offers manual paste.
        """
        try:
            validate_barcode(barcode)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_barcode",
                                             "message": str(exc)})
        result = app.state.client.lookup(barcode)
        return result.to_dict()

    @app.post("/api/check")
    def check(payload: Dict[str, Any] = Body(...)):
        """Reuse the shared evaluation engine on the user's reviewed ingredient text.

        This endpoint does NOT touch Open Beauty Facts. It validates the text and
        runs ``ingredient_report`` (the same engine the label playground/API use),
        returning the standard report plus the resolved snapshot timestamp.
        """
        text = payload.get("ingredients")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(422, detail={"code": "no_ingredients",
                                             "message": "ingredients text is required"})
        if len(text) > MAX_INGREDIENT_TEXT_LENGTH:
            raise HTTPException(422, detail={"code": "too_long",
                                             "message": "ingredients text too long"})
        product = payload.get("product") or {}
        if not isinstance(product, dict):
            raise HTTPException(422, detail={"code": "invalid_product",
                                             "message": "product must be an object"})
        # Input mode is separate from the product category. ``mode`` describes
        # how the ingredient text was obtained (manual paste / OCR draft / a
        # fetched product lookup); it is never itself a product category and is
        # not used to decide whether cosmetic rules apply.
        mode = product.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise HTTPException(422, detail={"code": "invalid_mode",
                                             "message": "mode must be a string"})
        if isinstance(mode, str) and mode not in ("manual", "ocr", "lookup"):
            raise HTTPException(422, detail={"code": "invalid_mode",
                                             "message": f"unknown input mode {mode!r}"})
        # Explicit non-cosmetic product category is rejected at the CHECK endpoint
        # too (not only in the UI): cosmetic rules must never be applied to an
        # explicitly food/petfood/other product. Cosmetic conclusions in that case
        # would be a fabricated regulatory determination. A manual/OCR input with
        # NO fetched product category (``product_type`` absent) is a distinct mode
        # and IS reviewable as cosmetic text.
        product_type = product.get("product_type")
        if isinstance(product_type, str) and product_type not in ("beauty", "cosmetic"):
            raise HTTPException(422, detail={
                "code": "non_cosmetic_product",
                "message": f"product_type {product_type!r} is not a cosmetic; "
                           "cosmetic regulatory rules are not applied."})
        rep = ingredient_report(text, app.state.snapshot)
        if rep["no_ingredients"]:
            raise HTTPException(422, detail={"code": "no_ingredients",
                                             "message": "no ingredient tokens parsed"})
        # Add explicit provenance: the report timestamp is the SNAPSHOT time.
        return {**rep, "snapshot_data_updated_at": _snapshot_ts(),
                "disclaimer": _disclaimer(),
                "product": product}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def run() -> None:
    import uvicorn
    snapshot_path = os.environ.get("EU_REG_SNAPSHOT",
                                   "data/processed/eu_reg_lists.sqlite")
    reg = load(snapshot_path)
    app = create_app(reg)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8010")))


if __name__ == "__main__":
    run()
