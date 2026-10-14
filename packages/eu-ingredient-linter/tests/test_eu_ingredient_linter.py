"""Tests for eu-ingredient-linter (Phase 2, Annex II only).

These use isolated in-memory registries (never the real data/processed) and
verify the honest-unknown semantics, Annex-II-only coverage reporting, and that
official Reference Numbers are preserved separately from file row positions.
"""

import sys
from pathlib import Path

import eu_reg_lists as erl
from eu_ingredient_linter import (batch_report, build_coverage, check_list,
                                  ingredient_report)
from eu_ingredient_linter import __init__ as linter_mod  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]


def _annex2_registry(verified_sources=None):
    """A small in-memory registry with an Annex II banned rule."""
    raw = {"source_kind": "cosing-annex-ii",
           "source_file": "COSING_Annex_II_v2.txt",
           "source_sha256": "abc",
           "row_reference": "COSING_Annex_II_v2.txt#49",
           "entry_number": 49,
           "reference_number": 47,
           "official_reference": 47,
           "accepted": True,
           "retrieved_at": "2026-10-08T00:07:00+00:00"}
    benzene = erl.Substance(
        id="benzene", name="Benzene", aliases=["benzol"], cas="71-43-2",
        ec="200-753-7", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw=raw)],
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
                             "accepted": True})],
        sources=["cosing-annex-ii"],
    )
    if verified_sources is None:
        verified_sources = {
            "cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                                "accepted": True, "fetched": True,
                                "retrieval_error": None},
            "echa-svhc": {"kind": "echa-svhc", "present": False,
                          "accepted": False, "fetched": False,
                          "retrieval_error": "HTTPError"},
        }
    return erl.Registry([benzene, water, dioxane],
                        data_updated_at="2026-10-08T00:00:00+00:00",
                        verified_sources=verified_sources)


def test_covered_banned_ingredient_is_flagged():
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg)
    assert rep["verdict"] == "FLAGGED"
    assert rep["n_banned"] == 1
    assert rep["clean"] is False
    benzene = rep["results"][0]
    assert benzene["determination"] == "banned"
    assert benzene["status"] == "banned"
    assert benzene["annex"] == "Annex II"


def test_reference_number_preserved_separately_from_row():
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg)
    rule = rep["results"][0]["rules"][0]
    # official Reference Number (Annex II #47) is distinct from the file row.
    assert rule["raw"]["official_reference"] == 47
    assert rule["raw"]["reference_number"] == 47
    assert rule["raw"]["row_reference"] == "COSING_Annex_II_v2.txt#49"
    assert rule["raw"]["row_reference"] != f"#{rule['raw']['official_reference']}"
    # source citation preserved
    assert rule["regulation"] == "(EC) 2009/1223"
    assert "ec.europa.eu" in rule["source_url"]
    assert rule["resolution"] == "cosing-annex-ii"


def test_unmatched_ingredient_remains_unknown():
    reg = _annex2_registry()
    rep = ingredient_report("TotallyFakeIngredient123", reg)
    res = rep["results"][0]
    assert res["is_in_dataset"] is False
    assert res["determination"] == "unknown"
    assert res["rules"] == []
    assert rep["verdict"] == "NOT-FULLY-VERIFIED"
    assert rep["clean"] is False


def test_known_but_unverified_identity_not_clean():
    reg = _annex2_registry()
    rep = ingredient_report("Aqua", reg)
    res = rep["results"][0]
    # Aqua is a recognised identity but carries no verified Annex II rule.
    assert res["is_in_dataset"] is True
    assert res["determination"] == "unknown"
    assert res["rules"] == []
    assert rep["clean"] is False
    assert rep["verdict"] == "NOT-FULLY-VERIFIED"


def test_coverage_reports_annex2_and_missing_lists():
    reg = _annex2_registry()
    cov = build_coverage(reg.verified_sources)
    assert cov.has_annex_ii is True
    assert "cosing-annex-ii" in cov.available
    # Missing (coverage gap, not a clean result): Annex III–VI.
    for k in ("cosing-annex-iii", "cosing-annex-iv", "cosing-annex-v",
              "cosing-annex-vi"):
        assert k in cov.missing
    # ECHA is deliberately NOT included in this release (deferred).
    assert "echa-svhc" in cov.not_included
    assert "echa-svhc" not in cov.missing


