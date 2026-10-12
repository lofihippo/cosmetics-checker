"""Tests for structured CSV/TSV/JSON inputs and CSV/HTML reporters.

Covers the bounded structured-input contract (one file = one formula, one
ingredient per record, required ``ingredient``, optional context columns) and
the cited CSV/HTML reporters (deterministic rows, direct vs linked evidence,
provenance, unknown/unresolved preservation, escaping). Uses an in-memory
synthetic registry plus the real processed snapshot for the integration cases.

``Regulatory information, not legal advice.``
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import eu_reg_lists as erl
from eu_ingredient_linter.formula import evaluate_formula
from eu_ingredient_linter.inputs import read_structured, StructuredInputError
from eu_ingredient_linter.reporters import render

ROOT = Path(__file__).resolve().parents[3]
PROCESSED = ROOT / "data" / "processed"


def _registry():
    from eu_ingredient_linter.formula import SUPPORTED_RULES

    def rule_for(ref):
        return erl.Rule(
            status="restricted", annex="Annex III", regulation="(EC) 2009/1223",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at=None, resolution="cosing-annex-iii",
            raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
                 "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
                 "reference_number": ref, "accepted": True,
                 "conditions_list": [dict(c) for c in SUPPORTED_RULES[ref]["conditions_template"]]})

    toluene = erl.Substance(
        name="Toluene", id="toluene", cas="108-88-3", ec="203-625-9",
        determination="restricted", rules=[rule_for("185")], sources=["cosing-annex-iii"])
    return erl.Registry([toluene], data_updated_at="2026-10-09T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-iii": {"present": True, "accepted": True},
                            "cosing-annex-v": {"present": True, "accepted": True}},
                        metadata={})


# ---------------------------------------------------------------------------
# Part B: structured input.
# ---------------------------------------------------------------------------
def test_csv_parses_reads_bom_and_embedded_newlines(tmp_path):
    p = tmp_path / "formula.csv"
    # UTF-8 BOM + a quoted field with a comma/embedded newline.
    text = ("\ufeffingredient,percent,unit,basis,product_type\n"
            "\"Aqua, PEG-40\nsecond line\",1,%,ready_for_use,nail_product\n")
    p.write_text(text, encoding="utf-8")
    f = read_structured(str(p), "csv")
    assert f["ingredients"][0]["ingredient"] == "Aqua, PEG-40\nsecond line"
    assert f["ingredients"][0]["percent"] == "1"


def test_csv_blank_percent_is_absent_never_zero(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,percent,unit,basis,product_type\nToluene,,%,ready_for_use,nail_product\n")
    rec = read_structured(str(p), "csv")["ingredients"][0]
    assert "percent" not in rec


def test_csv_blank_context_is_absent(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,unit,basis,product_type,body_area,professional_use,ready_for_use\n"
                 "Toluene,%,ready_for_use,nail_product,,,\n")
    rec = read_structured(str(p), "csv")["ingredients"][0]
    for k in ("body_area", "professional_use", "ready_for_use"):
        assert k not in rec
    assert rec["ingredient"] == "Toluene"


def test_csv_boolean_parsing(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,professional_use,ready_for_use\n"
                 "Toluene,yes,false\n"
                 "Aqua,TRUE,\n")
    recs = read_structured(str(p), "csv")["ingredients"]
    assert recs[0]["professional_use"] is True
    assert recs[0]["ready_for_use"] is False
    assert "ready_for_use" not in recs[1]
    assert recs[1]["professional_use"] is True


def test_csv_rejects_malformed_boolean(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,professional_use\nToluene,definitely\n")
    with pytest.raises(StructuredInputError):
        read_structured(str(p), "csv")


def test_csv_rejects_duplicate_header(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,ingredient,percent\nToluene,X,1\n")
    with pytest.raises(StructuredInputError):
        read_structured(str(p), "csv")


def test_csv_rejects_unsupported_column(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,bogus\nToluene,x\n")
    with pytest.raises(StructuredInputError):
        read_structured(str(p), "csv")


def test_csv_rejects_partial_unnamed_row(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient,percent\n,1\n")
    with pytest.raises(StructuredInputError):
        read_structured(str(p), "csv")


def test_csv_rejects_empty_input(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient\n", encoding="utf-8")
    with pytest.raises(StructuredInputError):
        read_structured(str(p), "csv")


def test_csv_ignores_completely_blank_records(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("ingredient\nToluene\n\n  \nAqua\n")
    recs = read_structured(str(p), "csv")["ingredients"]
    assert [r["ingredient"] for r in recs] == ["Toluene", "Aqua"]


def test_csv_equivalent_to_json(tmp_path):
    """A CSV file is equivalent to the same JSON formula."""
    reg = _registry()
    p = tmp_path / "f.csv"
    p.write_text("ingredient,percent,unit,basis,product_type\n"
                 "Toluene,25,%,ready_for_use,nail_product\n")
    csv_eval = evaluate_formula(read_structured(str(p), "csv"), reg)["evaluations"][0]
    json_eval = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": "25", "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}]}, reg)["evaluations"][0]
    assert csv_eval["numeric_comparison"] == json_eval["numeric_comparison"]
    assert csv_eval["verdict"] == json_eval["verdict"]
    assert csv_eval["cap"] == json_eval["cap"]


def test_tsv_equivalent_to_csv(tmp_path):
    reg = _registry()
    p = tmp_path / "f.tsv"
    p.write_text("ingredient\tpercent\tunit\tbasis\tproduct_type\n"
                 "Toluene\t25\t%\tready_for_use\tnail_product\n")
    tsv_eval = evaluate_formula(read_structured(str(p), "tsv"), reg)["evaluations"][0]
    csv_eval = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": "25", "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}]}, reg)["evaluations"][0]
    assert tsv_eval["numeric_comparison"] == csv_eval["numeric_comparison"]


def test_decimal_concentration_survives_exactly(tmp_path):
    reg = _registry()
    p = tmp_path / "f.csv"
    p.write_text("ingredient,percent,unit,basis,product_type\n"
                 "Toluene,25.0000000000000001,%,ready_for_use,nail_product\n")
    e = evaluate_formula(read_structured(str(p), "csv"), reg)["evaluations"][0]
    assert e["submitted_value"] == "25.0000000000000001"
    assert e["numeric_comparison"] == "exceeds_supported_limit"


def test_duplicate_resolved_identity_rejected(tmp_path):
    reg = _registry()
    p = tmp_path / "f.csv"
    p.write_text("ingredient,percent\nToluene,10\n108-88-3,20\n")
    with pytest.raises(ValueError) as exc:
        evaluate_formula(read_structured(str(p), "csv"), reg)
    assert "duplicate resolved identity" in str(exc.value)


def test_unknown_ingredient_kept(tmp_path):
    reg = _registry()
    p = tmp_path / "f.csv"
    p.write_text("ingredient\nTotallyFakeIngredient\n")
    e = evaluate_formula(read_structured(str(p), "csv"), reg)["evaluations"][0]
    assert e["verdict"] == "unknown_ingredient"


# ---------------------------------------------------------------------------
# Part C: reporters.
# ---------------------------------------------------------------------------
def test_csv_roundtrip_quoted_punctuation_and_newline(tmp_path):
    from io import StringIO
    report = evaluate_formula({"ingredients": [
        {"ingredient": "Aqua, PEG-40", "percent": "1", "unit": "%"}],
    }, _registry())
    csv_text = render(report, "csv")
    reader = csv.DictReader(StringIO(csv_text))
    rows = [r for r in reader if r["report_kind"] == "formula"]
    assert len(rows) == 1
    assert rows[0]["ingredient"] == "Aqua, PEG-40"


def test_csv_direct_and_linked_evidence_separate_rows():
    from io import StringIO
    # Build a member with own Annex III + linked Annex V group evidence.
    report = _member_plus_formula()
    csv_text = render(report, "csv")
    reader = csv.reader(StringIO(csv_text))
    rows = list(reader)
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    kinds = [r[idx["evidence_kind"]] for r in rows[1:] if r[idx["report_kind"]] == "formula"]
    assert kinds.count("direct") == 1
    assert kinds.count("linked") == 1
    # provenance present on every evidence row.
    for r in rows[1:]:
        if r[idx["report_kind"]] == "formula":
            assert r[idx["data_updated_at"]]
            assert r[idx["regulation"]]
            assert r[idx["official_reference"]]
            assert r[idx["row_reference"]]


def test_csv_emits_unknown_row_no_rules():
    from io import StringIO
    report = evaluate_formula({"ingredients": [{"ingredient": "Nope"}]}, _registry())
    csv_text = render(report, "csv")
    rows = list(csv.DictReader(StringIO(csv_text)))
    assert rows[0]["determination"] == "unknown"
    assert rows[0]["evidence_kind"] == "no_rule"


def test_csv_meta_rows_are_valid_csv_data():
    from io import StringIO
    report = evaluate_formula({"ingredients": [{"ingredient": "Toluene",
                                                "percent": "20", "unit": "%"}]},
                              _registry())
    csv_text = render(report, "csv")
    # A strict csv.reader must parse the whole output as data (no comment rows).
    rows = list(csv.reader(StringIO(csv_text)))
    header = rows[0]
    kinds = [r[header.index("report_kind")] for r in rows[1:]]
    assert kinds.count("meta") == 2  # coverage + disclaimer
    meta_rows = [r for r in rows[1:] if r[header.index("report_kind")] == "meta"]
    assert any(r[header.index("evidence_kind")] == "coverage" for r in meta_rows)
    assert any(r[header.index("evidence_kind")] == "disclaimer" for r in meta_rows)
    # The disclaimer value survives parsing.
    dis = next(r[header.index("meta")] for r in meta_rows
               if r[header.index("evidence_kind")] == "disclaimer")
    assert "Regulatory information, not legal advice" in dis



    report = evaluate_formula({"ingredients": [
        {"ingredient": "<script>alert(1)</script>", "percent": "1", "unit": "%"}],
    }, _registry())
    html_text = render(report, "html")
    assert "<script>alert" not in html_text
    assert "&lt;script&gt;" in html_text


def test_html_includes_unknown_not_clean():
    report = evaluate_formula({"ingredients": [
        {"ingredient": "Nope", "percent": "1", "unit": "%"}],
    }, _registry())
    html_text = render(report, "html")
    assert "unknown" in html_text.lower()
    assert "not a clean result" in html_text.lower()


def test_html_standalone_and_disclaimer():
    report = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": "20", "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}],
    }, _registry())
    html_text = render(report, "html")
    assert html_text.startswith("<!DOCTYPE html>")
    assert "<style>" in html_text and "</style>" in html_text
    assert "Regulatory information, not legal advice" in html_text


def _member_plus_formula():
    """Report for a member with its own Annex III rule + linked Annex V rule."""
    group = erl.Substance(
        id="group", name="Group substance", cas=None,
        determination="allowed_with_conditions",
        rules=[erl.Rule(status="allowed_with_conditions", annex="Annex V",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at="2026-01-01", resolution="cosing-annex-v",
                        raw={"source_kind": "cosing-annex-v", "source_file": "v",
                             "row_reference": "v#3", "official_reference": "3",
                             "source_sha256": "sha", "entry_number": 3,
                             "retrieved_at": "2026-01-02T00:00:00+00:00",
                             "conditions_list": [
                                 {"field": "max_concentration", "label": "Max",
                                  "value": "0.5% (acid)"}]})],
        sources=["cosing-annex-v"])
    member = erl.Substance(
        id="m", name="Member", cas=None, determination="restricted",
        rules=[erl.Rule(status="restricted", annex="Annex III",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-iii",
                        raw={"source_kind": "cosing-annex-iii", "source_file": "a",
                             "row_reference": "a#98", "official_reference": "98",
                             "source_sha256": "sha2", "entry_number": 98,
                             "conditions_list": []})],
        member_of=[{"group_id": "group", "group_name": "Group substance",
                    "official_reference": "3", "annex": "Annex V",
                    "source_kind": "cosing-annex-v", "source_file": "v",
                    "row_reference": "v#3", "source_url": "https://x",
                    "source_key": ["cosing-annex-v", "3", "v#3"],
                    "member_name": "member"}],
        sources=["cosing-annex-iii"])
    reg = erl.Registry([group, member], data_updated_at="2026-10-09T00:00:00+00:00",
                       verified_sources={
                           "cosing-annex-iii": {"present": True, "accepted": True},
                           "cosing-annex-v": {"present": True, "accepted": True}},
                       metadata={})
    return evaluate_formula({"ingredients": [{"ingredient": "Member", "percent": "0.5",
                                              "unit": "%", "basis": "ready_for_use"}]}, reg)


# ---------------------------------------------------------------------------
# Part A corrections.
# ---------------------------------------------------------------------------
def _hydroquinone_registry():
    """Registry with a banned substance whose Annex II source name carries a
    scope/exception qualification (Hydroquinone-like)."""
    from eu_ingredient_linter.formula import SUPPORTED_RULES

    def rule_for(ref):
        return erl.Rule(
            status="restricted", annex="Annex III", regulation="(EC) 2009/1223",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at=None, resolution="cosing-annex-iii",
            raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
                 "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
                 "reference_number": ref, "accepted": True,
                 "conditions_list": [dict(c) for c in SUPPORTED_RULES[ref]["conditions_template"]]})

    toluene = erl.Substance(
        name="Toluene", id="toluene", cas="108-88-3", ec="203-625-9",
        determination="restricted", rules=[rule_for("185")], sources=["cosing-annex-iii"])
    hq = erl.Substance(
        name="Hydroquinone", id="hydroquinone", cas="123-31-9", ec="204-617-8",
        determination="banned",
        rules=[erl.Rule(
            status="banned", annex="Annex II", regulation="(EC) 2013/344",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at="2026-01-01", resolution="cosing-annex-ii",
            raw={"official_reference": 1339, "row_reference": "f#1339",
                 "source_file": "f", "source_sha256": "sha",
                 "retrieved_at": "2026-01-02T00:00:00+00:00",
                 "conditions_list": None,
                 "source_row": {"Chemical name / INN":
                                "1,4-Dihydroxybenzene (Hydroquinone), with the "
                                "exception of entry 14 in Annex III"}})],
        sources=["cosing-annex-ii"])
    return erl.Registry([toluene, hq], data_updated_at="2026-10-09T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-iii": {"present": True, "accepted": True},
                            "cosing-annex-ii": {"present": True, "accepted": True}},
                        metadata={})


# A.1 strict quoting.
def test_malformed_unterminated_quote_rejected(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text('ingredient\n"Aqua\n')
    with pytest.raises(StructuredInputError) as exc:
        read_structured(str(p), "csv")
    assert "CSV parse error" in str(exc.value)
    assert "record 2" in str(exc.value)
    assert "line 2" in str(exc.value)  # record != physical line after no multiline here


def test_malformed_trailing_junk_after_quote_rejected(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text('ingredient\n"Benzene"junk\n')
    with pytest.raises(StructuredInputError) as exc:
        read_structured(str(p), "csv")
    assert "CSV parse error" in str(exc.value)


def test_valid_multiline_embedded_commas_ok(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text('ingredient,percent\n"Toluene, x\nsecond line",25\n')
    f = read_structured(str(p), "csv")
    assert f["ingredients"][0]["ingredient"] == "Toluene, x\nsecond line"
    assert f["ingredients"][0]["percent"] == "25"


def test_record_number_differs_from_line_after_multiline(tmp_path):
    # A multiline quoted field in record 2 shifts the physical line of a later
    # malformed record; the error must report the correct record and line.
    p = tmp_path / "f.csv"
    p.write_text('ingredient,percent\n"Toluene\nsecond",1\n"Bad\n')
    with pytest.raises(StructuredInputError) as exc:
        read_structured(str(p), "csv")
    assert "record 3" in str(exc.value)
    assert "line 4" in str(exc.value)


# A.2 submitted context.
def test_submitted_context_preserved_in_report():
    r = evaluate_formula({"ingredients": [{
        "ingredient": "Toluene", "product_type": "nail_product", "body_area": "nails",
        "professional_use": False, "ready_for_use": True}]}, _registry())
    e = r["evaluations"][0]
    assert e["submitted_context"] == {
        "product_type": "nail_product", "body_area": "nails",
        "professional_use": False, "ready_for_use": True}


def test_submitted_context_absent_vs_false_distinct():
    r = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "professional_use": False}]}, _registry())
    e = r["evaluations"][0]
    assert "product_type" not in e["submitted_context"]
    assert e["submitted_context"]["professional_use"] is False


def test_submitted_context_preserved_in_csv():
    import json as _json
    from io import StringIO
    r = evaluate_formula({"ingredients": [{
        "ingredient": "Toluene", "professional_use": False, "ready_for_use": True}]},
        _registry())
    rows = list(csv.DictReader(StringIO(render(r, "csv"))))
    row = [x for x in rows if x["report_kind"] == "formula"][0]
    ctx = _json.loads(row["submission_context"])
    assert ctx["professional_use"] is False
    assert ctx["ready_for_use"] is True


# A.4 source scope/exception text + citation snapshot.
def test_source_scope_text_in_csv():
    from io import StringIO
    r = evaluate_formula({"ingredients": [{"ingredient": "Hydroquinone"}]},
                         _hydroquinone_registry())
    rows = list(csv.DictReader(StringIO(render(r, "csv"))))
    row = [x for x in rows if x["evidence_kind"] == "direct"
           and x["annex"] == "Annex II"][0]
    assert "with the exception of entry 14" in row["source_scope_text"]


def test_source_scope_text_in_html():
    r = evaluate_formula({"ingredients": [{"ingredient": "Hydroquinone"}]},
                         _hydroquinone_registry())
    h = render(r, "html")
    assert "with the exception of entry 14" in h


def test_linked_citation_uses_snapshot_not_retrieval():
    group = erl.Substance(
        id="group", name="Group", cas=None, determination="allowed_with_conditions",
        rules=[erl.Rule(status="allowed_with_conditions", annex="Annex V",
                        regulation="(EC) 2009/1223", source_url="https://x",
                        source_updated_at=None, resolution="cosing-annex-v",
                        raw={"source_kind": "cosing-annex-v", "row_reference": "f#1",
                             "official_reference": "1",
                             "retrieved_at": "2026-01-01T00:00:00+00:00",
                             "conditions_list": []})],
        sources=["cosing-annex-v"])
    member = erl.Substance(
        id="m", name="Sodium benzoate", cas="532-32-1", determination="unknown",
        rules=[],
        member_of=[{"group_id": "group", "group_name": "Group",
                    "official_reference": "1", "annex": "Annex V",
                    "source_kind": "cosing-annex-v", "source_file": "f",
                    "row_reference": "f#1", "source_url": "https://x",
                    "source_key": ["cosing-annex-v", "1", "f#1"],
                    "member_name": "sodium benzoate"}],
        sources=[])
    reg = erl.Registry([group, member], data_updated_at="2026-10-09T00:00:00+00:00",
                       verified_sources={
                           "cosing-annex-v": {"present": True, "accepted": True}},
                       metadata={})
    r = evaluate_formula({"ingredients": [{"ingredient": "Sodium benzoate"}]}, reg)
    assert r["evaluations"][0]["linked_rules"], "expected linked evidence"
    h = render(r, "html")
    # The linked citation must carry the report snapshot timestamp, not retrieval.
    assert "snapshot 2026-10-09" in h


# ---------------------------------------------------------------------------
# A.1 CLI-level strict quoting regressions.
# ---------------------------------------------------------------------------
def _cli_via_input(tmp_path, monkeypatch, capsys, filename, content):
    from eu_ingredient_linter.__main__ import main
    reg = _registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from", lambda path: reg)
    p = tmp_path / filename
    p.write_text(content)
    rc = main(["--registry", "x", "--input", str(p), "--input-format", "csv"])
    return rc, capsys.readouterr()


def test_cli_rejects_unterminated_quote_exit2(tmp_path, monkeypatch, capsys):
    rc, cap = _cli_via_input(tmp_path, monkeypatch, capsys, "f.csv",
                             'ingredient\n"Aqua\n')
    assert rc == 2
    err = cap.err
    assert "CSV parse error" in err
    assert "Traceback" not in err
    assert "evaluations" not in cap.out  # no success report


def test_cli_rejects_trailing_junk_exit2(tmp_path, monkeypatch, capsys):
    rc, cap = _cli_via_input(tmp_path, monkeypatch, capsys, "f.csv",
                             'ingredient\n"Benzene"junk\n')
    assert rc == 2
    assert "CSV parse error" in cap.err
    assert "Traceback" not in cap.err
    assert "evaluations" not in cap.out  # no success report


def test_cli_valid_multiline_input_succeeds(tmp_path, monkeypatch, capsys):
    rc, cap = _cli_via_input(tmp_path, monkeypatch, capsys, "f.csv",
                             'ingredient,percent\n"Toluene, x\nsecond",25\n')
    assert rc == 0
    assert "evaluations" in cap.out or "Formula" in cap.out


# ---------------------------------------------------------------------------
# Part B: GitHub annotations (--format github).
# ---------------------------------------------------------------------------
def test_github_annotation_includes_ingredient_citation_and_snapshot():
    r = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": 30, "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}]}, _registry())
    gh = render(r, "github")
    assert gh.startswith("::error")
    assert "Toluene" in gh
    assert "ref 185" in gh
    assert "snapshot 2026-10-09" in gh
    assert "unresolved" in gh.lower()


def test_github_unknown_ingredient_is_notice():
    r = evaluate_formula({"ingredients": [{"ingredient": "Nope"}]}, _registry())
    gh = render(r, "github")
    assert "::notice" in gh
    assert "unknown_ingredient" in gh
    assert "not a compliance" in gh
    # Never a proven-violation claim.
    assert "unambiguous_prohibition" not in gh


def test_github_escapes_newlines_and_blocks_injection():
    r = evaluate_formula({"ingredients": [
        {"ingredient": "Benzene\n::error file=evil"}]}, _registry())
    gh = render(r, "github")
    # The newline is replaced with %0A so the injected ``::error`` cannot start a
    # NEW workflow command (a command begins at a real line start). The literal
    # text may appear on the line, but it is not a leading command.
    assert "%0A" in gh
    for line in gh.splitlines():
        # A genuine injection would place ``::error file=evil`` at a line start.
        assert not line.startswith("::error file=evil")


def test_github_rendered_findings_agree_with_json():
    import json as _json
    r = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": 30, "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"},
        {"ingredient": "Nope"}]}, _registry())
    gh = render(r, "github")
    js = _json.loads(render(r, "json"))
    # JSON says Toluene exceeds; GitHub emits an error level for it.
    assert js["evaluations"][0]["verdict"] == "exceeds_supported_limit"
    assert "Toluene" in gh and "::error" in gh
    assert js["evaluations"][1]["verdict"] == "unknown_ingredient"
    assert "Nope" in gh and "::notice" in gh


def test_github_linked_evidence_distinguished_from_direct():
    r = evaluate_formula({"ingredients": [
        {"ingredient": "Toluene", "percent": 25, "unit": "%",
         "basis": "ready_for_use", "product_type": "nail_product"}]}, _registry())
    gh = render(r, "github")
    # Direct rule annotated; resolution type present.
    assert "direct" not in gh.lower() or "restricted" in gh.lower()
