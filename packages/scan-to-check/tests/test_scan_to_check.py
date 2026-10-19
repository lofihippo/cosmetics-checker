"""Tests for the local scan-to-check lookup flow.


These run the REAL app (``create_app``) with a MOCK product-lookup client
(no network) and the REAL shared evaluation engine (``eu_ingredient_linter``).
They verify typed lookup outcomes, the editable-label check flow, leading-zero
barcode preservation, food-product exclusion, malformed/upstream errors, and the
shared engine's results against a loaded snapshot.

Product responses are fixtures only; the live snapshot is loaded read-only and
never fetched/modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "packages" / "eu-ingredient-linter"))
sys.path.insert(0, str(PACKAGE))

from eu_reg_lists import load  # noqa: E402
from scan_to_check.app import create_app  # noqa: E402
from scan_to_check.obf import (  # noqa: E402
    ProductResult,
    validate_barcode,
)

# Use the deterministic in-memory registry (not the gitignored production
# snapshot) so the scan flow tests run on a clean checkout.
HAS_SNAPSHOT = (ROOT / "data/processed/eu_reg_lists.sqlite").exists()


class StubClient:
    """Deterministic lookup client returning prebuilt ProductResults."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def lookup(self, barcode: str) -> ProductResult:
        self.calls.append(barcode)
        out = self.results.get(barcode)
        if out is None:
            return ProductResult(status="not_found", barcode=barcode,
                                 disclaimer="Regulatory information, not legal advice.")
        return out


def _beauty(barcode, ingredients, name="Fixture Cosmetic", brands="Fixture Brand"):
    return ProductResult(
        status="found_with_ingredients", barcode=barcode,
        product_type="beauty", name=name, brands=brands,
        ingredients_text=ingredients,
        source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        retrieved_at="2026-01-01T00:00:00+00:00",
        disclaimer="Regulatory information, not legal advice.")


@pytest.fixture
def client_snapshot():
    # Deterministic in-memory registry (see conftest.make_registry), so the scan
    # flow tests never depend on a gitignored production snapshot.
    from conftest import make_registry
    reg, _meta = make_registry()
    return reg


@pytest.fixture
def app_client(client_snapshot):
    stub = StubClient({})
    app = create_app(client_snapshot, client=stub)
    return TestClient(app), stub


def test_validate_barcode_preserves_leading_zeros():
    for raw, ok in [("0012345678901", True), ("000123456789", True),
                    ("1234567890123", True), ("00123", False),
                    ("1234", False), ("", False), ("abc", False), ("12-34", False)]:
        if ok:
            assert validate_barcode(raw) == raw  # unchanged, leading zeros kept
        else:
            with pytest.raises(ValueError):
                validate_barcode(raw)


def test_lookup_found_with_ingredients(app_client):
    client, stub = app_client
    stub.results = {"3560070791460": _beauty(
        "3560070791460", "AQUA, ALCOHOL, GLYCERIN")}
    r = client.get("/api/product/3560070791460")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "found_with_ingredients"
    assert d["product_type"] == "beauty"
    assert d["barcode"] == "3560070791460"
    # The scan-all endpoint normalises; the adapter preserves the input string.
    assert stub.calls and stub.calls[0] == "3560070791460"


def test_lookup_not_found(app_client):
    client, _ = app_client
    r = client.get("/api/product/9999999999999")
    assert r.status_code == 200
    assert r.json()["status"] == "not_found"


def test_lookup_missing_ingredients(app_client):
    client, stub = app_client
    stub.results = {"3560070791460": ProductResult(
        status="found_without_ingredients", barcode="3560070791460",
        product_type="beauty", name="No list", source_url="x",
        disclaimer="Regulatory information, not legal advice.")}
    d = client.get("/api/product/3560070791460").json()
    assert d["status"] == "found_without_ingredients"


def test_lookup_unsupported_food_category(app_client):
    """A food product (product_type food) is NOT checked with cosmetic rules."""
    client, stub = app_client
    stub.results = {"3017620422003": ProductResult(
        status="unsupported_product_category", barcode="3017620422003",
        product_type="food", name="Nutella", brands="Ferrero",
        ingredients_text="SUGAR, PALM OIL",
        source_url="https://world.openfoodfacts.org/product/3017620422003",
        disclaimer="Regulatory information, not legal advice.")}
    d = client.get("/api/product/3017620422003").json()
    assert d["status"] == "unsupported_product_category"
    assert d["product_type"] == "food"


def test_lookup_upstream_error_no_product(app_client):
    client, stub = app_client
    stub.results = {"3560070791460": ProductResult(
        status="upstream_error", barcode="3560070791460",
        error="HTTP 500", disclaimer="Regulatory information, not legal advice.")}
    d = client.get("/api/product/3560070791460").json()
    assert d["status"] == "upstream_error"
    assert d["error"] == "HTTP 500"
    assert d.get("ingredients_text") is None  # never an old product


