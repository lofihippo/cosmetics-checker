"""eu-reg-api: local, read-only REST API over the Annex-II registry.

Reuses ``eu_reg_lists`` for search/lookup and ``eu_ingredient_linter`` for
label checking via ``ingredient_report`` — it never re-derives matching,
tokenization, regulatory classification, or reconciliation.

Read-only contract:
  * A SQLite/JSONL snapshot is loaded ONCE at app creation through the existing
    ``eu_reg_lists.load`` loader; it is never fetched, normalized, or mutated,
    and never loaded per-request.
  * Each app owns its own immutable ``AppState`` on ``app.state.snapshot``;
    routes and exception handlers resolve it from the current request (never a
    module-global), so two apps stay fully isolated.
  * If the configured snapshot is missing or unusable, startup fails loudly.
    The curated Phase-0 seed or an empty registry is never silently substituted.
  * ``data_updated_at`` is the snapshot build/update time from the loaded
    metadata (never request time). ``source_updated_at`` and ``retrieved_at``
    are kept separate and never conflated with it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from eu_ingredient_linter import (
    DISCLAIMER,
    build_coverage,
    evaluate_formula,
    ingredient_report,
)
from eu_reg_lists import Registry, load
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import (
    CheckResponse,
    CoverageModel,
    ErrorDetail,
    ErrorResponse,
    FormulaCheckResponse,
    FormulaIngredientModel,
    HealthResponse,
    ListEntry,
    ListResponse,
    MetadataEnvelope,
    RuleModel,
    SearchResult,
    SubstanceModel,
    SubstanceResponse,
)

# Bounded request limits (documented).
SEARCH_LIMIT_MAX = 100
SEARCH_LIMIT_DEFAULT = 25
LIST_LIMIT_MAX = 500
LIST_LIMIT_DEFAULT = 100
MAX_INGREDIENTS = 200
# Raw character-length limit on the ingredient text. Enforced BEFORE
# tokenization so a very long single token can't bypass the token-count limit.
MAX_INGREDIENT_TEXT_LENGTH = 10_000
# Formula endpoint limits (bounded, consistent with /v1/check).
MAX_FORMULA_INGREDIENTS = 200
MAX_FORMULA_BODY_LENGTH = 200_000

# Canonical source-list identifiers exposed by the API and their friendly names.
KNOWN_LISTS: Dict[str, str] = {
    "cosing-annex-ii": "annex-ii",
    "cosing-annex-iii": "annex-iii",
    "cosing-annex-iv": "annex-iv",
    "cosing-annex-v": "annex-v",
    "cosing-annex-vi": "annex-vi",
    "echa-svhc": "svhc",
    "echa-annex-xiv": "annex-xiv",
    "echa-annex-xvii": "annex-xvii",
}
# Reverse: public list name -> internal source key.
_LIST_NAME_TO_KEY = {v: k for k, v in KNOWN_LISTS.items()}

# Typed error responses referenced in OpenAPI for 404/422 routes. Each route
# passes the relevant codes; the merged dict is typed for FastAPI's responses
# parameter (keys may be int or str).
_UNPROCESSABLE_MSG = "Invalid request or unavailable coverage"
_NOT_FOUND_MSG = "Resource not found"


def _static_dir() -> Optional[Path]:
    """Locate the packaged playground static directory.

    Resolves relative to this file so it works whether the package runs from
    the repository checkout or is installed into site-packages.
    """
    here = Path(__file__).resolve().parent
    candidate = here / "static"
    return candidate if candidate.is_dir() else None


def _error_responses(*codes: int) -> Dict[Union[int, str], Dict[str, Any]]:
    desc = {404: _NOT_FOUND_MSG, 422: _UNPROCESSABLE_MSG}
    return {code: {"description": desc.get(code, "Error"),
                   "model": ErrorResponse} for code in codes}  # type: ignore[dict-item]


def _source_key_for(list_name: str) -> Optional[str]:
    return _LIST_NAME_TO_KEY.get(list_name.strip().lower())


async def _read_bounded(request: Request) -> bytes:
    """Read the request body incrementally, capping at ``MAX_FORMULA_BODY_LENGTH``.

    Stops reading once the accumulated size exceeds the limit, so an oversized
    body cannot be fully buffered into memory. Does not rely on Content-Length.
    """
    chunks: List[bytes] = []
    total = 0
    async for chunk in request.stream():
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_FORMULA_BODY_LENGTH:
            raise HTTPException(422, detail={
                "code": "request_too_large",
                "message": f"request body exceeds {MAX_FORMULA_BODY_LENGTH} bytes",
            })
    return b"".join(chunks)


def _formula_request_extra() -> Dict[str, Any]:
    """OpenAPI requestBody for ``POST /v1/check-formula``.

    The ingredient schema is inlined from the typed ``FormulaIngredientModel``
    (which is self-contained — no ``$ref``) so every ``$ref`` in the generated
    document resolves against the OpenAPI document root. The nested ``$defs``
    produced by ``FormulaRequest.model_json_schema()`` would place a
    ``#/$defs/FormulaIngredientModel`` reference inside the request schema where
    it cannot be resolved, so we build the requestBody directly and inline the
    ingredient schema instead. Runtime precision-parsing (``parse_float=
    Decimal``) is unaffected: the route still reads the raw body exactly.
    """
    ing_schema = FormulaIngredientModel.model_json_schema()
    # FormulaIngredientModel.model_json_schema() must be self-contained (no
    # nested $defs/$ref), otherwise inlining would reference an unreachable
    # definition.
    if ing_schema.get("$defs") or "$ref" in ing_schema:
        raise RuntimeError("FormulaIngredientModel schema is not self-contained")
    ingredients_schema: Dict[str, Any] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 200,
        "title": "Ingredients",
        "description": "Structured-formula ingredients (1-200).",
        "items": ing_schema,
    }
    request_schema: Dict[str, Any] = {
        "type": "object",
        "required": ["ingredients"],
        "title": "FormulaRequest",
        "description": (
            "Request contract for ``POST /v1/check-formula``. ``ingredients`` "
            "is required and non-empty, with a maximum of 200 entries. The "
            "route parses the raw JSON body with ``parse_float=Decimal`` to "
            "preserve decimal precision; validation is delegated to the shared "
            "library ``eu_ingredient_linter.evaluate_formula`` (never a second "
            "evaluator)."
        ),
        "properties": {"ingredients": ingredients_schema},
    }
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": request_schema,
                    "examples": {
                        "supported-comparison": {
                            "summary": "Supported comparison with unresolved warnings",
                            "value": {"ingredients": [
                                {"ingredient": "Toluene", "percent": 20,
                                 "unit": "%", "basis": "ready_for_use",
                                 "product_type": "nail_product"}]},
                        },
                        "missing-context": {
                            "summary": "Missing context (unevaluated)",
                            "value": {"ingredients": [
                                {"ingredient": "Toluene", "percent": 20,
                                 "unit": "%"}]},
                        },
                        "benzene-annex2": {
                            "summary": "Benzene intact Annex II evidence",
                            "value": {"ingredients": [
                                {"ingredient": "Benzene", "percent": 1,
                                 "unit": "%"}]},
                        },
                        "hydroquinone-manual-review": {
                            "summary": "Hydroquinone cross-annex manual review",
                            "value": {"ingredients": [
                                {"ingredient": "Hydroquinone", "percent": 0.01,
                                 "unit": "%", "basis": "after_mixing",
                                 "product_type": "artificial_nail_system"}]},
                        },
                        "entry70-d5-washoff": {
                            "summary": "REACH Annex XVII entry 70, D5 wash-off",
                            "value": {"ingredients": [
                                {"ingredient": "541-02-6", "percent": 0.5,
                                 "unit": "% w/w", "basis": "ready_for_use",
                                 "product_type": "wash-off",
                                 "assessment_date": "2028-01-01"}]},
                        },
                    },
                }
            },
        }
    }


@dataclass
class AppState:
    """The loaded, immutable snapshot plus derived facts (per app instance)."""

    registry: Registry
    metadata: Dict[str, Any]

    @property
    def data_updated_at(self) -> Optional[str]:
        return self.registry.data_updated_at

    @property
    def content_hash(self) -> Optional[str]:
        return self.metadata.get("content_hash")

    @property
    def version(self) -> Optional[str]:
        return self.metadata.get("version")

    def coverage(self) -> CoverageModel:
        return CoverageModel(**build_coverage(
            getattr(self.registry, "verified_sources", {}) or {}).to_dict())


def _get_state(request: Request) -> AppState:
    """FastAPI dependency resolving the app-local snapshot from the request.

    Reads ``request.app.state.snapshot`` (set by ``create_app``), so each app
    uses only its own snapshot and apps are fully isolated — no module global.
    """
    state = getattr(request.app.state, "snapshot", None)
    if state is None:
        raise RuntimeError("eu-reg-api not initialised")
    return state


def _to_rule_model(r: Any) -> RuleModel:
    return RuleModel(
        status=r.status, annex=r.annex, regulation=r.regulation,
        source_url=r.source_url, source_updated_at=r.source_updated_at,
        conditions=r.conditions, resolution=r.resolution,
        effective_date=r.effective_date, raw=r.raw,
    )


def _to_substance_model(s: Any) -> SubstanceModel:
    return SubstanceModel(
        id=s.id, name=s.name, aliases=s.aliases, cas=s.cas, ec=s.ec,
        e_number=s.e_number, ci=s.ci, determination=s.determination,
        rules=[_to_rule_model(r) for r in s.rules], sources=s.sources,
        member_of=list(getattr(s, "member_of", None) or []),
    )


def _metadata(state: AppState) -> MetadataEnvelope:
    return MetadataEnvelope(data_updated_at=state.data_updated_at,
                            coverage=state.coverage(),
                            disclaimer=DISCLAIMER)


def _error(state: AppState, code: str, message: str,
           detail: Optional[str] = None) -> ErrorResponse:
    return ErrorResponse(error=ErrorDetail(code=code, message=message,
                                           detail=detail),
                         disclaimer=DISCLAIMER, metadata=_metadata(state))


def list_has_accepted_source(reg: Registry, source_key: str) -> bool:
    vs = getattr(reg, "verified_sources", {}) or {}
    entry = vs.get(source_key, {})
    return bool(entry.get("present") and entry.get("accepted"))


def create_app(registry: Registry, metadata: Dict[str, Any] | None = None) -> FastAPI:
    """Build the FastAPI app, injecting an already-loaded registry.

    State is stored on ``app.state.snapshot`` (per app) and resolved per
    request via ``_get_state``. Loading happens once here, never per request.
    """
    metadata = metadata or getattr(registry, "metadata", {}) or {}
    state = AppState(registry=registry, metadata=metadata)

    app = FastAPI(
        title="EU Regulatory Substance API",
        version="0.1.0",
        description=(
            "Read-only, local API over the Annex-II EU regulatory snapshot. "
            "Regulatory information, not legal advice. A result is never a "
            "compliance determination."
        ),
    )
    app.state.snapshot = state

    # ---- browser playground (static assets from the installed package) ----
    static = _static_dir()
    if static is not None:
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/", response_class=HTMLResponse,
             summary="Browser playground")
    def playground():
        index = _static_dir() / "index.html" if _static_dir() else None
        if index is None or not index.exists():
            raise HTTPException(503, detail={
                "code": "playground_unavailable",
                "message": "playground static assets not installed",
            })
        return FileResponse(str(index))

    @app.get("/formula", response_class=HTMLResponse,
             summary="Browser formula editor")
    def formula_editor():
        index = _static_dir() / "formula.html" if _static_dir() else None
        if index is None or not index.exists():
            raise HTTPException(503, detail={
                "code": "formula_editor_unavailable",
                "message": "formula editor static assets not installed",
            })
        return FileResponse(str(index))

    @app.get("/health", response_model=HealthResponse,
             summary="Service readiness and loaded snapshot identity")
    def health(st: AppState = Depends(_get_state)):
        return HealthResponse(
            status="ready",
            snapshot=st.content_hash or (st.data_updated_at or "unknown"),
            version=st.version,
            content_hash=st.content_hash,
            data_updated_at=st.data_updated_at,
            coverage=st.coverage(),
            count_substances=len(st.registry),
            count_verified_rules=int(st.metadata.get("count_verified_rules", 0)),
            disclaimer=DISCLAIMER,
            note="Service health is not an ingredient compliance determination.",
        )

    @app.get("/v1/substances", response_model=SearchResult,
             summary="Search substances by name/alias/exact identifier",
             responses=_error_responses(422))
    def search(
        q: str = Query(..., min_length=1, description="Name, alias, CAS, EC, E-number, or CI."),
        limit: int = Query(SEARCH_LIMIT_DEFAULT, ge=1, le=SEARCH_LIMIT_MAX,
                           description="Maximum results"),
        st: AppState = Depends(_get_state),
    ):
        results = st.registry.search(q, limit=limit)
        # No matches is an empty result, never a clean determination.
        return SearchResult(
            query=q, count=len(results), limit=limit,
            results=[_to_substance_model(s) for s in results],
            metadata=_metadata(st),
        )

    @app.get("/v1/substances/{cas}", response_model=SubstanceResponse,
             summary="Resolve a substance by CAS identifier",
             responses=_error_responses(404, 422))
    def substance(cas: str, st: AppState = Depends(_get_state)):
        sub = st.registry.lookup(cas)
        if sub is None:
            # Distinguish truly-unknown from ambiguous (never first-pick).
            if cas.strip().lower() in st.registry.ambiguous_keys:
                raise HTTPException(404, detail={
                    "code": "ambiguous_identifier",
                    "message": "identifier resolves to more than one substance; "
                               "left unresolved",
                })
            raise HTTPException(404, detail={
                "code": "not_found", "message": "no substance for this CAS",
            })
        return SubstanceResponse(
            substance=_to_substance_model(sub),
            is_in_dataset=True,
            metadata=_metadata(st),
        )

    @app.get("/v1/lists/{list_name}", response_model=ListResponse,
             summary="List substances in a known EU source list (bounded)",
             responses=_error_responses(422, 404))
    def lists(
        list_name: str,
        limit: int = Query(LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX),
        offset: int = Query(0, ge=0),
        st: AppState = Depends(_get_state),
    ):
        key = _source_key_for(list_name)
        if key is None:
            raise HTTPException(404, detail={
                "code": "unknown_list",
                "message": f"unrecognized list name: {list_name!r}",
                "detail": "accepted: " + ", ".join(sorted(_LIST_NAME_TO_KEY.values())),
            })
        if not list_has_accepted_source(st.registry, key):
            # Known but unavailable -> explicit coverage gap (NOT an empty list).
            raise HTTPException(422, detail={
                "code": "coverage_unavailable",
                "message": f"list {list_name!r} is not covered by this snapshot "
                           f"(Annex II only); missing coverage is not empty coverage.",
            })
        members = [s for s in st.registry.substances if key in (s.sources or [])]
        window = members[offset:offset + limit]
        return ListResponse(
            list_name=list_name, total=len(members), limit=limit, offset=offset,
            entries=[ListEntry(row=offset + i, substance=_to_substance_model(s))
                     for i, s in enumerate(window)],
            metadata=_metadata(st),
        )

    @app.get("/v1/check", response_model=CheckResponse,
             summary="Check a list of ingredients against the snapshot",
             responses=_error_responses(422))
    def check(
        ingredients: str = Query(
            ..., min_length=1, max_length=MAX_INGREDIENT_TEXT_LENGTH,
            description=f"Comma/semicolon-separated ingredients "
                        f"(max {MAX_INGREDIENT_TEXT_LENGTH} chars)."),
        st: AppState = Depends(_get_state),
    ):
        # The raw length is enforced by FastAPI via max_length (before
        # splitting); the token-count limit is a separate, documented check.
        rep = ingredient_report(ingredients, st.registry)
        if rep["no_ingredients"]:
            raise HTTPException(422, detail={
                "code": "no_ingredients",
                "message": "no ingredient tokens parsed (empty/whitespace/tokenless)",
            })
        if rep["n_ingredients"] > MAX_INGREDIENTS:
            raise HTTPException(422, detail={
                "code": "too_many_ingredients",
                "message": f"exceeds limit of {MAX_INGREDIENTS} ingredient tokens",
            })
        # Combine linter fields into the CheckResponse envelope.
        return CheckResponse(
            results=rep["results"], tokens=rep["tokens"],
            n_ingredients=rep["n_ingredients"], n_flagged=rep["n_flagged"],
            n_banned=rep["n_banned"], n_not_in_dataset=rep["n_not_in_dataset"],
            n_unverified_identity=rep["n_unverified_identity"],
            n_unverified=rep["n_unverified"], clean=rep["clean"],
            verdict=rep["verdict"], no_ingredients=rep["no_ingredients"],
            data_updated_at=rep["data_updated_at"],
            registry_size=rep["registry_size"], coverage=rep["coverage"],
            disclaimer=DISCLAIMER, metadata=_metadata(st),
        )

    @app.post("/v1/check-formula", response_model=FormulaCheckResponse,
              summary="Check a structured formula against the snapshot",
              responses=_error_responses(422),
              openapi_extra=_formula_request_extra())
    async def check_formula(
        request: Request,
        st: AppState = Depends(_get_state),
    ):
        """Evaluate a structured formula (ingredient + optional concentration +
        explicit unit/basis/product context) against the bounded Annex III
        evaluator via the shared library.

        The raw JSON body is read incrementally (bounded) and parsed with
        ``parse_float=Decimal`` so decimal input precision is preserved exactly
        (no binary-float round-trip); validation is delegated to
        ``eu_ingredient_linter.evaluate_formula`` (never a second evaluator).
        The snapshot is read-only.
        """
        try:
            body = await _read_bounded(request)
        except HTTPException:
            raise
        try:
            driven_raw = json.loads(body.decode("utf-8"), parse_float=Decimal,
                                    parse_int=Decimal)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(422, detail={
                "code": "invalid_json",
                "message": f"invalid JSON body: {exc}",
            }) from exc
        except (InvalidOperation, ValueError, ArithmeticError) as exc:
            # e.g. a JSON number like 1e99999999999999999999 that Decimal cannot
            # hold: return a structured 422, never an HTTP 500.
            raise HTTPException(422, detail={
                "code": "invalid_formula",
                "message": "invalid numeric value in formula",
            }) from exc
        raw = driven_raw

        # Bounded ingredient count, checked before evaluation.
        if isinstance(raw, dict) and isinstance(raw.get("ingredients"), list):
            if len(raw["ingredients"]) > MAX_FORMULA_INGREDIENTS:
                raise HTTPException(422, detail={
                    "code": "too_many_ingredients",
                    "message": f"exceeds limit of {MAX_FORMULA_INGREDIENTS} "
                               f"formula ingredients",
                })

        try:
            rep = evaluate_formula(raw, st.registry)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail={
                "code": "invalid_formula",
                "message": str(exc),
            }) from exc

        return FormulaCheckResponse(
            evaluations=rep["evaluations"],
            data_updated_at=rep.get("data_updated_at"),
            registry_size=rep.get("registry_size", 0),
            coverage=rep["coverage"],
            disclaimer=DISCLAIMER,
            metadata=_metadata(st),
        )

    # ---- error handlers (use the current request's app-local state) ------

    def _st_for(request: Request) -> AppState:
        return getattr(request.app.state, "snapshot", None) or state

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        st = _st_for(request)
        detail = exc.detail
        if isinstance(detail, dict):
            body = _error(st, detail.get("code", "error"),
                          detail.get("message", str(exc)),
                          detail.get("detail"))
        elif isinstance(detail, list):
            # FastAPI/pydantic request-validation errors -> structured 4xx.
            body = _error(st, "invalid_request", "request validation failed",
                          "; ".join(f"{d.get('loc', [])[-1]}: {d.get('msg')}"
                                    for d in detail) if detail else None)
        else:
            body = _error(st, "error", str(detail or exc))
        return JSONResponse(status_code=exc.status_code,
                            content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _req_validation(request: Request, exc: RequestValidationError):
        st = _st_for(request)
        body = _error(st, "invalid_request", "request validation failed",
                      "; ".join(f"{e.get('loc', [])[-1]}: {e.get('msg')}"
                                for e in (exc.errors() or [])) or None)
        return JSONResponse(status_code=422, content=body.model_dump())

    return app


def _load_snapshot(path: str) -> tuple[Registry, dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"snapshot not found: {p}")
    reg = load(p)
    if len(reg) == 0:
        raise ValueError(f"snapshot has no substances: {p}")
    return reg, getattr(reg, "metadata", {}) or {}


def build_app_from_snapshot(path: str) -> FastAPI:
    """Load a real snapshot and build the app (fails loudly if unusable)."""
    registry, metadata = _load_snapshot(path)
    return create_app(registry, metadata)


def run() -> None:
    import uvicorn
    snapshot = os.environ.get("EU_REG_SNAPSHOT",
                              "data/processed/eu_reg_lists.sqlite")
    app = build_app_from_snapshot(snapshot)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("EU_REG_PORT", "8000")))


if __name__ == "__main__":
    run()