def test_empty_label_is_no_ingredients_not_clean():
    reg = _annex2_registry()
    rep = ingredient_report("   ", reg)
    assert rep["verdict"] == "NO-INGREDIENTS"
    assert rep["no_ingredients"] is True
    assert rep["clean"] is False


def test_batch_report_aggregates_and_reports_coverage():
    reg = _annex2_registry()
    rep = batch_report(["Benzene", "TotallyFake", "Aqua"], reg)
    assert rep["n_items"] == 3
    assert rep["total_ingredients"] == 3
    assert rep["total_flagged"] == 1
    assert rep["total_banned"] == 1
    assert rep["total_unverified"] == 2
    assert rep["coverage"]["has_annex_ii"] is True
    assert rep["disclaimer"]


def test_check_list_returns_honest_results():
    reg = _annex2_registry()
    results = check_list(["Benzene", "nope"], reg)
    assert results[0].determination == "banned"
    assert results[1].is_in_dataset is False
    assert results[1].determination == "unknown"


def test_every_report_includes_disclaimer():
    reg = _annex2_registry()
    assert "not legal advice" in ingredient_report("Benzene", reg)["disclaimer"]


def test_punctuation_tokenizer_keeps_known_names_whole():
    """1,4-dioxane and a trailing comma must NOT split a banned ingredient away."""
    reg = _annex2_registry()
    rep = ingredient_report("1,4-dioxane", reg)
    assert rep["tokens"] == ["1,4-dioxane"]
    assert rep["n_flagged"] == 1
    assert rep["n_banned"] == 1
    rep2 = ingredient_report("Benzene,", reg)
    assert rep2["tokens"] == ["benzene"]
    assert rep2["n_banned"] == 1
    rep3 = ingredient_report("1,4-dioxane, Benzene", reg)
    assert rep3["n_banned"] == 2


def test_cli_exit_code_for_flag(monkeypatch):
    from eu_ingredient_linter.__main__ import main
    reg = _annex2_registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from",
                        lambda path: reg)
    rc = main(["--registry", "unused", "--label", "Benzene"])
    assert rc == 1  # FLAGGED -> exit 1


def test_cli_exit_code_for_invalid_and_empty_input(monkeypatch, capsys):
    from eu_ingredient_linter.__main__ import main
    reg = _annex2_registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from",
                        lambda path: reg)
    # empty/whitespace input -> NO-INGREDIENTS -> exit 2
    assert main(["--registry", "x", "--label", ";;;"]) == 2
    # no input at all -> exit 2
    assert main(["--registry", "x"]) == 2
    # a clean registerable-yet-unverified label -> NOT a flag -> exit 0
    assert main(["--registry", "x", "--label", "Aqua"]) == 0


def test_cli_batch_exit_code_for_flag(monkeypatch, tmp_path):
    from eu_ingredient_linter.__main__ import main
    reg = _annex2_registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from",
                        lambda path: reg)
    b = tmp_path / "labels.txt"
    b.write_text("Benzene\nAqua\n")
    assert main(["--registry", "x", "--batch", str(b)]) == 1  # batch has a flag


def test_cli_exit_code_for_entry70_applicable_exceeds(monkeypatch, tmp_path):
    """A supported REACH Annex XVII entry 70 trigger yields a finding exit (1),
    while a plain parent verdict of ``requires_manual_review`` no longer masks it."""
    import json
    from eu_ingredient_linter.__main__ import main
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE, ENTRY70_SOURCE_KIND
    d5 = erl.Substance(
        id="cyclopentasiloxane", name="Cyclopentasiloxane", cas="541-02-6",
        ec="208-764-9", determination="unknown", rules=[], aliases=["D5"],
        sources=[])
    verified = {
        "cosing-annex-ii": {"present": True, "accepted": True},
        "echa-svhc": {"present": False, "accepted": False},
        ENTRY70_SOURCE_KIND: {
            "present": True, "accepted": True, "kind": "reach-legal-act",
            "files": {"OJ.pdf": {"sha256": ENTRY70_SOURCE["document_sha256"],
                                 "byte_len": 1}},
            "data_updated_at": "2026-01-01", "retrieved_at": "2026-01-01",
            "mapping": {
                "mapping_version": ENTRY70_SOURCE["mapping_version"],
                "document_sha256": ENTRY70_SOURCE["document_sha256"],
                "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
                "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
            },
        },
    }
    reg = erl.Registry([d5], data_updated_at="2026-01-01",
                       verified_sources=verified)
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from",
                        lambda path: reg)
    f = tmp_path / "formula.json"
    f.write_text(json.dumps({
        "ingredients": [{
            "ingredient": "Cyclopentasiloxane", "percent": "0.5", "unit": "%",
            "basis": "ready_for_use", "product_type": "wash-off",
            "assessment_date": "2028-01-01",
        }],
    }))
    rc = main(["--registry", "x", "--formula", str(f), "--format", "json"])
    assert rc == 1


