"""Command-level regressions for the acceptance runner (examples/acceptance_demo.py).


These verify the reproduced defects are fixed:
  * a required condition that fails (impossible timing target / wrong exit probe)
    makes the REAL command return a non-zero status (previously it returned 0);
  * the runner is independent of the gitignored ``.local-reference`` dir AND of
    the production snapshot (it uses a run-owned synthetic snapshot);
  * product acceptance cannot succeed without a successful cosmetic lookup,
    nonempty ingredients, a valid shared check and full required provenance;
  * an empty / missing / malformed / not-found / non-cosmetic replay does NOT
    pass, while a valid unknown cosmetic IS legitimate evidence;
  * a relative ``--out-dir`` does not break the run.

Each test drives the actual ``main()`` entry point (the real command) so the
process-status behaviour is asserted end to end, without issuing live provider
requests.
"""

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

import fixtures

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def synthetic_snapshot(tmp_path_factory):
    tree = fixtures.build_tree(tmp_path_factory.mktemp("snap"))
    return tree / "data" / "processed" / "eu_reg_lists.sqlite"


def _load_demo():
    spec = importlib.util.spec_from_file_location(
        "acceptance_demo", ROOT / "examples" / "acceptance_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


def _run_main(demo, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = demo.main(argv)
    raw = buf.getvalue().strip()
    return code, (json.loads(raw) if raw else None)


def _demo_args(snapshot, extra=None):
    return ["--snapshot", str(snapshot), *(extra or [])]


def test_failing_timing_condition_returns_nonzero(tmp_path, synthetic_snapshot):
    """Impossible timing targets must make the command exit non-zero."""
    demo = _load_demo()
    demo.PROTO_TARGET_SECONDS = -1
    demo.LINTER_TARGET_SECONDS = -1
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot, ["--out-dir", str(tmp_path / "ev")]))
    assert code == 1
    assert report["acceptance_passed"] is False
    failed = {c["name"] for c in report["timing"]["required_conditions"] if not c["passed"]}
    assert "prototype_timing" in failed
    assert "linter_timing" in failed


def test_wrong_invalid_exit_probe_returns_nonzero(tmp_path, synthetic_snapshot):
    """A failing invalid-input exit probe must make the command exit non-zero."""
    demo = _load_demo()
    demo.verify_exit_behavior = lambda out_dir: {
        "invalid_input_exit_code": 0, "invalid_input_expected_2": False,
        "stderr_has_error": False}
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--verify-exit", "--out-dir", str(tmp_path / "ev")]))
    assert code == 1
    assert report["acceptance_passed"] is False
    assert report["verify_exit_behavior"]["invalid_input_expected_2"] is False


def test_runner_does_not_depend_on_local_reference(tmp_path, synthetic_snapshot):
    """The runner must not require the gitignored ``.local-reference`` dir."""
    demo = _load_demo()
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot, ["--out-dir", str(tmp_path / "ev")]))
    assert code == 0
    assert report["acceptance_passed"] is True
    # The retained cited reports are written to the caller-selected out-dir.
    for artifact in (report["timing"]["prototype"]["report_artifact"],
                     report["timing"]["linter"]["report_artifact"]):
        assert Path(artifact["path"]).exists()
        assert ".local-reference" not in Path(artifact["path"]).parts


def test_us_tag_requires_exact_match():
    """Country evidence requires an exact ``en:united-states`` string tag."""
    demo = _load_demo()
    assert demo._is_us_tag("en:united-states") is True
    assert demo._is_us_tag("united-states") is False
    assert demo._is_us_tag("en:us") is False
    assert demo._is_us_tag(["en:united-states"]) is False
    assert demo._is_us_tag("En:United-States") is False


# --- Part B: product-acceptance predicate regressions -----------------------

def _us_beauty(barcode, ingredients="Water, Glycerin, Benzene"):
    return {
        "status": "found_with_ingredients", "product_type": "beauty",
        "name": "Fixture", "brands": "Brand", "source_url": f"https://x/{barcode}",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "ingredients_text": ingredients,
        "countries_tags": ["en:united-states"], "countries": ["United States"],
        "error": None,
    }


def _replay(*products):
    return {"products": dict(products)}


def test_not_found_us_product_does_not_pass(tmp_path, synthetic_snapshot):
    """A not_found US-tagged record with no ingredients must NOT pass."""
    demo = _load_demo()
    rec = _replay(("123", {
        "status": "not_found", "product_type": "beauty",
        "countries_tags": ["en:united-states"],
        "retrieved_at": "2026-01-01T00:00:00+00:00"}))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(rec))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--out-dir", str(tmp_path / "ev")]))
    assert code == 1
    assert report["acceptance_passed"] is False
    row = report["live"]["products"][0]
    assert row["demonstration_ok"] is False


