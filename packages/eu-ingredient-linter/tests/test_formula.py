"""Tests for the corrected structured-formula evaluator (Part A of this task).

Covers:
  * strict applicability gating (unit/basis/product context) BEFORE any numeric
    comparison — missing/unsupported/contradictory context => numeric_comparison
    is null;
  * preservation of all regulatory evidence independently of numeric support
    (Annex II, unsupported/multiple III rules, provenance);
  * unresolved conditions are always surfaced (a "within limit" is numeric-only);
  * robust input validation (no float round-trip, exact decimal parity, duplicate
    resolved identities, malformed strings/booleans/non-finite/out-of-range);
  * report-level snapshot metadata + coverage;
  * CLI exit code 2 on invalid input.

Uses an in-memory synthetic registry (never the real data/processed bundle) for
deterministic rule fingerprints, plus the real snapshot for a provenance
integration check.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

import eu_reg_lists as erl
from eu_ingredient_linter.formula import evaluate_formula, evaluate_formula_file

ROOT = Path(__file__).resolve().parents[3]
PROCESSED = ROOT / "data" / "processed"


def _annex3_rule(ref, conditions_list) -> erl.Rule:
    return erl.Rule(
        status="restricted", annex="Annex III", regulation="(EC) 2009/1223",
        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
        source_updated_at=None, resolution="cosing-annex-iii",
        raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
             "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
             "reference_number": ref, "accepted": True,
             "conditions_list": conditions_list},
    )


def _registry():
    """A synthetic registry covering the supported rules + complex + unknown."""
    from eu_ingredient_linter.formula import SUPPORTED_RULES

    def rule_for(ref):
        return _annex3_rule(ref, [dict(c) for c in SUPPORTED_RULES[ref]["conditions_template"]])

    def sub(name, ref, **kw):
        return erl.Substance(name=name, id=name.lower().replace(" ", "-"), **kw,
                             determination="restricted",
                             rules=[rule_for(ref)], sources=["cosing-annex-iii"])

    toluene = sub("Toluene", "185", cas="108-88-3", ec="203-625-9")
    sodium = sub("Sodium nitrite", "17", cas="7632-00-0", ec="231-555-9")
    oxalic = sub("Oxalic acid, its esters and alkaline salts", "3",
                 aliases=["OXALIC ACID"], cas=None)
    hydroq = erl.Substance(
        id="hydroquinone", name="Hydroquinone", cas="123-31-9", ec="204-617-8",
        determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2013/344",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"official_reference": 1339, "row_reference": "f#1339",
                             "conditions_list": None,
                             "source_row": {"Chemical name / INN":
                                            "1,4-Dihydroxybenzene (Hydroquinone), "
                                            "with the exception of entry 14 in Annex III"}}),
               rule_for("14")],
        sources=["cosing-annex-ii", "cosing-annex-iii"])
    thiogly = erl.Substance(
        id="thioglycolic-acid", name="Thioglycolic acid and its salts",
        cas="68-11-1", determination="restricted",
        rules=[_annex3_rule("2a", [
            {"field": "product_types", "label": "Product type", "value": "(a) Hair products"},
            {"field": "max_concentration", "label": "Maximum concentration",
             "value": "(a) (i) 8%    (ii) 11%"},
            {"field": "other", "label": "Other", "value": "pH 7 to 9.5"}])],
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
    aqua = erl.Substance(id="water", name="Water", aliases=["Aqua"],
                         cas="7732-18-5", determination="unknown", rules=[])
    return erl.Registry([toluene, sodium, oxalic, hydroq, thiogly, benzene, aqua],
                        data_updated_at="2026-10-09T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-ii": {"present": True, "accepted": True},
                            "cosing-annex-iii": {"present": True, "accepted": True},
                            "echa-svhc": {"present": False, "accepted": False},
                        })


def _valid_formula(name="Toluene", percent=20, basis="ready_for_use",
                   product="nail_product", unit="%"):
    return {"ingredients": [{"ingredient": name, "percent": percent, "unit": unit,
                             "basis": basis, "product_type": product}]}


# ---------------------------------------------------------------------------
# Input contract validation.
# ---------------------------------------------------------------------------
def test_rejects_boolean_percentage():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": True}]}, _registry())


def test_rejects_non_finite_percentage():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": float("inf")}]}, _registry())
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": float("nan")}]}, _registry())


def test_rejects_out_of_range_percentage():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": -1}]}, _registry())
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": 110}]}, _registry())


def test_rejects_malformed_numeric_string():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": "20%%%"}]}, _registry())


def test_rejects_missing_name():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": [{"percent": 1}]}, _registry())


def test_rejects_empty_ingredients():
    with pytest.raises(ValueError):
        evaluate_formula({"ingredients": []}, _registry())


def test_rejects_non_dict_top_level():
    with pytest.raises(ValueError):
        evaluate_formula("not a dict", _registry())


def test_rejects_duplicate_resolved_identity_name_plus_cas():
    # "Toluene" and "108-88-3" resolve to the SAME substance -> reject.
    with pytest.raises(ValueError, match="duplicate resolved identity"):
        evaluate_formula({"ingredients": [{"ingredient": "Toluene", "percent": 1},
                                          {"ingredient": "108-88-3", "percent": 2}]}, _registry())


def test_rejects_duplicate_resolved_identity_name_plus_alias():
    with pytest.raises(ValueError, match="duplicate resolved identity"):
        evaluate_formula({"ingredients": [{"ingredient": "Oxalic acid, its esters and alkaline salts", "percent": 1},
                                          {"ingredient": "OXALIC ACID", "percent": 2}]}, _registry())


def test_missing_concentration_is_allowed_but_unevaluated():
    r = evaluate_formula({"ingredients": [{"ingredient": "Toluene", "unit": "%",
                                           "basis": "ready_for_use",
                                           "product_type": "nail_product"}]}, _registry())
    e = r["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert e["verdict"] == "requires_context"


# ---------------------------------------------------------------------------
# Exact decimal parity (no float round-trip).
# ---------------------------------------------------------------------------
def test_json_number_and_string_decimal_agree(tmp_path):
    reg = _registry()
    pnum = tmp_path / "num.json"
    pstr = tmp_path / "str.json"
    pnum.write_text('{"ingredients":[{"ingredient":"Toluene","percent":25.0000000000000001,'
                    '"unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}')
    pstr.write_text('{"ingredients":[{"ingredient":"Toluene","percent":"25.0000000000000001",'
                    '"unit":"%","basis":"ready_for_use","product_type":"nail_product"}]}')
    en = evaluate_formula_file(str(pnum), reg)["evaluations"][0]
    es = evaluate_formula_file(str(pstr), reg)["evaluations"][0]
    assert en["numeric_comparison"] == es["numeric_comparison"] == "exceeds_supported_limit"
    assert en["submitted_value"] == es["submitted_value"]


# ---------------------------------------------------------------------------
# Strict applicability gating (numeric_comparison must be null).
# ---------------------------------------------------------------------------
def test_unsupported_unit_no_numeric():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "ppm", "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert e["verdict"] == "requires_context"
    assert any("unit" in s.lower() for s in e["reasons"])


def test_missing_unit_no_numeric():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert any("unit" in s.lower() for s in e["reasons"])


def test_missing_basis_no_numeric_not_assumed():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "%", "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert e["basis"] is None
    assert any("basis" in s.lower() for s in e["reasons"])


def test_negated_product_name_no_numeric():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "not a nail product"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert any("product" in s.lower() for s in e["reasons"])


def test_basis_prefix_not_assumed():
    # 'ready' is a prefix of 'ready_for_use'; it must NOT substring-match.
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    e2 = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                            "unit": "%", "basis": "ready",
                                            "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert e2["numeric_comparison"] is None


def test_contradictory_ready_for_use_false_no_numeric():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor",
                                           "ready_for_use": False}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert any("ready_for_use is false" in s for s in e["reasons"])


# ---------------------------------------------------------------------------
# Supported numeric evaluation (below / at / above).
# ---------------------------------------------------------------------------
def test_below_limit_is_within_numeric_only():
    e = evaluate_formula(_valid_formula(percent=20), _registry())["evaluations"][0]
    assert e["verdict"] == "within_supported_numeric_limit"
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert e["rule_status"] == "restricted"
    assert e["resolved_name"] == "Toluene"
    # The numeric comparison is NOT clean/compliant; the warnings stay unresolved.
    assert any("children" in s.lower() for s in e["unresolved_requirements"])


def test_exactly_at_limit_is_within():
    e = evaluate_formula(_valid_formula(percent=25), _registry())["evaluations"][0]
    assert e["numeric_comparison"] == "within_supported_numeric_limit"


def test_above_limit_exceeds():
    e = evaluate_formula(_valid_formula(percent=30), _registry())["evaluations"][0]
    assert e["numeric_comparison"] == "exceeds_supported_limit"
    assert e["verdict"] == "exceeds_supported_limit"


# ---------------------------------------------------------------------------
# Unresolved conditions surfaced regardless of numeric outcome.
# ---------------------------------------------------------------------------
def test_toluene_warnings_stay_unresolved():
    e = evaluate_formula(_valid_formula(percent=20), _registry())["evaluations"][0]
    assert any("children" in s.lower() for s in e["unresolved_requirements"])


def test_sodium_nitrite_other_restriction_stays_unresolved():
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite", "percent": 0.1,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor"}]}, _registry())["evaluations"][0]
    assert any("amine" in s.lower() for s in e["unresolved_requirements"])


def test_professional_use_requirement_is_unresolved():
    e = evaluate_formula({"ingredients": [{"ingredient": "OXALIC ACID", "percent": 3,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "hair_product"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert any("professional-use" in s.lower() for s in e["unresolved_requirements"])


def test_oxalic_professional_use_absent_does_not_invalidate_numeric():
    # professional use is an unevaluated context requirement, not a hard gate.
    e = evaluate_formula({"ingredients": [{"ingredient": "OXALIC ACID", "percent": 3,
                                           "unit": "%", "basis": "ready_for_use",
                                           "product_type": "hair_product"}]}, _registry())["evaluations"][0]
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert any("professional-use" in s.lower() for s in e["unresolved_requirements"])


# ---------------------------------------------------------------------------
# Complex rule + cross-annex exception -> manual review, evidence preserved.
# ---------------------------------------------------------------------------
def test_complex_rule_requires_manual_review_and_preserves_rule():
    e = evaluate_formula({"ingredients": [{"ingredient": "Thioglycolic acid and its salts",
                                           "percent": 3, "unit": "%"}]}, _registry())["evaluations"][0]
    assert e["verdict"] == "requires_manual_review"
    assert e["numeric_comparison"] is None
    assert e["resolved_name"] == "Thioglycolic acid and its salts"
    # All identified rules retained as evidence.
    assert any(r["annex"] == "Annex III" for r in e["rules"])


def test_cross_annex_exception_retains_both_rules():
    e = evaluate_formula({"ingredients": [{"ingredient": "Hydroquinone", "percent": 0.01,
                                           "unit": "%", "basis": "after_mixing",
                                           "product_type": "artificial_nail_system"}]}, _registry())["evaluations"][0]
    assert e["verdict"] == "requires_manual_review"
    assert e["numeric_comparison"] is None
    annexes = {r["annex"] for r in e["rules"]}
    assert annexes == {"Annex II", "Annex III"}
    assert any("exception" in s.lower() for s in e["reasons"])
    # The Annex II scope/exception text is retained as evidence.
    assert any("exception of entry 14" in str(r) for r in e["rules"])


# ---------------------------------------------------------------------------
# Benzene / Aqua / unknown.
# ---------------------------------------------------------------------------
def test_benzene_retains_annex2_prohibition():
    e = evaluate_formula({"ingredients": [{"ingredient": "Benzene", "percent": 1,
                                           "unit": "%"}]}, _registry())["evaluations"][0]
    assert e["is_in_dataset"] is True
    assert e["determination"] == "banned"
    assert e["numeric_comparison"] is None
    assert any(r["annex"] == "Annex II" for r in e["rules"])
    assert any("Annex II" in s for s in e["reasons"])


def test_aqua_recognised_unverified_distinct_from_unknown():
    e = evaluate_formula({"ingredients": [{"ingredient": "Aqua", "percent": 5,
                                           "unit": "%"}]}, _registry())["evaluations"][0]
    assert e["is_in_dataset"] is True
    assert e["determination"] == "unknown"
    assert e["rules"] == []
    assert e["verdict"] == "requires_manual_review"


def test_unknown_ingredient_is_unknown():
    e = evaluate_formula({"ingredients": [{"ingredient": "TotallyFakeIngredientX",
                                           "percent": 1}]}, _registry())["evaluations"][0]
    assert e["is_in_dataset"] is False
    assert e["determination"] == "unknown"
    assert e["verdict"] == "unknown_ingredient"
    assert e["numeric_comparison"] is None


# ---------------------------------------------------------------------------
# Evidence / provenance / serialization.
# ---------------------------------------------------------------------------
def test_report_has_snapshot_metadata_and_coverage():
    r = evaluate_formula({"ingredients": [{"ingredient": "Aqua", "percent": 5,
                                           "unit": "%"}]}, _registry())
    assert r["data_updated_at"] == "2026-10-09T00:00:00+00:00"
    assert r["coverage"]["has_annex_ii"] is True
    assert "cosing-annex-ii" in r["coverage"]["available"]
    assert "echa-svhc" in r["coverage"]["not_included"]
    assert "echa-svhc" not in r["coverage"]["missing"]
    assert r["disclaimer"]


def test_report_is_json_serializable():
    r = evaluate_formula(_valid_formula(percent=20), _registry())
    s = json.dumps(r)
    assert "Toluene" in s
    assert "within_supported_numeric_limit" in s
    assert "coverage" in s


def test_provenance_carries_source_reference_and_timestamp():
    e = evaluate_formula(_valid_formula(percent=20), _registry())["evaluations"][0]
    prov = e["provenance"]
    assert prov["official_reference"] == "185"
    assert prov["row_reference"] == "f.txt#185"
    assert prov["data_updated_at"] == "2026-10-09T00:00:00+00:00"
    assert prov["conditions_fingerprint"]
    assert prov["source_sha256"] == "abc"
    assert e["source_rule"]["status"] == "restricted"


def test_stale_conditions_fingerprint_invalidates_mapping():
    reg = _registry()
    for s in reg.substances:
        if s.name == "Toluene":
            for r in s.rules:
                r.raw["conditions_list"][0]["value"] = "Nail products (changed)"
    e = evaluate_formula(_valid_formula(percent=20), reg)["evaluations"][0]
    assert e["verdict"] == "requires_manual_review"
    assert any("fingerprint" in s.lower() for s in e["reasons"])


# ---------------------------------------------------------------------------
# CLI JSON-file input + exit codes.
# ---------------------------------------------------------------------------
def test_evaluate_formula_file_loads(tmp_path):
    p = tmp_path / "formula.json"
    p.write_text(json.dumps({"ingredients": [{"ingredient": "Toluene", "percent": 20,
                                              "unit": "%", "basis": "ready_for_use",
                                              "product_type": "nail_product"}]}))
    r = evaluate_formula_file(str(p), _registry())
    assert r["evaluations"][0]["numeric_comparison"] == "within_supported_numeric_limit"


def test_cli_invalid_formula_exits_2_without_traceback(tmp_path, monkeypatch, capsys):
    from eu_ingredient_linter.__main__ import main
    reg = _registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from", lambda path: reg)
    p = tmp_path / "formula.json"
    p.write_text("not json")
    rc = main(["--registry", "x", "--formula", str(p)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid JSON" in err
    assert "Traceback" not in err


def _cli_rc(tmp_path, monkeypatch, formula_obj):
    """Helper: run the CLI on a formula object and capture its exit code."""
    import json as _json
    from eu_ingredient_linter.__main__ import main
    reg = _registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from", lambda path: reg)
    p = tmp_path / "formula.json"
    p.write_text(_json.dumps(formula_obj))
    return main(["--registry", "x", "--formula", str(p)])


def test_cli_exit_for_unambiguous_prohibition(tmp_path, monkeypatch):
    # Benzene is a pure Annex II ban -> unambiguous prohibition -> exit 1.
    rc = _cli_rc(tmp_path, monkeypatch, {"ingredients": [
        {"ingredient": "Benzene", "percent": 1, "unit": "%"}]})
    assert rc == 1


def test_cli_exit_for_supported_exceedance(tmp_path, monkeypatch, capsys):
    rc = _cli_rc(tmp_path, monkeypatch, {"ingredients": [
        {"ingredient": "Toluene", "percent": 30, "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}]})
    assert rc == 1


def test_cli_exit_for_unresolved_exception_is_zero(tmp_path, monkeypatch, capsys):
    # Hydroquinone retains its Annex II evidence but the cross-annex exception
    # applicability is unresolved -> manual review -> exit 0 (NOT inferred as a
    # prohibition from the headline 'banned' determination alone).
    rc = _cli_rc(tmp_path, monkeypatch, {"ingredients": [
        {"ingredient": "Hydroquinone", "percent": 0.01, "unit": "%",
         "basis": "after_mixing", "product_type": "artificial_nail_system"}]})
    assert rc == 0
    out = capsys.readouterr().out
    assert "requires_manual_review" in out


def test_cli_exit_for_extreme_decimal_is_2(tmp_path, monkeypatch, capsys):
    from eu_ingredient_linter.__main__ import main
    reg = _registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from", lambda path: reg)
    p = tmp_path / "formula.json"
    p.write_text('{"ingredients":[{"ingredient":"Toluene","percent":'
                 '1e99999999999999999999,"unit":"%","basis":"ready_for_use",'
                 '"product_type":"nail_product"}]}')
    rc = main(["--registry", "x", "--formula", str(p)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# Integration against the real processed snapshot.
# ---------------------------------------------------------------------------
def test_supported_numeric_rule_provenance():
    """A supported numeric rule carries its provenance (official reference) and the
    registry snapshot timestamp. Uses the deterministic in-memory registry so it
    runs on a clean checkout (no gitignored production snapshot)."""
    reg = _registry()
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium nitrite",
                                           "percent": 0.1, "unit": "%",
                                           "basis": "ready_for_use",
                                           "product_type": "rust_inhibitor"}]}, reg)["evaluations"][0]
    assert e["numeric_comparison"] == "within_supported_numeric_limit"
    assert e["provenance"]["official_reference"] == "17"
    assert e["provenance"]["data_updated_at"] == reg.data_updated_at


def test_supported_rules_evaluate_with_valid_context():
    """Supported numeric rules in the deterministic in-memory registry evaluate
    with valid context (run on a clean checkout; no production snapshot)."""
    reg = _registry()
    cases = [("Sodium nitrite", "rust_inhibitor", "ready_for_use", "0.2"),
             ("Toluene", "nail_product", "ready_for_use", "25"),
             ("Oxalic acid, its esters and alkaline salts", "hair_product", "ready_for_use", "5")]
    for name, product, basis, cap in cases:
        e = evaluate_formula({"ingredients": [{"ingredient": name, "percent": Decimal('0.1'),
                                               "unit": "%", "basis": basis,
                                               "product_type": product}]}, reg)["evaluations"][0]
        assert e["is_in_dataset"] is True
        assert e["numeric_comparison"] in ("within_supported_numeric_limit",
                                           "exceeds_supported_limit")
        assert e["source_rule"]["status"] == "restricted"