def test_human_report_renders_citation_for_flag(capsys):
    """Human text output must include regulation, source URL and data_updated_at."""
    from eu_ingredient_linter.__main__ import _print_human
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg)
    _print_human(rep)
    out = capsys.readouterr().out
    assert "(EC) 2009/1223" in out
    assert "ec.europa.eu" in out
    assert "data_updated_at=2026-10-08T00:00:00+00:00" in out
    assert "data_updated_at:" in out  # top-level report line
    assert "Ref." in out or "ref #" in out or "official_reference" in out
    # The retrieval time must NOT be presented as the snapshot update time.
    assert "updated 2026-10-08T00:07:00" not in out


def _activate_entry70_registry(reg):
    """Attach an accepted, compatible entry 70 source/mapping record."""
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE, ENTRY70_SOURCE_KIND
    if not getattr(reg, "verified_sources", None):
        reg.verified_sources = {}
    reg.verified_sources[ENTRY70_SOURCE_KIND] = {
        "present": True, "accepted": True, "kind": "reach-legal-act",
        "files": {"OJ.pdf": {"sha256": ENTRY70_SOURCE["document_sha256"],
                             "byte_len": 1}},
        # Deliberately NO per-source data_updated_at (regression for Part B:
        # the promoted source record lacks one, so the effective LOADED snapshot
        # timestamp must flow through instead of yielding null nested stamps).
        "retrieved_at": "2026-01-01",
        "mapping": {
            "mapping_version": ENTRY70_SOURCE["mapping_version"],
            "document_sha256": ENTRY70_SOURCE["document_sha256"],
            "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
            "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
        },
    }
    return reg


def test_label_entry70_missing_context_is_restriction_identified_not_clean():
    """A label-only D5/D6 identity carrying entry 70 evidence is a restriction
    finding (context required), not an established violation and never an
    unverified 'no verified rule' identity."""
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE, ENTRY70_SOURCE_KIND
    d5 = erl.Substance(
        id="cyclopentasiloxane", name="Cyclopentasiloxane", cas="541-02-6",
        ec="208-764-9", determination="unknown", rules=[], aliases=["D5"],
        sources=[])
    d6 = erl.Substance(
        id="cyclohexasiloxane", name="Cyclohexasiloxane", cas="540-97-6",
        ec="208-762-8", determination="unknown", rules=[], aliases=["D6"],
        sources=[])
    reg = erl.Registry([d5, d6], data_updated_at="2026-10-14T07:32:13+00:00",
                       verified_sources={
                           "cosing-annex-ii": {"present": True, "accepted": True},
                           "echa-svhc": {"present": False, "accepted": False},
                       })
    _activate_entry70_registry(reg)
    rep = ingredient_report("Cyclopentasiloxane, Cyclohexasiloxane", reg)
    # Restriction identified (entry 70 evidence) -> flagged, but NOT a ban.
    assert rep["verdict"] == "FLAGGED"
    assert rep["n_flagged"] == 2
    assert rep["n_banned"] == 0
    assert rep["clean"] is False
    for res in rep["results"]:
        assert res["entry70"] is not None
        assert res["entry70"]["state"] == "missing_context"
        # The effective snapshot timestamp flows through even though the source
        # record has no per-source data_updated_at (Part B regression).
        assert res["entry70"]["data_updated_at"] == "2026-10-14T07:32:13+00:00"
        assert res["entry70"]["provenance"]["data_updated_at"] == \
            "2026-10-14T07:32:13+00:00"


