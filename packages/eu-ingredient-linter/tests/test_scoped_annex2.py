"""Regressions for conservative scoped-Annex-II classification (Part C).

A scoped Annex II prohibition (e.g. ``... when used as a substance in hair dye
products``) must NOT be classified as an ``unambiguous`` prohibition nor trigger
the formula's definitive-prohibition exit, while an unconditional ban like
Benzene retains ``unambiguous`` and exit 1. The unevaluated conditions of a
retained positive-list / multi-list rule are surfaced in ``unresolved_requirements``.

"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest  # noqa: F401  (kept for potential fixtures)

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
for p in (PACKAGES / "eu-reg-lists", PACKAGES / "eu-ingredient-linter"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import eu_reg_lists as erl  # noqa: E402
from eu_ingredient_linter.formula import evaluate_formula  # noqa: E402 (used in tests)


def _scope_rule(src_name: str, annex: str, ref: str, status: str = "banned",
                conditions=None):
    """Build a Rule whose raw source_row carries the given source name."""
    return erl.Rule(
        status=status, annex=annex, regulation="(EC) 2009/1223",
        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
        source_updated_at=None, resolution="cosing-annex-ii",
        conditions=conditions,
        raw={
            "source_kind": "cosing-annex-ii", "source_file": "x.csv",
            "source_sha256": "abc", "row_reference": "x.csv#1",
            "entry_number": 1, "reference_number": ref,
            "official_reference": ref, "accepted": True,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "conditions_list": conditions or [],
            "source_row": {"Chemical name / INN": src_name},
        },
    )


def _scoped_registry():
    """Registry with an unconditional ban (Benzene) and a scoped Annex II ban."""
    benzene = erl.Substance(
        id="benzene", name="Benzene", cas="71-43-2", ec="200-753-7",
        determination="banned",
        rules=[_scope_rule("Benzene", "Annex II", "1")],
        sources=["cosing-annex-ii"])
    blue26 = erl.Substance(
        id="basic-blue-26", name="Basic Blue 26 (CI 44045)",
        cas="2580-56-5", ci="CI 44045", determination="banned",
        rules=[
            _scope_rule(
                "[4-[[4-Anilino-1-naphthyl][4-(dimethylamino)phenyl]methylene]"
                "cyclohexa-2,5-dien-1-ylidene]dimethylammonium chloride "
                "(Basic Blue 26; CI 44045) when used as a substance in hair dye "
                "products", "Annex II", "1340"),
            _scope_rule(None, "Annex IV", "69", status="allowed_with_conditions",
                        conditions=[{"field": "max_concentration",
                                     "label": "Maximum concentration in ready "
                                              "for use preparation",
                                     "value": "0.5%"},
                                    {"field": "other", "label": "Other",
                                     "value": "Rinse-off products"}]),
        ],
        sources=["cosing-annex-ii", "cosing-annex-iv"])
    return erl.Registry(
        [benzene, blue26], data_updated_at="2026-10-09T00:00:00+00:00",
        verified_sources={
            "cosing-annex-ii": {"present": True, "accepted": True},
            "cosing-annex-iv": {"present": True, "accepted": True},
        }, metadata={})


def test_scoped_annex2_prohibition_is_not_unambiguous():
    """CI 44045's Annex II 'when used in hair dye products' is scoped; it must
    NOT be classified an unambiguous prohibition."""
    e = evaluate_formula({"ingredients": [{"ingredient": "CI 44045",
                                           "product_type": "oral_product",
                                           "percent": 0.01, "unit": "%",
                                           "basis": "ready_for_use"}]},
                         _scoped_registry())["evaluations"][0]
    assert e["prohibition_finding"] == "scope_unresolved", e["prohibition_finding"]
    # The Annex IV conditional rule (and its conditions) are retained, never a
    # clean claim, and the scoped Annex II rule stays as evidence.
    assert any(r["annex"] == "Annex II" for r in e["rules"])
    assert any(r["annex"] == "Annex IV" for r in e["rules"])
    # The unevaluated positive-list conditions are surfaced.
    assert any("Rinse-off products" in s for s in e["unresolved_requirements"])


def test_benzene_stays_unambiguous():
    """An unconditional ban retains an unambiguous prohibition finding."""
    e = evaluate_formula({"ingredients": [{"ingredient": "Benzene", "percent": 1,
                                           "unit": "%"}]},
                         _scoped_registry())["evaluations"][0]
    assert e["prohibition_finding"] == "unambiguous"


def test_cli_exit_scoped_vs_unambiguous(tmp_path):
    """Exit 1 only on a definitive (unambiguous) prohibition; a scoped Annex II
    prohibition must not trigger exit 1."""
    from eu_ingredient_linter.formula import evaluate_formula as _ev
    reg = _scoped_registry()
    scoped = _ev({"ingredients": [{"ingredient": "CI 44045",
                                   "percent": 0.01, "unit": "%",
                                   "basis": "ready_for_use"}]}, reg)
    assert all(e["prohibition_finding"] != "unambiguous"
               for e in scoped["evaluations"])
    benzene = _ev({"ingredients": [{"ingredient": "Benzene",
                                    "percent": 1, "unit": "%"}]}, reg)
    assert all(e["prohibition_finding"] == "unambiguous"
               for e in benzene["evaluations"])


def _member_registry():
    """Registry with a member whose only evidence is a linked group rule."""
    group = erl.Substance(
        id="group", name="Benzoic acid and its sodium salt",
        cas="65-85-0 / 532-32-1", ec="200-618-2 / 208-534-8",
        determination="allowed_with_conditions",
        rules=[erl.Rule(
            status="allowed_with_conditions", annex="Annex V",
            regulation="(EC) 2009/1223",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at=None, resolution="cosing-annex-v",
            raw={"source_kind": "cosing-annex-v", "source_file": "f",
                 "row_reference": "f#1", "official_reference": "1",
                 "source_sha256": "abc", "entry_number": 1,
                 "reference_number": "1", "accepted": True,
                 "retrieved_at": "2026-01-01T00:00:00+00:00",
                 "conditions_list": [{"field": "max_concentration",
                                      "label": "Maximum concentration in ready "
                                               "for use preparation",
                                      "value": "2.5% (acid)"},
                                     {"field": "product_types",
                                      "label": "Product type, body parts",
                                      "value": "Rinse-off products"}]})],
        sources=["cosing-annex-v"])
    member = erl.Substance(
        id="sodium-benzoate", name="Sodium benzoate", cas="532-32-1",
        ec="208-534-8", determination="unknown", rules=[],
        member_of=[{"group_id": "group",
                    "group_name": "Benzoic acid and its sodium salt",
                    "official_reference": "1", "annex": "Annex V",
                    "source_kind": "cosing-annex-v", "source_file": "f",
                    "row_reference": "f#1", "source_url": "https://x",
                    "source_key": ["cosing-annex-v", "1", "f#1"],
                    "member_name": "sodium benzoate"}],
        sources=[])
    return erl.Registry([group, member], data_updated_at="2026-10-09T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-v": {"present": True, "accepted": True}},
                        metadata={})


def test_formula_preserves_linked_evidence():
    """A member with no own rules but linked group evidence must NOT be reported
    as having no regulatory evidence; the linked rule + conditions survive."""
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium benzoate",
                                           "percent": 0.5, "unit": "%",
                                           "basis": "ready_for_use"}]},
                         _member_registry())["evaluations"][0]
    assert e["is_in_dataset"] is True
    assert e["rules"] == []  # member's own rules
    assert len(e["linked_rules"]) == 1
    lr = e["linked_rules"][0]
    assert lr["annex"] == "Annex V" and lr["status"] == "allowed_with_conditions"
    assert (lr["raw"] or {}).get("official_reference") == "1"
    # The membership record + its source locator survive.
    assert any(m["group_id"] == "group" for m in e["membership"])
    # Unevaluated conditions of the linked rule are surfaced.
    assert any("Rinse-off products" in s for s in e["unresolved_requirements"])
    # Not described as "no regulatory evidence".
    assert not any("no verified rule" in s.lower() for s in e["reasons"])


def test_linked_rule_not_numerically_evaluated():
    """A linked group rule is surfaced as evidence, never numerically evaluated
    or classified as a definitive prohibition without applicability logic."""
    e = evaluate_formula({"ingredients": [{"ingredient": "Sodium benzoate",
                                           "percent": 99, "unit": "%",
                                           "basis": "ready_for_use"}]},
                         _member_registry())["evaluations"][0]
    assert e["numeric_comparison"] is None
    assert e["verdict"] == "requires_manual_review"
    assert e["prohibition_finding"] is None


def _own_plus_linked_registry():
    """Registry for a member that has its OWN Annex III rule AND linked group
    evidence (the Salicylic acid shape: own Annex III ref 98 + linked Annex V
    ref 3 concentration condition)."""
    """Registry for a member that has its OWN Annex III rule AND linked group
    evidence (the Salicylic acid shape: own Annex III ref 98 + linked Annex V
    ref 3 concentration condition)."""
    group = erl.Substance(
        id="salicyl-group", name="Salicylic acid and its salts", cas=None,
        determination="allowed_with_conditions",
        rules=[erl.Rule(
            status="allowed_with_conditions", annex="Annex V",
            regulation="(EC) 2009/1223",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at=None, resolution="cosing-annex-v",
            raw={"source_kind": "cosing-annex-v", "source_file": "v",
                 "row_reference": "v#3", "official_reference": "3",
                 "source_sha256": "abc", "entry_number": 3,
                 "reference_number": "3", "accepted": True,
                 "retrieved_at": "2026-01-01T00:00:00+00:00",
                 "conditions_list": [
                     {"field": "max_concentration",
                      "label": "Maximum concentration in ready for use preparation",
                      "value": "Salicylic acid: 0,5 % (acid)  "
                               "Salicylic acid salts: 0,5 % (acid)"}]})],
        sources=["cosing-annex-v"])
    member = erl.Substance(
        id="salicylic-acid", name="Salicylic acid", cas="69-72-7",
        ec="200-712-3", determination="restricted",
        rules=[erl.Rule(
            status="restricted", annex="Annex III", regulation="(EC) 2009/1223",
            source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
            source_updated_at=None, resolution="cosing-annex-iii",
            raw={"official_reference": "98", "row_reference": "f.txt#98",
                 "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
                 "reference_number": "98", "accepted": True,
                 "conditions_list": [{"field": "warnings", "label": "Warnings",
                                      "value": "children"}]})],
        member_of=[{"group_id": "salicyl-group",
                    "group_name": "Salicylic acid and its salts",
                    "official_reference": "3", "annex": "Annex V",
                    "source_kind": "cosing-annex-v", "source_file": "v",
                    "row_reference": "v#3", "source_url": "https://x",
                    "source_key": ["cosing-annex-v", "3", "v#3"],
                    "member_name": "salicylic acid"}],
        sources=["cosing-annex-iii"])
    return erl.Registry([group, member], data_updated_at="2026-10-10T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-iii": {"present": True, "accepted": True},
                            "cosing-annex-v": {"present": True, "accepted": True}},
                        metadata={})


def test_own_rule_plus_linked_conditions_both_survive():
    """A member with its OWN Annex III rule AND linked Annex V evidence must
    retain BOTH. The linked (group) concentration condition must appear in
    ``unresolved_requirements`` even on the unsupported-rule branch (the bug:
    linked conditions were dropped when an own Annex III rule exists)."""
    e = evaluate_formula({"ingredients": [{"ingredient": "Salicylic acid",
                                           "percent": 0.5, "unit": "%",
                                           "basis": "ready_for_use"}]},
                         _own_plus_linked_registry())["evaluations"][0]
    # Own Annex III rule retained as direct evidence.
    assert ((e["rules"][0].get("raw") or {}).get("official_reference") == "98")
    # Linked Annex V rule retained.
    assert len(e["linked_rules"]) == 1
    assert ((e["linked_rules"][0].get("raw") or {}).get("official_reference") == "3")
    # Linked concentration condition is surfaced (the regression).
    assert any("0,5 % (acid)" in s for s in e["unresolved_requirements"])
    # The linked rule is never numerically evaluated / applicability inferred.
    assert e["numeric_comparison"] is None


def test_surface_conditions_shared_path_aggregates_and_dedups():
    """The shared ``_surface_conditions`` aggregation path surfaces every linked
    rule's conditions; the caller's ``_dedup`` collapses repeated strings across
    rules while direct/linked rule evidence stays separate."""
    from eu_ingredient_linter.formula import _surface_conditions, _dedup
    reasons = ["existing reason"]
    rules = [
        {"raw": {"conditions_list": [
            {"field": "max_concentration", "label": "Max", "value": "0.5% (acid)"},
            {"field": "other", "label": "Other", "value": "Professional use"}]}},
        {"raw": {"conditions_list": [
            {"field": "max_concentration", "label": "Max", "value": "0.5% (acid)"}]}},
    ]
    _surface_conditions(reasons, rules)
    assert "existing reason" in reasons
    assert any("0.5% (acid)" in s for s in reasons)
    assert any("Professional use" in s for s in reasons)
    # The path surfaces the same condition from two rules; the caller dedups.
    assert sum("0.5% (acid)" in s for s in reasons) == 2
    deduped = _dedup(reasons)
    assert sum("0.5% (acid)" in s for s in deduped) == 1
    assert any("Professional use" in s for s in deduped)


def test_label_path_scoped_prohibition_not_counted_as_banned():
    """The ordinary label path must NOT surface a scoped Annex II prohibition
    (CI 44045 'in hair dye products', Hydroquinone 'with the exception of entry
    14') as an unconditional-sounding 'banned', while an unambiguous ban
    (Benzene) still counts as banned."""
    from eu_ingredient_linter import ingredient_report
    reg = _scoped_registry()
    rep = ingredient_report("CI 44045, Benzene", reg)
    assert rep["verdict"] == "FLAGGED"
    assert rep["clean"] is False
    # Only Benzene is an unambiguous prohibition; CI 44045 stays flagged but is
    # NOT counted as a definitive banned finding.
    assert rep["n_banned"] == 1, rep["n_banned"]
    by_ing = {r["ingredient"]: r for r in rep["results"] if r["is_in_dataset"]}
    ci = by_ing["ci 44045"]
    assert ci["prohibition_finding"] == "scope_unresolved", ci["prohibition_finding"]
    assert ci["evaluation"] == "scope_unresolved", ci["evaluation"]
    assert "unresolved" in (ci["evaluation_reason"] or "").lower()
    benz = by_ing["benzene"]
    assert benz["prohibition_finding"] == "unambiguous"
    assert benz["evaluation"] == "prohibition_identified"


def test_label_path_sodium_benzoate_linked_is_not_a_violation():
    """A conditional positive-list (linked Annex V) rule for Sodium benzoate is
    presented as a conditional/restriction finding, never a demonstrated
    violation, and no definitive prohibition is attributed to it."""
    from eu_ingredient_linter import ingredient_report
    reg = _member_registry()
    rep = ingredient_report("Sodium benzoate", reg)
    assert rep["verdict"] == "FLAGGED"
    assert rep["n_banned"] == 0
    r = rep["results"][0]
    assert r["is_in_dataset"] is True
    assert r["determination"] == "unknown"
    assert r["prohibition_finding"] is None
    assert any(lr["status"] == "allowed_with_conditions" for lr in r["linked_rules"])


