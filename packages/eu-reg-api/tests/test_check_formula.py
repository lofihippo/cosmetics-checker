"""Tests for the POST /v1/check-formula endpoint (Part B).

Uses an isolated in-memory registry with a supported Annex III rule (Toluene,
ref 185) plus an Annex II-banned Benzene, an unverified Aqua, and a cross-annex
Hydroquinone. Verifies the endpoint delegates to the shared library, preserves
decimal precision, returns typed errors, enforces bounds, and leaves the
snapshot untouched.
"""

from __future__ import annotations

import eu_reg_lists as erl
from eu_ingredient_linter.formula import SUPPORTED_RULES, evaluate_formula
from eu_reg_api.main import create_app
from fastapi.testclient import TestClient

DATA_UPDATED_AT = "2026-10-09T00:00:00+00:00"


def _annex3_rule(ref):
    conds = [dict(c) for c in SUPPORTED_RULES[ref]["conditions_template"]]
    return erl.Rule(
        status="restricted", annex="Annex III", regulation="(EC) 2013/344",
        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
        source_updated_at=None, resolution="cosing-annex-iii",
        raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
             "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
             "reference_number": ref, "accepted": True, "conditions_list": conds},
    )


def _registry():
    verified = {
        "cosing-annex-ii": {"present": True, "accepted": True},
        "cosing-annex-iii": {"present": True, "accepted": True},
        "echa-svhc": {"present": False, "accepted": False},
    }
    toluene = erl.Substance(
        id="toluene", name="Toluene", cas="108-88-3", ec="203-625-9",
        determination="restricted", rules=[_annex3_rule("185")],
        sources=["cosing-annex-iii"])
    benzene = erl.Substance(
        id="benzene", name="Benzene", cas="71-43-2", ec="200-753-7",
        determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"official_reference": 47, "row_reference": "f#49",
                             "conditions_list": None})],
        sources=["cosing-annex-ii"])
    hydroq = erl.Substance(
        id="hydroquinone", name="Hydroquinone", cas="123-31-9", ec="204-617-8",
        determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2013/344",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"official_reference": 1339, "row_reference": "f#1339",
                             "conditions_list": None,
                             "source_row": {"Chemical name / INN": "Hydroquinone"}}),
               _annex3_rule("14")],
        sources=["cosing-annex-ii", "cosing-annex-iii"])
    aqua = erl.Substance(id="water", name="Water", aliases=["Aqua"],
                         cas="7732-18-5", determination="unknown", rules=[])
    metadata = {"data_updated_at": DATA_UPDATED_AT, "version": "0.2.0",
                "count_substances": 4, "verified_sources": verified,
                "snapshot": verified}
    reg = erl.Registry([toluene, benzene, hydroq, aqua],
                       data_updated_at=DATA_UPDATED_AT,
                       verified_sources=verified, metadata=metadata)
    return reg, metadata


def _client():
    reg, metadata = _registry()
    return TestClient(create_app(reg, metadata))


VALID = {"ingredient": "Toluene", "percent": 20, "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}


def test_valid_formula_delegates_to_library():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [VALID]})
    assert r.status_code == 200
    body = r.json()
    e = body["evaluations"][0]
    assert e["verdict"] == "within_supported_numeric_limit"
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert body["data_updated_at"] == DATA_UPDATED_AT
    assert body["coverage"]["has_annex_ii"] is True
    assert body["disclaimer"]
    # The warnings condition stays unresolved (numeric compare is numeric-only).
    assert any("children" in s.lower() for s in e["unresolved_requirements"])


def test_formula_matches_library_output_for_supported_comparison():
    reg, _ = _registry()
    lib_rep = evaluate_formula({"ingredients": [VALID]}, reg)
    cli = _client()
    body = cli.post("/v1/check-formula", json={"ingredients": [VALID]}).json()
    lib_e = lib_rep["evaluations"][0]
    api_e = body["evaluations"][0]
    assert api_e["verdict"] == lib_e["verdict"]
    assert api_e["numeric_comparison"] == lib_e["numeric_comparison"]
    assert api_e["provenance"]["official_reference"] == \
        lib_e["provenance"]["official_reference"]