def test_empty_product_evidence_does_not_pass(tmp_path, synthetic_snapshot):
    """An empty product list is never a passing demonstration."""
    demo = _load_demo()
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"products": {}}))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--out-dir", str(tmp_path / "ev")]))
    assert code == 2
    assert report is None


def test_missing_replay_file_is_actionable(tmp_path, synthetic_snapshot):
    """A missing replay path must yield a non-zero actionable result."""
    demo = _load_demo()
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(tmp_path / "nope.json"),
                          "--out-dir", str(tmp_path / "ev")]))
    assert code == 2


def test_malformed_replay_file_is_actionable(tmp_path, synthetic_snapshot):
    """A malformed replay path must yield a non-zero actionable result."""
    demo = _load_demo()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(bad), "--out-dir", str(tmp_path / "ev")]))
    assert code == 2


def test_food_product_does_not_pass(tmp_path, synthetic_snapshot):
    """A non-cosmetic (food) record with a US tag must NOT pass."""
    demo = _load_demo()
    rec = _replay(("456", {
        "status": "found_with_ingredients", "product_type": "food",
        "source_url": "https://x/456", "retrieved_at": "2026-01-01T00:00:00+00:00",
        "ingredients_text": "Sugar, Water",
        "countries_tags": ["en:united-states"], "error": None}))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(rec))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--out-dir", str(tmp_path / "ev")]))
    assert code == 1
    assert report["acceptance_passed"] is False
    assert report["live"]["products"][0]["demonstration_ok"] is False


def test_valid_unknown_cosmetic_is_legitimate(tmp_path, synthetic_snapshot):
    """A valid cosmetic that resolves to an unknown outcome is legitimate evidence."""
    demo = _load_demo()
    rec = _replay(("789", {
        "status": "found_with_ingredients", "product_type": "beauty",
        "source_url": "https://x/789", "retrieved_at": "2026-01-01T00:00:00+00:00",
        "ingredients_text": "Water, UnknownFixtureSubstance",
        "countries_tags": ["en:united-states"], "error": None}))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(rec))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--out-dir", str(tmp_path / "ev")]))
    assert code == 0
    assert report["acceptance_passed"] is True
    assert report["live"]["products"][0]["demonstration_ok"] is True


def test_original_acceptance_requires_three_distinct(tmp_path, synthetic_snapshot):
    """The original three-product acceptance requires 3 DISTINCT successful products."""
    demo = _load_demo()
    rec = _replay(
        ("1", _us_beauty("1")), ("2", _us_beauty("2")), ("3", _us_beauty("3")))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(rec))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--original-acceptance",
                          "--out-dir", str(tmp_path / "ev")]))
    assert code == 0
    assert report["acceptance_passed"] is True
    assert report["acceptance_mode"] == "original-three-product"
    assert report["required_successful_products"] == 3


def test_original_acceptance_fails_with_two_duplicate(tmp_path, synthetic_snapshot):
    """Two distinct products (with a duplicate) must NOT satisfy 3-distinct."""
    demo = _load_demo()
    rec = _replay(
        ("1", _us_beauty("1")), ("1", _us_beauty("1")))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(rec))
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--replay", str(replay), "--original-acceptance",
                          "--out-dir", str(tmp_path / "ev")]))
    assert code == 1
    assert report["acceptance_passed"] is False


def test_citation_validator_rejects_missing_evidence():
    """A finding whose rule lacks source URL / snapshot time must not validate."""
    demo = _load_demo()
    # Rule with a regulation but no source URL and a report without a snapshot time.
    bad_rule = {"regulation": "(EC) 1223/2009", "annex": "Annex II"}
    bad_check = {"n_ingredients": 1, "data_updated_at": None,
                 "results": [{"rules": [bad_rule], "linked_rules": []}]}
    assert demo._check_is_valid(bad_check) is False
    # A valid finding carries source URL + report snapshot time.
    good_rule = {"regulation": "(EC) 1223/2009", "annex": "Annex II",
                 "source_url": "https://ec.europa.eu/cosing/"}
    good_check = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
                  "results": [{"rules": [good_rule], "linked_rules": []}]}
    assert demo._check_is_valid(good_check) is True


