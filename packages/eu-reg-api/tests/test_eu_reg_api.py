"""Tests for eu-reg-api (Phase 3, read-only Annex-II API).

These use an isolated in-memory registry (never the real data/processed) and
the FastAPI ``TestClient``. No downloads, no workspace dataset mutation.
"""

from __future__ import annotations

from pathlib import Path

import eu_reg_lists as erl
from eu_reg_api.main import build_app_from_snapshot, create_app
from fastapi.testclient import TestClient

DATA_UPDATED_AT = "2026-10-08T14:35:34.921319+00:00"
RETRIEVED_AT = "2026-10-08T14:35:34.921319+00:00"


def _registry():
    raw = {
        "source_kind": "cosing-annex-ii",
        "source_file": "COSING_Annex_II_v2.txt",
        "source_sha256": "abc",
        "row_reference": "COSING_Annex_II_v2.txt#49",
        "entry_number": 49,
        "reference_number": 47,
        "official_reference": 47,
        "accepted": True,
        "retrieved_at": RETRIEVED_AT,
    }
    benzene = erl.Substance(
        id="benzene", name="Benzene", aliases=["benzol"], cas="71-43-2",
        ec="200-753-7", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None,
                        resolution="cosing-annex-ii", raw=raw)],
        sources=["cosing-annex-ii"],
    )
    water = erl.Substance(id="water", name="Aqua", aliases=["water"],
                          cas="7732-18-5", ec="231-791-2",
                          determination="unknown", rules=[])
    dioxane = erl.Substance(
        id="1-4-dioxane", name="1,4-dioxane", aliases=["1,4-dioxane", "dioxane"],
        cas="123-91-1", ec="204-661-8", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"source_kind": "cosing-annex-ii",
                             "source_file": "COSING_Annex_II_v2.txt",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_II_v2.txt#1234",
                             "entry_number": 1234,
                             "reference_number": 1234,
                             "official_reference": 1234,
                             "accepted": True,
                             "retrieved_at": RETRIEVED_AT})],
        sources=["cosing-annex-ii"],
    )
    verified_sources = {
        "cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                            "accepted": True, "fetched": True,
                            "retrieval_error": None},
        "echa-svhc": {"kind": "echa-svhc", "present": False,
                      "accepted": False, "fetched": False,
                      "retrieval_error": "HTTPError"},
    }
    metadata = {
        "title": "test", "version": "0.2.0",
        "data_updated_at": DATA_UPDATED_AT,
        "content_hash": "testhash1234",
        "count_substances": 3, "count_verified_rules": 2,
        "count_identity_only": 1, "count_quarantined": 0,
        "verified_sources": verified_sources,
        "snapshot": verified_sources,
    }
    reg = erl.Registry([benzene, water, dioxane],
                       data_updated_at=DATA_UPDATED_AT,
                       verified_sources=verified_sources, metadata=metadata)
    return reg, metadata


def _client():
    reg, metadata = _registry()
    app = create_app(reg, metadata)
    return TestClient(app)


def test_health_reports_snapshot_identity():
    cli = _client()
    r = cli.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["content_hash"] == "testhash1234"
    assert body["data_updated_at"] == DATA_UPDATED_AT
    assert body["count_substances"] == 3
    assert body["count_verified_rules"] == 2
    assert body["coverage"]["has_annex_ii"] is True
    assert "echa-svhc" in body["coverage"]["not_included"]
    assert "echa-svhc" not in body["coverage"]["missing"]
    assert "compliance determination" in body["note"]