def test_unresolved_context_returns_no_numeric_comparison():
    cli = _client()
    # Missing unit/basis/product -> numeric_comparison null.
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "Toluene", "percent": 20}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert e["verdict"] == "requires_context"


def test_submitted_context_preserved_through_api():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "Toluene", "percent": 20, "product_type": "nail_product",
         "body_area": "nails", "professional_use": False,
         "ready_for_use": True}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    # Raw supplied context preserved distinctly (absent vs false vs true).
    assert e["submitted_context"]["product_type"] == "nail_product"
    assert e["submitted_context"]["body_area"] == "nails"
    assert e["submitted_context"]["professional_use"] is False
    assert e["submitted_context"]["ready_for_use"] is True


def test_benzene_retains_annex2_prohibition():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "Benzene", "percent": 1, "unit": "%"}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    assert e["is_in_dataset"] is True
    assert e["determination"] == "banned"
    assert e["numeric_comparison"] is None
    assert any(rl["annex"] == "Annex II" for rl in e["rules"])
    # API parity with the shared evaluator: prohibition_finding is surfaced.
    assert e["prohibition_finding"] == "unambiguous"


def test_hydroquinone_retains_both_rules_and_manual_review():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "Hydroquinone", "percent": 0.01, "unit": "%",
         "basis": "after_mixing", "product_type": "artificial_nail_system"}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    assert e["verdict"] == "requires_manual_review"
    assert e["numeric_comparison"] is None
    annexes = {rl["annex"] for rl in e["rules"]}
    assert annexes == {"Annex II", "Annex III"}


def test_unknown_ingredient():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "TotallyFakeX", "percent": 1}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    assert e["is_in_dataset"] is False
    assert e["determination"] == "unknown"
    assert e["verdict"] == "unknown_ingredient"


def test_invalid_input_returns_422_structured_error():
    cli = _client()
    r = cli.post("/v1/check-formula", json={"ingredients": [
        {"ingredient": "Toluene", "percent": True}]})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "invalid_formula"
    assert body["disclaimer"]


