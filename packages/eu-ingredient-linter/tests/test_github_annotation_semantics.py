"""Regression tests for GitHub annotation severity, file attribution and escaping.

Regulatory information, not legal advice.

These assert the documented annotation policy that the reusable Action relies on:
an ``error`` is reserved for an unambiguous prohibition or a supported-limit
exceedance; an unresolved scope/exception assessment stays ``warning``; an
unknown / not-in-dataset identity is ``notice``; linked-group evidence is never
promoted to a definitive prohibition. They also assert that the optional
``github_file`` is emitted as an escaped ``file`` property and that the required
disclaimer is present.

They exercise ``render(..., "github")`` directly (the same call the CLI makes),
not YAML string searches.
"""

from __future__ import annotations

import eu_reg_lists as erl
from eu_ingredient_linter.formula import evaluate_formula
from eu_ingredient_linter.reporters import render


def _registry(substances):
    return erl.Registry(substances, data_updated_at="2026-10-09T00:00:00+00:00",
                        verified_sources={
                            "cosing-annex-ii": {"present": True, "accepted": True}},
                        metadata={})


def _rule(annex, status, ref):
    return erl.Rule(
        status=status, annex=annex, regulation="(EC) 2009/1223",
        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
        source_updated_at=None, resolution="cosing-annex-ii",
        raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
             "source_file": "COSING_Annex_II.txt", "source_sha256": "abc",
             "reference_number": ref, "accepted": True})


def _annotations(gh):
    return [ln for ln in gh.splitlines() if ln.startswith("::")]


def test_unambiguous_prohibition_is_error():
    sub = erl.Substance(name="Benzene", id="benzene", cas="71-43-2",
                        ec="200-753-7", determination="banned",
                        rules=[_rule("Annex II", "banned", "47")],
                        sources=["cosing-annex-ii"])
    r = evaluate_formula({"ingredients": [{"ingredient": "Benzene"}]}, _registry([sub]))
    gh = render(r, "github")
    # The evaluator brands this an unambiguous prohibition -> error.
    assert not gh.startswith("::warning")
    assert not gh.rstrip().endswith("0")
    levels = [a.split(" ", 1)[0] for a in _annotations(gh)]
    assert levels[0] == "::error"
    assert "unambiguous_prohibition" in gh


def test_scope_unresolved_is_warning_not_error():
    # CI 44045: an Annex II banned rule whose cross-annex scope is unresolved.
    sub = erl.Substance(name="CI 44045", id="ci44045", cas="2580-56-5",
                        ec="219-943-6", determination="banned",
                        rules=[_rule("Annex II", "banned", "1340"),
                               _rule("Annex IV", "allowed_with_conditions", "69")],
                        sources=["cosing-annex-ii"])
    # Force the scope-unresolved finding: evaluate a plain banned-rule record
    # that the evaluator tags scope_unresolved. We inject the finding directly
    # because the evaluator's annotation mapping (not its classification) is the
    # subject here.
    r = evaluate_formula({"ingredients": [{"ingredient": "CI 44045"}]}, _registry([sub]))
    for e in r["evaluations"]:
        e["prohibition_finding"] = "scope_unresolved"
    gh = render(r, "github")
    levels = [a.split(" ", 1)[0] for a in _annotations(gh)]
    # No ban-derived error; the assessment stays a warning.
    assert "::error" not in levels
    assert "::warning" in levels
    assert "scope_unresolved" in gh


def test_exception_unresolved_is_warning_not_error():
    # Hydroquinone: banned in Annex II but reference to an Annex III exception.
    sub = erl.Substance(name="Hydroquinone", id="hydroquinone", cas="123-31-9",
                        ec="204-617-8", determination="banned",
                        rules=[_rule("Annex II", "banned", "1339"),
                               _rule("Annex III", "restricted", "14")],
                        sources=["cosing-annex-ii"])
    r = evaluate_formula({"ingredients": [{"ingredient": "Hydroquinone"}]},
                         _registry([sub]))
    for e in r["evaluations"]:
        e["prohibition_finding"] = "exception_unresolved"
    gh = render(r, "github")
    levels = [a.split(" ", 1)[0] for a in _annotations(gh)]
    assert "::error" not in levels
    assert "::warning" in levels
    assert "exception_unresolved" in gh


def test_unknown_name_is_notice_not_clean():
    r = evaluate_formula({"ingredients": [{"ingredient": "Aqua"}]},
                         _registry([erl.Substance(name="Benzene", id="benzene",
                                                  cas="71-43-2", ec="200-753-7",
                                                  determination="banned",
                                                  rules=[_rule("Annex II", "banned", "47")],
                                                  sources=["cosing-annex-ii"])]))
    gh = render(r, "github")
    assert "::notice" in gh
    assert "unknown" in gh.lower()
    assert "not a compliance" in gh
    # Never a clean/verdict-passing claim.
    assert "clean" not in gh.lower()