def test_lookup_malformed_response(app_client):
    client, stub = app_client
    stub.results = {"3560070791460": ProductResult(
        status="malformed_response", barcode="3560070791460",
        error="JSON error", disclaimer="Regulatory information, not legal advice.")}
    d = client.get("/api/product/3560070791460").json()
    assert d["status"] == "malformed_response"


def test_check_reuses_shared_engine(app_client):
    client, _ = app_client
    r = client.post("/api/check", json={"ingredients": "AQUA, ALCOHOL, BENZENE"})
    assert r.status_code == 200
    rep = r.json()
    # The engine reports honest unknown/not-clean; Benzene is a known banned
    # substance, Aqua is unknown -> no clean claim.
    names = {res["cased_name"] for res in rep["results"]}
    assert "Benzene" in names
    assert rep["clean"] is False
    assert rep["disclaimer"].startswith("Regulatory information, not legal advice.")
    assert "snapshot_data_updated_at" in rep


def test_check_empty_rejected(app_client):
    client, _ = app_client
    r = client.post("/api/check", json={"ingredients": "   "})
    assert r.status_code == 422


def test_check_invalid_barcode_request(app_client):
    client, _ = app_client
    # A malformed (non-numeric / short) barcode -> 422 before any lookup.
    r = client.get("/api/product/00123")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_barcode"


def test_check_rejects_explicit_non_cosmetic_product(app_client):
    """Only the UI currently excludes food; the /api/check endpoint must ALSO
    reject an explicitly non-cosmetic product context so cosmetic rules are
    never applied to a food/petfood product."""
    client, _ = app_client
    for ptype in ("food", "petfood", "product"):
        r = client.post("/api/check", json={
            "ingredients": "AQUA, BENZENE",
            "product": {"product_type": ptype, "name": "Nutella",
                        "barcode": "3017620422003"},
        })
        assert r.status_code == 422, ptype
        assert r.json()["detail"]["code"] == "non_cosmetic_product", ptype


def test_check_accepts_manual_cosmetic_mode(app_client):
    """Manual cosmetic input (no product, or a beauty product) is a valid mode."""
    client, _ = app_client
    r = client.post("/api/check", json={"ingredients": "AQUA, ALCOHOL", "product": {}})
    assert r.status_code == 200
    r2 = client.post("/api/check", json={
        "ingredients": "AQUA, ALCOHOL",
        "product": {"product_type": "beauty", "name": "Fixture"}})
    assert r2.status_code == 200
    # ``mode`` is an explicit input method (manual paste / OCR draft / a fetched
    # lookup). It must NOT be rejected as a non-cosmetic product category, and it
    # is NOT itself a product category used to decide whether cosmetic rules apply.
    for mode in ("manual", "ocr", "lookup"):
        r3 = client.post("/api/check", json={
            "ingredients": "AQUA, ALCOHOL",
            "product": {"mode": mode}})
        assert r3.status_code == 200, mode
        assert r3.json()["product"]["mode"] == mode, mode


def test_check_rejects_unknown_invalid_mode(app_client):
    client, _ = app_client
    r = client.post("/api/check", json={
        "ingredients": "AQUA", "product": {"mode": "scan_pdf"}})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_mode"
    r2 = client.post("/api/check", json={
        "ingredients": "AQUA", "product": {"mode": 5}})
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "invalid_mode"


def test_check_manual_mode_with_food_product_category_still_rejected(app_client):
    """Even in a manual/OCR mode, an explicit non-cosmetic product CATEGORY is
    rejected: cosmetic rules must never apply to a food/petfood product."""
    client, _ = app_client
    r = client.post("/api/check", json={
        "ingredients": "AQUA, BENZENE",
        "product": {"mode": "manual", "product_type": "food", "name": "Nutella"}})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "non_cosmetic_product"