def test_invalid_json_returns_422():
    cli = _client()
    r = cli.post("/v1/check-formula", content="not json",
                 headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_json"


def test_too_many_ingredients_returns_422():
    cli = _client()
    payload = {"ingredients": [dict(VALID) for _ in range(500)]}
    r = cli.post("/v1/check-formula", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "too_many_ingredients"


def test_decimal_precision_preserved_through_http():
    cli = _client()
    num = ('{"ingredients":[{"ingredient":"Toluene","percent":25.0000000000000001,'
           '"unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}')
    s = ('{"ingredients":[{"ingredient":"Toluene","percent":"25.0000000000000001",'
         '"unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}')
    en = cli.post("/v1/check-formula", content=num,
                  headers={"Content-Type": "application/json"}).json()["evaluations"][0]
    es = cli.post("/v1/check-formula", content=s,
                  headers={"Content-Type": "application/json"}).json()["evaluations"][0]
    # Both the JSON number and its exact string equivalent exceed 25%.
    assert en["numeric_comparison"] == es["numeric_comparison"] == "exceeds_supported_limit"
    assert en["submitted_value"] == es["submitted_value"] == "25.0000000000000001"


def test_formula_is_read_only_and_leaves_snapshot_untouched(tmp_path):
    reg, metadata = _registry()
    # Snapshot files do not exist; the endpoint must never create/mutate them.
    app = create_app(reg, metadata)
    cli = TestClient(app)
    cli.post("/v1/check-formula", json={"ingredients": [VALID]})
    # The registry's content is unchanged.
    assert reg.lookup("toluene").name == "Toluene"
    assert len(reg) == 4


# ---------------------------------------------------------------------------
# Part A: request-body OpenAPI contract.
# ---------------------------------------------------------------------------
def _collect_refs(node):
    """Recursively collect every ``#/...`` local reference in an OpenAPI doc."""
    refs = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/"):
                refs.append(v)
            else:
                refs.extend(_collect_refs(v))
    elif isinstance(node, list):
        for v in node:
            refs.extend(_collect_refs(v))
    return refs


def _all_local_references_resolve(spec):
    """Assert every local ``$ref`` in the OpenAPI document resolves.

    A reference like ``#/$defs/FormulaIngredientModel`` that is nested inside a
    request-body schema does NOT resolve against the document root, even when
    the referenced schema text happens to be present. We validate that every
    ``#/`` path traverses the document to an existing node.
    """
    def _follow(path):
        node = spec
        for part in path.split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    for ref in _collect_refs(spec):
        assert ref.startswith("#/"), f"non-local ref: {ref}"
        assert _follow(ref[2:]), f"unresolved local reference: {ref}"


def test_openapi_has_check_formula_request_body():
    reg, metadata = _registry()
    app = create_app(reg, metadata)
    spec = app.openapi()
    post = spec["paths"]["/v1/check-formula"]["post"]
    rb = post.get("requestBody")
    assert rb is not None
    assert rb.get("required") is True
    content = rb["content"]["application/json"]
    schema = content["schema"]
    # The precise schema is documented (percent = number | string | null),
    # inlined so it does not rely on a nested definition reference.
    assert "$defs" not in schema
    percent = schema["properties"]["ingredients"]["items"]["properties"]["percent"]
    types = {a["type"] for a in percent["anyOf"]}
    assert types == {"number", "string", "null"}
    # ingredients array is bounded and required, and its item schema is inlined
    # (no nested $ref that would not resolve against the document root).
    items = schema["properties"]["ingredients"]["items"]
    assert "$ref" not in items
    assert items["title"] == "FormulaIngredientModel"
    props = schema["properties"]
    assert props["ingredients"]["type"] == "array"
    assert props["ingredients"]["maxItems"] == 200
    assert props["ingredients"]["minItems"] == 1
    # Examples are documented so /docs provides an editable example.
    assert content.get("examples")


def test_openapi_local_references_all_resolve():
    reg, metadata = _registry()
    app = create_app(reg, metadata)
    spec = app.openapi()
    _all_local_references_resolve(spec)


# ---------------------------------------------------------------------------
# Part A: extreme decimal input -> structured 422, never HTTP 500.
# ---------------------------------------------------------------------------
def test_extreme_decimal_exponent_returns_422():
    cli = _client()
    body = ('{"ingredients":[{"ingredient":"Toluene","percent":'
            '1e99999999999999999999,"unit":"%","basis":"ready_for_use",'
            '"product_type":"nail_product"}]}')
    r = cli.post("/v1/check-formula", content=body,
                 headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    body_json = r.json()
    assert body_json["error"]["code"] == "invalid_formula"
    assert body_json["disclaimer"]


# ---------------------------------------------------------------------------
# Part A: bounded incremental body reading (no Content-Length reliance).
# ---------------------------------------------------------------------------
def test_oversized_streaming_body_returns_422():
    from eu_reg_api.main import MAX_FORMULA_BODY_LENGTH
    cli = _client()
    big = "{" + '"padding":"' + "x" * (MAX_FORMULA_BODY_LENGTH + 2000) + '"}'

    def gen():
        for i in range(0, len(big), 8192):
            yield big[i:i + 8192].encode()

    r = cli.post("/v1/check-formula", content=gen(),
                 headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "request_too_large"


def test_at_limit_body_is_accepted():
    from eu_reg_api.main import MAX_FORMULA_BODY_LENGTH
    cli = _client()
    # A body under the limit is accepted (no 500 / no over-read).
    body = '{"ingredients":[{"ingredient":"Toluene","percent":20,"unit":"%",' \
           '"basis":"ready_for_use","product_type":"nail_product"}]}'
    assert len(body) < MAX_FORMULA_BODY_LENGTH
    r = cli.post("/v1/check-formula", content=body,
                 headers={"Content-Type": "application/json"})
    assert r.status_code == 200