def test_linked_evidence_is_not_promoted_to_definitive_prohibition():
    # A member with no own rules but a linked group rule (e.g. Sodium benzoate →
    # Annex V). The linked rule must NOT force an error even if the group rule's
    # source status is restrictive; it stays a warning and preserves the linked
    # group identity.
    linked = {
        "status": "allowed_with_conditions", "annex": "Annex V",
        "regulation": "(EC) 2009/1223",
        "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
        "raw": {"official_reference": "1", "row_reference": "f.txt#1",
                "source_file": "COSING_Annex_V.txt", "source_sha256": "abc",
                "reference_number": "1", "accepted": True,
                "conditions_list": [{"label": "conditions", "value": "max 0.5%"}]},
    }
    sub = erl.Substance(name="Sodium benzoate", id="sodium-benzoate", cas="532-32-1",
                        ec="208-534-8", determination="unknown", rules=[],
                        sources=[])
    # Inject linked_rules + membership directly (evaluator's group machinery is
    # covered by the group-membership tests); this test guards the annotation
    # mapping only.
    r = evaluate_formula({"ingredients": [{"ingredient": "Sodium benzoate"}]},
                         _registry([sub]))
    for e in r["evaluations"]:
        e["linked_rules"] = [linked]
        e["membership"] = [{"group_name": "Benzoic acid, sodium salt",
                            "annex": "Annex V", "official_reference": "1",
                            "source_kind": "cosing-annex-v", "row_reference": "f.txt#1"}]
    gh = render(r, "github")
    levels = [a.split(" ", 1)[0] for a in _annotations(gh)]
    assert "::error" not in levels
    assert "::warning" in levels
    assert "linked_group_evidence" in gh
    assert "Annex V" in gh


def test_mixed_direct_and_linked_evidence_keeps_origin(tmp_path):
    """A record with both direct and linked rules labels each annotation's origin.

    Salicylic acid has a DIRECT Annex III restriction AND a LINKED Annex V group
    rule. Each annotation must identify its origin and, for linked evidence, the
    source group so it is never read as a definitive direct prohibition.
    """
    direct = {
        "status": "restricted", "annex": "Annex III",
        "regulation": "(EU) 2021/850",
        "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
        "raw": {"official_reference": "98", "row_reference": "f.txt#98",
                "source_file": "COSING_Annex_III.txt", "source_sha256": "abc",
                "reference_number": "98", "accepted": True},
    }
    linked = {
        "status": "allowed_with_conditions", "annex": "Annex V",
        "regulation": "(EU) 2019/1966",
        "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
        "raw": {"official_reference": "3", "row_reference": "f.txt#3",
                "source_file": "COSING_Annex_V.txt", "source_sha256": "abc",
                "reference_number": "3", "accepted": True,
                "conditions_list": [{"label": "conditions", "value": "max 0.5%"}]},
    }
    sub = erl.Substance(name="Salicylic acid", id="salicylic-acid", cas="69-72-7",
                        ec="200-712-3", determination="restricted",
                        rules=[_rule("Annex III", "restricted", "98")],
                        sources=["cosing-annex-iii"])
    r = evaluate_formula({"ingredients": [{"ingredient": "Salicylic acid"}]},
                         _registry([sub]))
    for e in r["evaluations"]:
        e["rules"] = [direct]
        e["linked_rules"] = [linked]
        e["membership"] = [{"group_name": "Salicylic acid and its salts",
                            "annex": "Annex V", "official_reference": "3",
                            "source_kind": "cosing-annex-v", "row_reference": "f.txt#3"}]
    gh = render(r, "github")
    # Both origins appear.
    assert "origin=direct" in gh
    assert "origin=linked group" in gh
    # The linked annotation names the source group and reference.
    assert "source group" in gh and "Salicylic acid and its salts" in gh
    assert "ref 3" in gh
    # The direct annotation keeps its own reference.
    assert "ref 98" in gh
    # Neither origin is promoted to a definitive prohibition (both warnings).
    levels = [a.split(" ", 1)[0] for a in _annotations(gh)]
    assert "::error" not in levels
    assert "::warning" in levels


def test_github_file_property_is_escaped():
    r = evaluate_formula({"ingredients": [{"ingredient": "Benzene"}]}, _registry([
        erl.Substance(name="Benzene", id="benzene", cas="71-43-2", ec="200-753-7",
                      determination="banned",
                      rules=[_rule("Annex II", "banned", "47")],
                      sources=["cosing-annex-ii"])]))
    evil_file = "ingredients %,:\n\r.csv"
    gh = render(r, "github", github_file=evil_file)
    # The file property escapes % CR LF and the property punctuation , : so the
    # path cannot terminate the command or start a new one.
    assert "file=ingredients %25%2C%3A%0A%0D.csv" in gh
    for line in gh.splitlines():
        assert not line.startswith("::error file=") or "ingredients" in line


def test_github_output_includes_disclaimer():
    r = evaluate_formula({"ingredients": [{"ingredient": "Aqua"}]}, _registry([]))
    gh = render(r, "github")
    assert "Regulatory information, not legal advice." in gh
    assert "::notice" in gh