def test_search_by_cas_and_name():
    cli = _client()
    r = cli.get("/v1/substances", params={"q": "71-43-2"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["results"][0]["name"] == "Benzene"
    # substring name search
    r2 = cli.get("/v1/substances", params={"q": "benzen"})
    assert r2.status_code == 200
    assert body["metadata"]["data_updated_at"] == DATA_UPDATED_AT
    # no matches -> empty, not a clean determination
    r3 = cli.get("/v1/substances", params={"q": "zzzznotreal"})
    assert r3.status_code == 200
    assert r3.json()["count"] == 0
    assert r3.json()["results"] == []


def test_search_respects_limit_bound():
    cli = _client()
    r = cli.get("/v1/substances", params={"q": "a"})
    assert r.status_code == 200
    assert r.json()["limit"] == 25


def test_cas_lookup_returns_provenance():
    cli = _client()
    r = cli.get("/v1/substances/71-43-2")
    assert r.status_code == 200
    body = r.json()
    # envelope: substance + explicit membership + shared metadata.
    assert body["is_in_dataset"] is True
    assert body["substance"]["name"] == "Benzene"
    assert body["substance"]["determination"] == "banned"
    assert body["metadata"]["data_updated_at"] == DATA_UPDATED_AT
    assert body["metadata"]["coverage"]["has_annex_ii"] is True
    assert body["metadata"]["disclaimer"].startswith("Regulatory information")
    rule = body["substance"]["rules"][0]
    assert rule["status"] == "banned"
    assert rule["annex"] == "Annex II"
    assert rule["regulation"] == "(EC) 2009/1223"
    assert "ec.europa.eu" in rule["source_url"]
    # official_reference kept separate from row_reference
    assert rule["raw"]["official_reference"] == 47
    assert rule["raw"]["row_reference"] == "COSING_Annex_II_v2.txt#49"
    assert rule["raw"]["row_reference"] != f"#{rule['raw']['official_reference']}"
    assert "source_sha256" in rule["raw"]
    # source_updated_at stays None (no upstream date); NOT filled with retrieval.
    assert rule["source_updated_at"] is None
    assert rule["raw"]["retrieved_at"] == RETRIEVED_AT


def test_cas_lookup_unknown_is_404_structured():
    cli = _client()
    r = cli.get("/v1/substances/000-00-0")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert body["disclaimer"].startswith("Regulatory information")


def test_list_annex2_paginates_and_total():
    cli = _client()
    r = cli.get("/v1/lists/annex-ii", params={"limit": 1, "offset": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # benzene + dioxane
    assert len(body["entries"]) == 1
    assert body["entries"][0]["row"] == 1


def test_list_known_unavailable_is_422_coverage():
    cli = _client()
    r = cli.get("/v1/lists/svhc")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "coverage_unavailable"
    assert "not covered" in body["error"]["message"]


def test_list_unknown_name_is_404():
    cli = _client()
    r = cli.get("/v1/lists/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_list"


def test_check_flags_benzene_and_dioxane_with_citations():
    cli = _client()
    r = cli.get("/v1/check", params={"ingredients": "1,4-dioxane, Benzene"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "FLAGGED"
    assert body["n_banned"] == 2
    assert body["clean"] is False
    assert body["data_updated_at"] == DATA_UPDATED_AT
    # every result carries the same snapshot timestamp
    for res in body["results"]:
        assert res["data_updated_at"] == DATA_UPDATED_AT
    # first flagged result has citation
    flagged = [x for x in body["results"] if x["status"] == "banned"]
    assert flagged[0]["rules"][0]["regulation"] == "(EC) 2009/1223"
    assert "ec.europa.eu" in flagged[0]["rules"][0]["source_url"]
    assert body["disclaimer"].startswith("Regulatory information")


def test_check_unknown_ingredients_remain_unknown_not_clean():
    cli = _client()
    r = cli.get("/v1/check", params={"ingredients": "Aqua, TotallyFake123"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "NOT-FULLY-VERIFIED"
    assert body["clean"] is False
    for res in body["results"]:
        assert res["determination"] == "unknown"
    assert body["n_unverified"] == 2


def test_check_surfaces_prohibition_finding():
    """The label-path API result carries the scoped-prohibition classification
    so an unconditional ban (Benzene) is distinguishable from a scoped one."""
    cli = _client()
    r = cli.get("/v1/check", params={"ingredients": "Benzene"})
    body = r.json()
    assert body["verdict"] == "FLAGGED"
    assert body["n_banned"] == 1
    res = body["results"][0]
    assert res["prohibition_finding"] == "unambiguous"


def test_check_surfaces_entry70_for_siloxane():
    """The API check surfaces a source-bound REACH Annex XVII entry 70 finding
    for a D4/D5/D6 identity, distinct from and never replacing the CosIng rule."""
    import eu_reg_lists as erl
    d4 = erl.Substance(
        id="d4", name="Octamethylcyclotetrasiloxane; D4", cas="556-67-2",
        ec="209-136-7", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"source_kind": "cosing-annex-ii"})],
        sources=["cosing-annex-ii"])
    vs = {"cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                              "accepted": True, "fetched": True,
                              "retrieval_error": None},
          "echa-svhc": {"kind": "echa-svhc", "present": False,
                        "accepted": False, "fetched": False,
                        "retrieval_error": "HTTPError"},
          "reach-annex-xvii-entry70": {
              "kind": "reach-legal-act", "present": True, "accepted": True,
              "fetched": True, "retrieval_error": None,
              "files": {"OJ_L_202401328_EN.pdf": {
                  "sha256": ("2ae8024b749623971c03967ba6b829e8abaa0285c8fee1"
                             "b3e459c1a24d730966"),
                  "byte_len": 488977}},
              "data_updated_at": DATA_UPDATED_AT,
              "retrieved_at": RETRIEVED_AT,
              "mapping": {
                  "mapping_version": 1,
                  "document_sha256": ("2ae8024b749623971c03967ba6b829e8abaa028"
                                      "5c8fee1b3e459c1a24d730966"),
                  "supported_substances": ["D4", "D5", "D6"],
                  "supported_branches": ["washoff_cosmetic", "other_cosmetic"],
              },
          }}
    meta = {"title": "t", "version": "0.2.0", "data_updated_at": DATA_UPDATED_AT,
            "content_hash": "h", "count_substances": 1,
            "count_verified_rules": 1, "count_identity_only": 0,
            "count_quarantined": 0, "verified_sources": vs, "snapshot": vs}
    reg = erl.Registry([d4], data_updated_at=DATA_UPDATED_AT,
                       verified_sources=vs, metadata=meta)
    app = create_app(reg, meta)
    from fastapi.testclient import TestClient
    cli = TestClient(app)
    r = cli.get("/v1/check", params={"ingredients": "556-67-2"})
    body = r.json()
    res = body["results"][0]
    assert res["entry70"] is not None
    assert res["entry70"]["substance_short"] == "D4"
    assert res["entry70"]["state"] == "missing_context"
    assert res["entry70"]["source"]["celex"] == "32024R1328"
    # CosIng evidence preserved independently.
    assert res["status"] == "banned" and res["annex"] == "Annex II"
    assert body["verdict"] == "FLAGGED"


def test_check_agrees_with_ingredient_report():
    cli = _client()
    r = cli.get("/v1/check", params={"ingredients": "Benzene"})
    body = r.json()
    reg, _ = _registry()
    from eu_ingredient_linter import ingredient_report
    rep = ingredient_report("Benzene", reg)
    assert body["verdict"] == rep["verdict"]
    assert body["n_flagged"] == rep["n_flagged"]
    assert body["n_banned"] == rep["n_banned"]
    assert body["data_updated_at"] == rep["data_updated_at"]


def test_check_empty_and_tokenless_rejected():
    cli = _client()
    for inp in ["   ", ";;;"]:
        r = cli.get("/v1/check", params={"ingredients": inp})
        assert r.status_code == 422, inp
        assert r.json()["error"]["code"] == "no_ingredients"
    # empty string is a FastAPI validation error, still structured 422.
    r = cli.get("/v1/check", params={"ingredients": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("no_ingredients", "invalid_request")


def test_missing_snapshot_fails_startup(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        build_app_from_snapshot(str(tmp_path / "missing.sqlite"))


def test_requests_leave_snapshot_unchanged(tmp_path):
    """Calling the API must not modify the snapshot bytes (read-only)."""
    import eu_reg_lists as erl
    reg, metadata = _registry()
    # serialize to a temp sqlite to mimic a snapshot file
    import json
    import sqlite3
    p = tmp_path / "snap.sqlite"
    con = sqlite3.connect(str(p))
    con.execute("""CREATE TABLE substances (id TEXT PRIMARY KEY, name TEXT,
        aliases TEXT, cas TEXT, ec TEXT, e_number TEXT, ci TEXT,
        determination TEXT, sources TEXT)""")
    con.execute("""CREATE TABLE rules (substance_id TEXT, status TEXT, annex TEXT,
        regulation TEXT, source_url TEXT, source_updated_at TEXT, conditions TEXT,
        resolution TEXT, effective_date TEXT, raw TEXT)""")
    con.execute("CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT)")
    for s in reg.substances:
        con.execute("INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?)",
                    (s.id, s.name, json.dumps(s.aliases), s.cas, s.ec,
                     s.e_number, s.ci, s.determination, json.dumps(s.sources)))
        for r in s.rules:
            con.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (s.id, r.status, r.annex, r.regulation, r.source_url,
                         r.source_updated_at, r.conditions, r.resolution,
                         r.effective_date, json.dumps(r.raw)))
    con.execute("INSERT INTO metadata VALUES (?,?)", ("data_updated_at",
                json.dumps(DATA_UPDATED_AT)))
    con.execute("INSERT INTO metadata VALUES (?,?)", ("content_hash",
                json.dumps("testhash1234")))
    con.commit()
    before = p.read_bytes()
    reg2 = erl.load(p)
    app = create_app(reg2, reg2.metadata)
    cli = TestClient(app)
    cli.get("/health")
    cli.get("/v1/substances", params={"q": "71-43-2"})
    cli.get("/v1/check", params={"ingredients": "Benzene"})
    cli.get("/v1/lists/annex-ii")
    assert p.read_bytes() == before


# --- Fix 1: structured substance envelope + error metadata -----------------


def test_cas_lookup_error_404_when_unknown():
    cli = _client()
    r = cli.get("/v1/substances/000-00-0")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    # Shared metadata/coverage now included in the error envelope.
    assert body["metadata"]["data_updated_at"] == DATA_UPDATED_AT
    assert body["metadata"]["coverage"]["has_annex_ii"] is True
    assert body["disclaimer"].startswith("Regulatory information")


def test_cas_lookup_ambiguous_is_explicit_unresolved():
    # Build a registry where one CAS is shared by two substances (ambiguous).
    import eu_reg_lists as erl
    double_cas = "999-99-9"
    a = erl.Substance(id="a", name="AmbigA", cas=double_cas, determination="unknown",
                      rules=[])
    b = erl.Substance(id="b", name="AmbigB", cas=double_cas, determination="unknown",
                      rules=[])
    vs = {"cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                              "accepted": True}}
    meta = {"data_updated_at": DATA_UPDATED_AT, "content_hash": "hash2",
            "verified_sources": vs, "snapshot": vs}
    reg = erl.Registry([a, b], data_updated_at=DATA_UPDATED_AT,
                       verified_sources=vs, metadata=meta)
    cli = TestClient(create_app(reg, meta))
    r = cli.get(f"/v1/substances/{double_cas}")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "ambiguous_identifier"
    assert "unresolved" in body["error"]["message"]


def test_request_validation_error_includes_metadata():
    cli = _client()
    # limit outside the ge/le bound triggers a FastAPI validation error.
    r = cli.get("/v1/substances", params={"q": "x", "limit": 99999})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["metadata"]["data_updated_at"] == DATA_UPDATED_AT
    assert body["disclaimer"].startswith("Regulatory information")


# --- Fix 2: two-app isolation (no shared module global) --------------------


def _small_registry():
    import eu_reg_lists as erl
    only = erl.Substance(id="only", name="OnlySub", cas="111-11-1",
                         determination="unknown", rules=[])
    vs = {"cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                              "accepted": True}}
    meta = {"data_updated_at": "2099-01-01T00:00:00+00:00",
            "content_hash": "hash-B", "count_substances": 1,
            "count_verified_rules": 0, "verified_sources": vs, "snapshot": vs}
    reg = erl.Registry([only], data_updated_at="2099-01-01T00:00:00+00:00",
                       verified_sources=vs, metadata=meta)
    return reg, meta


def test_two_apps_are_isolated():
    regA, metaA = _registry()          # hash-A, 3 substances, benzene present
    regB, metaB = _small_registry()    # hash-B, 1 substance, no benzene
    cliA = TestClient(create_app(regA, metaA))
    cliB = TestClient(create_app(regB, metaB))

    # Health reflects each app's own snapshot.
    ha, hb = cliA.get("/health").json(), cliB.get("/health").json()
    assert ha["content_hash"] == "testhash1234"
    assert ha["count_substances"] == 3
    assert hb["content_hash"] == "hash-B"
    assert hb["count_substances"] == 1

    # Benzene exists only in A.
    assert cliA.get("/v1/substances/71-43-2").status_code == 200
    assert cliB.get("/v1/substances/71-43-2").status_code == 404

    # Lookup in B works, A doesn't have the B-only substance.
    assert cliB.get("/v1/substances/111-11-1").status_code == 200
    assert cliA.get("/v1/substances/111-11-1").status_code == 404

    # Check uses each app's own timestamp + coverage metadata.
    checkA = cliA.get("/v1/check", params={"ingredients": "Benzene"}).json()
    checkB = cliB.get("/v1/check", params={"ingredients": "OnlySub"}).json()
    assert checkA["data_updated_at"] == DATA_UPDATED_AT
    assert checkB["data_updated_at"] == "2099-01-01T00:00:00+00:00"

    # Error metadata is associated with the correct app.
    errA = cliA.get("/v1/substances/000-00-0").json()
    errB = cliB.get("/v1/substances/000-00-0").json()
    assert errA["metadata"]["data_updated_at"] == DATA_UPDATED_AT
    assert errB["metadata"]["data_updated_at"] == "2099-01-01T00:00:00+00:00"


# --- Fix 4: raw length enforced before tokenization ------------------------


def test_oversized_input_rejected_before_tokenization(monkeypatch):
    from eu_reg_api.main import MAX_INGREDIENT_TEXT_LENGTH
    cli = _client()
    # Very long single token (no commas) built to exceed the char limit.
    long_text = "x" * (MAX_INGREDIENT_TEXT_LENGTH + 1)
    # Spy: ensure ingredient_report is NOT called for oversized input.
    import eu_reg_api.main as m
    called = {"n": 0}
    real = m.ingredient_report
    def spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(m, "ingredient_report", spy)
    r = cli.get("/v1/check", params={"ingredients": long_text})
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("invalid_request", "too_many_ingredients")
    assert called["n"] == 0  # rejected before tokenization / linting


def test_input_at_length_boundary_passes():
    from eu_reg_api.main import MAX_INGREDIENT_TEXT_LENGTH
    cli = _client()
    # Exactly at the character boundary: a single unknown token, so it should
    # pass length validation and return 200 (NOT-FULLY-VERIFIED, never clean).
    token = "z" * MAX_INGREDIENT_TEXT_LENGTH
    r = cli.get("/v1/check", params={"ingredients": token})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "NOT-FULLY-VERIFIED"
    assert body["clean"] is False
    assert body["results"][0]["determination"] == "unknown"


def test_short_input_more_than_200_tokens_rejected_by_token_limit():
    from eu_reg_api.main import MAX_INGREDIENTS
    cli = _client()
    # 201 commas -> 202 empty-ish tokens, but we need real tokens: use commas.
    # A short string with many token separators exceeds the token limit.
    inp = ",".join(["x"] * (MAX_INGREDIENTS + 1))
    r = cli.get("/v1/check", params={"ingredients": inp})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "too_many_ingredients"


def test_punctuation_ingredients_still_work():
    cli = _client()
    r = cli.get("/v1/check", params={"ingredients": "1,4-dioxane, Benzene"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "FLAGGED"
    assert r.json()["n_banned"] == 2


# --- Fix 3: OpenAPI describes the actual typed error responses -------------


def test_openapi_declares_error_response_for_errors():
    reg, meta = _registry()
    app = create_app(reg, meta)
    schema = app.openapi()
    schemas = schema["components"]["schemas"]
    # The custom envelope (not FastAPI's HTTPValidationError) is used.
    assert "ErrorResponse" in schemas
    assert "HTTPValidationError" not in schemas
    props = set(schemas["ErrorResponse"]["properties"].keys())
    assert {"error", "disclaimer", "metadata"} <= props

    def ref_for(path, code):
        return schema["paths"][path]["get"]["responses"][code][
            "content"]["application/json"]["schema"]["$ref"]

    assert ref_for("/v1/substances/{cas}", "404") == "#/components/schemas/ErrorResponse"
    assert ref_for("/v1/lists/{list_name}", "404") == "#/components/schemas/ErrorResponse"
    assert ref_for("/v1/lists/{list_name}", "422") == "#/components/schemas/ErrorResponse"
    assert ref_for("/v1/check", "422") == "#/components/schemas/ErrorResponse"
    assert ref_for("/v1/substances", "422") == "#/components/schemas/ErrorResponse"


def test_saved_openapi_matches_live_schema():
    import json

    import yaml
    reg, meta = _registry()
    app = create_app(reg, meta)
    live = app.openapi()
    saved = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    # The saved file was generated from this live schema path; compare the
    # relevant contract pieces (stable, key-sorted comparison).
    def norm(o):
        return json.loads(json.dumps(o, sort_keys=True))
    assert norm(live["paths"]) == norm(saved["paths"])
    assert norm(live["components"]["schemas"]) == norm(saved["components"]["schemas"])


# --- Browser playground ----------------------------------------------------


def test_playground_root_serves_index():
    cli = _client()
    r = cli.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "EU Regulatory Substance Playground" in r.text
    assert "Regulatory information, not legal advice" in r.text


def test_playground_static_assets_served():
    cli = _client()
    for path in ("/static/playground.css", "/static/playground.js"):
        r = cli.get(path)
        assert r.status_code == 200, path
        assert r.content, path
    # index references the two assets so the UI loads them.
    index = cli.get("/").text
    assert "playground.css" in index
    assert "playground.js" in index


def test_playground_index_references_same_origin_api():
    cli = _client()
    # The JS makes same-origin /health and /v1/check calls.
    js = cli.get("/static/playground.js").text
    assert "/health" in js
    assert "/v1/check" in js
    assert "window.location.origin" in js  # no absolute external base URL
    # API routes/documentation remain intact beside the playground.
    assert cli.get("/docs").status_code == 200
    assert cli.get("/redoc").status_code == 200


def test_playground_does_not_mutate_snapshot(tmp_path):
    """Serving the playground is read-only; snapshot bytes unchanged."""
    import json
    import sqlite3

    import eu_reg_lists as erl
    reg, metadata = _registry()
    from eu_reg_api.main import create_app
    p = tmp_path / "snap.sqlite"
    con = sqlite3.connect(str(p))
    con.execute("""CREATE TABLE substances (id TEXT PRIMARY KEY, name TEXT,
        aliases TEXT, cas TEXT, ec TEXT, e_number TEXT, ci TEXT,
        determination TEXT, sources TEXT)""")
    con.execute("""CREATE TABLE rules (substance_id TEXT, status TEXT, annex TEXT,
        regulation TEXT, source_url TEXT, source_updated_at TEXT, conditions TEXT,
        resolution TEXT, effective_date TEXT, raw TEXT)""")
    con.execute("CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT)")
    for s in reg.substances:
        con.execute("INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?)",
                    (s.id, s.name, json.dumps(s.aliases), s.cas, s.ec,
                     s.e_number, s.ci, s.determination, json.dumps(s.sources)))
        for r in s.rules:
            con.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (s.id, r.status, r.annex, r.regulation, r.source_url,
                         r.source_updated_at, r.conditions, r.resolution,
                         r.effective_date, json.dumps(r.raw)))
    con.commit()
    before = p.read_bytes()
    reg2 = erl.load(p)
    cli = TestClient(create_app(reg2, reg2.metadata))
    cli.get("/")
    cli.get("/static/playground.js")
    cli.get("/static/playground.css")
    assert p.read_bytes() == before


# --- Annex IV / V / VI conditional positive-list endpoints ------------------
def _multi_list_client():
    """A registry with Annex II banned + Annex IV/V/VI conditional rules so the
    list endpoints and coverage report reflect the newly supported sources."""
    import eu_reg_lists as erl
    vs = {
        "cosing-annex-ii": {"kind": "cosing-annex", "present": True, "accepted": True},
        "cosing-annex-iv": {"kind": "cosing-annex", "present": True, "accepted": True},
        "cosing-annex-v": {"kind": "cosing-annex", "present": True, "accepted": True},
        "cosing-annex-vi": {"kind": "cosing-annex", "present": True, "accepted": True},
        "echa-svhc": {"kind": "echa-svhc", "present": False, "accepted": False},
    }
    benzene = erl.Substance(
        id="benzene", name="Benzene", cas="71-43-2", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"official_reference": 47, "row_reference": "f#49"})],
        sources=["cosing-annex-ii"])
    zno = erl.Substance(
        id="zinc-oxide", name="Zinc oxide", cas="1314-13-2", ci="CI 77947",
        determination="allowed_with_conditions",
        rules=[
            erl.Rule(status="allowed_with_conditions", annex="Annex IV",
                     regulation="(EU) 2017/1413",
                     source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                     source_updated_at=None, resolution="cosing-annex-iv",
                     conditions="Maximum concentration in ready for use preparation: 25%",
                     raw={"official_reference": 144, "row_reference": "IV#145",
                          "conditions_list": [{"field": "max_concentration",
                                               "label": "Maximum concentration in ready for use preparation",
                                               "value": "25%"}]}),
            erl.Rule(status="allowed_with_conditions", annex="Annex VI",
                     regulation="(EU) 2016/621",
                     source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                     source_updated_at=None, resolution="cosing-annex-vi",
                     conditions="Maximum concentration in ready for use preparation: 25%",
                     raw={"official_reference": "30a", "row_reference": "VI#29",
                          "conditions_list": [{"field": "max_concentration",
                                               "label": "Maximum concentration in ready for use preparation",
                                               "value": "25%"}]}),
        ],
        sources=["cosing-annex-iv", "cosing-annex-vi"])
    meta = {"data_updated_at": DATA_UPDATED_AT, "content_hash": "multi-list",
            "verified_sources": vs, "snapshot": vs}
    reg = erl.Registry([benzene, zno], data_updated_at=DATA_UPDATED_AT,
                       verified_sources=vs, metadata=meta)
    return TestClient(create_app(reg, meta))


def test_lists_annex_iv_v_vi_serve_conditional_rules():
    cli = _multi_list_client()
    # Coverage now reports the positive lists as available.
    cov = cli.get("/health").json()["coverage"]
    assert "cosing-annex-iv" in cov["available"]
    assert "cosing-annex-v" in cov["available"]
    assert "cosing-annex-vi" in cov["available"]
    # Each list endpoint returns real paginated entries with conditions.
    for lst in ("annex-iv", "annex-v", "annex-vi"):
        r = cli.get(f"/v1/lists/{lst}?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["list_name"] == lst
        assert isinstance(body["entries"], list)
    # Zinc oxide appears in both Annex IV and VI with its conditions retained.
    iv = cli.get("/v1/lists/annex-iv?limit=10").json()["entries"]
    vi = cli.get("/v1/lists/annex-vi?limit=10").json()["entries"]
    zno_iv = [e["substance"] for e in iv if e["substance"]["name"] == "Zinc oxide"]
    zno_vi = [e["substance"] for e in vi if e["substance"]["name"] == "Zinc oxide"]
    assert zno_iv and zno_vi
    assert zno_iv[0]["ci"] == "CI 77947"
    assert any(rule["annex"] == "Annex IV" for rule in zno_iv[0]["rules"])
    assert any(r["annex"] == "Annex VI" for r in zno_vi[0]["rules"])


def test_formula_preserves_positive_list_evidence_without_numeric_judgment():
    cli = _multi_list_client()
    r = cli.post("/v1/check-formula", json={
        "ingredients": [{"ingredient": "Zinc oxide", "percent": 10,
                         "unit": "%", "basis": "ready_for_use"}]})
    assert r.status_code == 200
    e = r.json()["evaluations"][0]
    # No numeric judgment is invented for the conditional positive list.
    assert e["numeric_comparison"] is None
    assert e["verdict"] in ("requires_context", "requires_manual_review")
    assert any(rule["annex"] in ("Annex IV", "Annex VI") for rule in e["rules"])


def test_check_parenthetical_ban_reaches_api():
    response = _client().get('/v1/check', params={'ingredients': 'Aqua, Parfum (contains Benzene)'})
    assert response.status_code == 200
    body = response.json()
    assert body['n_banned'] == 1 and body['verdict'] == 'FLAGGED'
    result = next(r for r in body['results'] if r['ingredient'] == 'benzene')
    assert result['derived_from'] == 'parfum (contains benzene)'
    assert result['regulation'] and result['source_url']
    assert result['data_updated_at'] == DATA_UPDATED_AT