def test_rule_evidence_requires_actual_regulation_not_annex():
    """An annex is NOT a substitute for a regulation reference."""
    demo = _load_demo()
    annex_only = {"annex": "Annex II",
                  "source_url": "https://ec.europa.eu/cosing/"}
    check = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
             "results": [{"rules": [annex_only], "linked_rules": []}]}
    assert demo._check_is_valid(check) is False


def test_rule_evidence_requires_actual_url_not_updated_at():
    """A publication/retrieval date is NOT a substitute for a source URL."""
    demo = _load_demo()
    updated_only = {"regulation": "(EC) 1223/2009", "annex": "Annex II",
                    "source_updated_at": "2026-01-01T00:00:00+00:00"}
    check = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
             "results": [{"rules": [updated_only], "linked_rules": []}]}
    assert demo._check_is_valid(check) is False


def test_rule_evidence_rejects_empty_rule():
    """An empty rule object has no provenance and must be rejected."""
    demo = _load_demo()
    empty_rule = {}
    check = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
             "results": [{"determination": "banned", "rules": [empty_rule],
                          "linked_rules": []}]}
    assert demo._check_is_valid(check) is False


def test_check_is_valid_rejects_mismatched_result_count():
    """An empty results array claiming n_ingredients=1 is structurally invalid."""
    demo = _load_demo()
    mismatched = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
                  "results": []}
    assert demo._check_is_valid(mismatched) is False


def test_check_is_valid_rejects_a_later_linked_rule_without_url():
    """Removing the required URL from a LATER linked rule (not rule[0]) must fail."""
    demo = _load_demo()
    complete_rule = {"regulation": "(EC) 1223/2009", "annex": "Annex II",
                     "source_url": "https://ec.europa.eu/cosing/"}
    broken_later_rule = {"regulation": "(EC) 1907/2006", "annex": "SVHC"}
    check = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
             "results": [{"determination": "restricted", "rules": [],
                          "linked_rules": [complete_rule, broken_later_rule]}]}
    assert demo._check_is_valid(check) is False


def test_check_is_valid_unknown_needs_no_rule_but_valid_structure():
    """A legitimate unknown outcome needs no fabricated rule, but its result list
    must still match the declared ingredient count (no empty-results cheat)."""
    demo = _load_demo()
    unknown = {"n_ingredients": 1, "data_updated_at": "2026-01-01T00:00:00+00:00",
               "results": [{"determination": "unknown", "rules": [],
                            "linked_rules": []}]}
    assert demo._check_is_valid(unknown) is True


def test_check_is_valid_requires_nonempty_ingredients():
    """A valid check must have at least one ingredient."""
    demo = _load_demo()
    empty_check = {"n_ingredients": 0, "data_updated_at": "2026-01-01T00:00:00+00:00",
                   "results": []}
    assert demo._check_is_valid(empty_check) is False


def test_check_is_valid_requires_rule_for_finding():
    """An in-dataset finding with a non-unknown determination and NO rule cannot
    be a valid demonstration (headline key presence is not enough)."""
    demo = _load_demo()
    finding_no_rule = {"n_ingredients": 1,
                       "data_updated_at": "2026-01-01T00:00:00+00:00",
                       "results": [{"determination": "banned",
                                    "rules": [], "linked_rules": []}]}
    assert demo._check_is_valid(finding_no_rule) is False


def test_relative_out_dir_works(tmp_path, synthetic_snapshot):
    """A relative --out-dir is resolved before the subprocess cwd changes."""
    demo = _load_demo()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    import os
    os.chdir(cwd)
    try:
        code, report = _run_main(
            demo, _demo_args(synthetic_snapshot, ["--out-dir", "ev"]))
    finally:
        os.chdir(ROOT)
    assert code == 0
    assert report["acceptance_passed"] is True
    assert report["timing"]["prototype"]["report_artifact"]["path"]


def test_enforce_real_workload_rejects_small_fixture(tmp_path, synthetic_snapshot):
    """The stated >=1000-token workload is NOT silently claimed from a small
    synthetic fixture: --enforce-real-workload makes it an explicit failing
    condition when the batch has fewer tokens."""
    demo = _load_demo()
    code, report = _run_main(
        demo, _demo_args(synthetic_snapshot,
                         ["--enforce-real-workload", "--out-dir",
                          str(tmp_path / "ev")]))
    assert code == 1
    assert report["original_workload_condition_enforced"] is True
    failed = {c["name"] for c in report["timing"]["required_conditions"]
              if not c["passed"]}
    assert "linter_workload_met" in failed
    assert report["timing"]["linter"]["batch_tokens"] < 1000