def test_label_entry70_absent_identity_stays_unknown():
    """A recognised-but-unverified identity with NO entry 70 evidence stays an
    unknown (never a clean result), and is NOT flown to FLAGGED."""
    reg = erl.Registry([erl.Substance(
        id="water", name="Aqua", aliases=["water"], cas="7732-18-5",
        ec="231-791-2", determination="unknown", rules=[])],
        data_updated_at="2026-01-01",
        verified_sources={"cosing-annex-ii": {"present": True, "accepted": True}})
    rep = ingredient_report("Aqua", reg)
    assert rep["verdict"] == "NOT-FULLY-VERIFIED"
    assert rep["n_flagged"] == 0
    assert rep["results"][0]["entry70"] is None


# --- Part 1A: batch input validation & exit codes -------------------------


def test_batch_report_flags_invalid_tokenless_items():
    reg = _annex2_registry()
    rep = batch_report(["Benzene", ";;;", "Aqua"], reg)
    assert rep["is_valid"] is False
    assert rep["has_invalid_input"] is True
    assert rep["invalid_item_indices"] == [1]
    assert rep["n_invalid_items"] == 1
    # Valid items still produce per-item results.
    assert rep["items"][0]["verdict"] == "FLAGGED"
    assert rep["items"][2]["verdict"] == "NOT-FULLY-VERIFIED"
    assert rep["total_flagged"] == 1


def test_batch_report_empty_items_invalid():
    reg = _annex2_registry()
    rep = batch_report([], reg)
    assert rep["is_valid"] is False
    assert rep["has_invalid_input"] is True
    assert rep["n_invalid_items"] == 1
    assert rep["total_ingredients"] == 0


def test_cli_batch_exit_codes(monkeypatch, tmp_path):
    from eu_ingredient_linter.__main__ import main
    reg = _annex2_registry()
    monkeypatch.setattr("eu_ingredient_linter.__main__._registry_from",
                        lambda path: reg)

    def run(lines):
        b = tmp_path / "labels.txt"
        b.write_text("\n".join(lines))
        return main(["--registry", "x", "--batch", str(b)])

    # Empty batch (blank lines only) -> invalid -> 2
    assert run(["\n", "  ", "\n"]) == 2
    # Only tokenless labels -> invalid -> 2
    assert run([";;;"]) == 2
    # Mixed valid + invalid -> invalid precedence -> 2
    assert run(["Aqua", ";;;"]) == 2
    # Flagged + invalid -> invalid takes precedence -> 2
    assert run(["Benzene", ";;;"]) == 2
    # Valid batch with a flag -> 1
    assert run(["Benzene", "Aqua"]) == 1
    # Valid batch, no flag (unverified only) -> 0
    assert run(["Aqua", "TotallyFake"]) == 0


# --- Part 1B: data_updated_at semantics ----------------------------------


def test_report_timestamp_defaults_to_registry_and_is_consistent():
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg)
    assert rep["data_updated_at"] == "2026-10-08T00:00:00+00:00"
    res = rep["results"][0]
    assert res["data_updated_at"] == rep["data_updated_at"]
    # Every ingredient result carries the same snapshot timestamp.
    rep2 = ingredient_report("Benzene, Aqua", reg)
    for r in rep2["results"]:
        assert r["data_updated_at"] == "2026-10-08T00:00:00+00:00"


def test_report_honors_explicit_timestamp_override():
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg, data_updated_at="2099-01-01T00:00:00+00:00")
    assert rep["data_updated_at"] == "2099-01-01T00:00:00+00:00"
    assert rep["results"][0]["data_updated_at"] == "2099-01-01T00:00:00+00:00"


def test_batch_timestamp_consistent_across_items():
    reg = _annex2_registry()
    rep = batch_report(["Benzene", "Aqua"], reg)
    assert rep["data_updated_at"] == "2026-10-08T00:00:00+00:00"
    for item in rep["items"]:
        assert item["data_updated_at"] == rep["data_updated_at"]


def test_source_updated_at_and_retrieval_kept_separate():
    """source_updated_at (upstream) and retrieved_at are never conflated with
    data_updated_at; if either is None it must stay None, not be filled."""
    reg = _annex2_registry()
    rep = ingredient_report("Benzene", reg)
    rule = rep["results"][0]["rules"][0]
    # data_updated_at is the snapshot time (present).
    assert rep["data_updated_at"] is not None
    # source_updated_at stays None (no upstream date known).
    assert rule["source_updated_at"] is None
    # retrieved_at lives in raw provenance, not as a stand-in for the update.
    assert rule["raw"]["retrieved_at"] == "2026-10-08T00:07:00+00:00"
    assert rule["source_updated_at"] != rule["raw"]["retrieved_at"]