def test_check_rejects_non_object_product(app_client):
    client, _ = app_client
    r = client.post("/api/check", json={"ingredients": "AQUA", "product": [1]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_product"


def test_products_not_checked_twice_by_frontend(client_snapshot):
    """The engine is the single evaluator: /api/check runs ingredient_report,
    not a re-implemented matcher. Verify a result rule list is populated from the
    snapshot for a known banned substance."""
    client = TestClient(create_app(client_snapshot, client=StubClient({})))
    r = client.post("/api/check", json={"ingredients": "Zinc pyrithione"})
    rep = r.json()
    zinc = [x for x in rep["results"] if x["ingredient"] == "zinc pyrithione"][0]
    assert zinc["is_in_dataset"] is True
    assert zinc["status"] == "banned"
    assert any("Annex II" == rule.get("annex") for rule in zinc["rules"])


def test_check_parenthetical_ban_reaches_scanner(app_client):
    client, _ = app_client
    response = client.post('/api/check', json={'ingredients': 'Aqua, Fragrance (contains Zinc Pyrithione)'})
    assert response.status_code == 200
    body = response.json()
    assert body['n_banned'] == 1 and body['verdict'] == 'FLAGGED'
    result = next(r for r in body['results'] if r['ingredient'] == 'zinc pyrithione')
    assert result['derived_from'] == 'fragrance (contains zinc pyrithione)'
    assert result['regulation'] and result['source_url'] and result['data_updated_at']


def test_browser_security_headers_and_documentation_exemption(app_client):
    client, _ = app_client
    for path in ['/', '/static/index.html']:
        response = client.get(path)
        assert response.status_code == 200
        csp = response.headers['Content-Security-Policy']
        for directive in ["default-src 'self'", "frame-ancestors 'none'",
                          "worker-src 'self' blob:", "'wasm-unsafe-eval'",
                          "img-src 'self' blob: data:"]:
            assert directive in csp
        assert 'unsafe-inline' not in csp and "'unsafe-eval'" not in csp
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert '<script>' not in response.text and '<style>' not in response.text
    for path in ['/docs', '/docs/oauth2-redirect', '/redoc']:
        response = client.get(path)
        assert response.status_code == 200
        assert 'Content-Security-Policy' not in response.headers
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    for path in ['/static/scanner.js', '/static/scanner.css']:
        assert client.get(path).status_code == 200


@pytest.mark.parametrize('category', [False, 0, [], ['food'], {'category': 'food'}])
def test_check_rejects_non_string_product_category(app_client, category):
    client, _ = app_client
    response = client.post('/api/check', json={'ingredients': 'Zinc Pyrithione',
                                               'product': {'product_type': category}})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_product_type'


@pytest.mark.parametrize('product', [False, 0, [], ''])
def test_falsy_product_is_not_silently_converted_to_manual(app_client, product):
    client, _ = app_client
    response = client.post('/api/check', json={'ingredients': 'Aqua', 'product': product})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_product'


@pytest.mark.parametrize('product', [None, {}, {'mode': 'manual'}, {'mode': 'ocr'},
                                     {'product_type': None}, {'product_type': 'beauty'},
                                     {'mode': 'lookup', 'product_type': 'cosmetic'}])
def test_check_valid_optional_product_context(app_client, product):
    client, _ = app_client
    assert client.post('/api/check', json={'ingredients': 'Aqua', 'product': product}).status_code == 200


def test_check_rejects_oversized_unrelated_body_before_engine(app_client, monkeypatch):
    import scan_to_check.app as module
    client, _ = app_client
    def unexpected(*args):
        pytest.fail('oversized request reached the checking engine')
    monkeypatch.setattr(module, 'ingredient_report', unexpected)
    response = client.post('/api/check', json={'ingredients': 'Aqua', 'unused': 'x' * 200_000})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'request_too_large'


def test_stream_limit_precedes_json_decoding_even_without_trusted_length(monkeypatch):
    import asyncio
    import scan_to_check.app as module
    from starlette.requests import Request
    from fastapi import HTTPException
    received = []
    async def receive():
        received.append(True)
        assert len(received) <= 3, 'reader continued after crossing the limit'
        return {'type': 'http.request', 'body': b' ' * 80_000, 'more_body': True}
    def unexpected(*args, **kwargs):
        pytest.fail('oversized request reached JSON decoding')
    monkeypatch.setattr(module.json, 'loads', unexpected)
    for headers in [[], [(b'content-length', b'1')]]:
        received.clear()
        request = Request({'type': 'http', 'headers': headers}, receive=receive)
        with pytest.raises(HTTPException) as error:
            asyncio.run(module._read_check_payload(request))
        assert error.value.detail['code'] == 'request_too_large'
        assert len(received) == 3


@pytest.mark.parametrize('body', [b'{bad', b'\xff', b'[' * 100_000,
    b'{"ingredients":"Aqua","product":{"value":NaN}}',
    b'{"ingredients":"Aqua","product":{"value":1e999}}',
    b'{"ingredients":"Aqua","product":{"value":' + b'[' * 40 + b'0' + b']' * 40 + b'}}'])
def test_check_rejects_invalid_json_and_excessive_nesting(app_client, body):
    client, _ = app_client
    response = client.post('/api/check', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_json'


@pytest.mark.parametrize('payload', [[], 0, False, 'Aqua'])
def test_check_requires_json_object(app_client, payload):
    client, _ = app_client
    response = client.post('/api/check', json=payload)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_payload'
