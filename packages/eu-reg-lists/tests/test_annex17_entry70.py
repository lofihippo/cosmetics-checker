"""Tests for the source-bound REACH Annex XVII entry 70 cosmetic evaluator.

"""
from __future__ import annotations

from decimal import Decimal

from eu_reg_lists.annex17_entry70 import (
    ENTRY70_SOURCE,
    ENTRY70_SOURCE_KIND,
    MARKET_THRESHOLD,
    evaluate_entry70,
    identify_substance,
)


def _active_sources():
    """An accepted, compatible entry 70 source/mapping record.

    The gating contract (Part C) requires a snapshot to carry an accepted
    mapping record for entry 70 evaluation/coverage to activate; these focused
    evaluator tests pass one explicitly (snapshot-level activation is covered
    by the release-policy and integration tests).
    """
    record = {
        "present": True,
        "accepted": True,
        "kind": "reach-legal-act",
        "files": {
            "OJ_L_202401328_EN.pdf": {
                "sha256": ENTRY70_SOURCE["document_sha256"],
                "byte_len": 488977,
            }
        },
        "data_updated_at": "2026-01-01T00:00:00+00:00",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "mapping": {
            "mapping_version": ENTRY70_SOURCE["mapping_version"],
            "document_sha256": ENTRY70_SOURCE["document_sha256"],
            "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
            "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
        },
    }
    return {ENTRY70_SOURCE_KIND: record}


def _eval(name_or_id, **kw):
    """Call the evaluator with an accepted entry 70 source record injected.

    Defaults the concentration basis to the marketed (ready-for-use) basis so
    the trigger/comparison tests assert the numeric semantics; missing-basis and
    incompatible-basis behaviour is covered by its own tests.
    """
    if "basis" not in kw:
        kw["basis"] = "ready_for_use"
    return evaluate_entry70(name_or_id, verified_sources=_active_sources(), **kw)


def test_exact_threshold_equal_triggers():
    """'equal to or greater than 0,1 % by weight' -> exactly 0.1% triggers."""
    r = _eval("D4", percent=Decimal("0.1"), unit="%",
              product_type="wash-off", assessment_date="2027-01-01")
    assert r.state == "applicable_exceeds"
    assert r.threshold == "0.1 % by weight"
    assert r.operator == ">="


def test_just_below_threshold_does_not_trigger():
    r = _eval("D4", percent=Decimal("0.099"), unit="%",
              product_type="wash-off", assessment_date="2027-01-01")
    assert r.state == "applicable_within"


def test_washoff_effective_2020_01_31():
    r = _eval("D4", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2020-01-31")
    assert r.state == "applicable_exceeds"
    assert r.effective_date == "2020-01-31"
    assert r.branch == "washoff_cosmetic"


def test_washoff_before_effective_is_transition_not_effective():
    r = _eval("D4", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2020-01-30")
    assert r.state == "transition_not_effective"


def test_other_cosmetic_effective_2027_06_06():
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="other_cosmetic", assessment_date="2027-06-06")
    assert r.state == "applicable_exceeds"
    assert r.effective_date == "2027-06-06"
    assert r.branch == "other_cosmetic"


def test_keep_on_maps_to_other_cosmetic():
    # An explicit non-wash-off cosmetic product ("leave-on") is an explicit
    # 3(b) branch, distinct from the ambiguous bare word "other".
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="leave-on", assessment_date="2027-06-06")
    assert r.state == "applicable_exceeds"
    assert r.branch == "other_cosmetic"


def test_generic_cosmetic_is_unresolved():
    # The generic word "cosmetic" does not establish whether the product is
    # washed off; it must stay unresolved (never assumed to be a leave-on /
    # other product).
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="cosmetic", assessment_date="2027-06-06")
    assert r.state == "missing_context"
    assert r.supported is False
    assert "Cosmetic product type" in " ".join(r.unresolved_requirements)


def test_ambiguous_other_is_unresolved():
    # The bare word "other" (without "cosmetic") is ambiguous: it does not
    # establish whether the product is a cosmetic at all, so it stays unresolved
    # rather than being treated as evidence of a leave-on cosmetic product.
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="other", assessment_date="2027-06-06")
    assert r.state == "missing_context"
    assert r.supported is False


def test_other_cosmetic_before_effective_transition():
    r = _eval("D6", percent=Decimal("0.5"), unit="%",
              product_type="leave-on", assessment_date="2027-06-05")
    assert r.state == "transition_not_effective"
    assert r.effective_date == "2027-06-06"


def test_missing_concentration_is_missing_context():
    r = _eval("D4", unit="%", product_type="wash-off",
              assessment_date="2027-01-01")
    assert r.state == "missing_context"
    assert r.supported is False
    assert "Concentration" in " ".join(r.unresolved_requirements)


def test_missing_product_type_is_missing_context():
    r = _eval("D4", percent=Decimal("0.5"), unit="%",
              assessment_date="2027-01-01")
    assert r.state == "missing_context"
    assert "Cosmetic product type" in " ".join(r.unresolved_requirements)


def test_missing_assessment_date_is_missing_context():
    r = _eval("D4", percent=Decimal("0.5"), unit="%",
              product_type="wash-off")
    assert r.state == "missing_context"
    assert "Assessment date" in " ".join(r.unresolved_requirements)


def test_missing_basis_is_missing_context():
    """A missing concentration basis cannot establish the marketed comparison."""
    r = _eval("D4", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2027-01-01",
              basis=None)
    assert r.state == "missing_context"
    assert "Marketed" in " ".join(r.unresolved_requirements)


def test_incompatible_raw_material_basis_is_missing_context():
    """A raw-material percentage does NOT establish the marketed cosmetic
    concentration; it must stay unresolved, never silently converted."""
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2028-01-01",
              basis="raw_material")
    assert r.state == "missing_context"
    assert r.supported is False
    assert "Marketed" in " ".join(r.unresolved_requirements)


def test_ready_for_use_false_is_missing_context():
    """ready_for_use=false is contradictory to the marketed basis."""
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2028-01-01",
              basis="ready_for_use", ready_for_use=False)
    assert r.state == "missing_context"
    assert "Marketed" in " ".join(r.unresolved_requirements)


def test_marketed_basis_aliases_are_supported():
    for basis in ("ready_for_use", "marketed_product", "finished_product",
                  "cosmetic_product", "product", "mixture"):
        r = _eval("D5", percent=Decimal("0.5"), unit="%",
                  product_type="wash-off", assessment_date="2028-01-01",
                  basis=basis)
        assert r.state == "applicable_exceeds", basis
        assert r.basis == "ready_for_use", basis


def test_exact_decimal_concentration_preserved():
    """An exact high-precision decimal is preserved in the result."""
    r = _eval("D5", percent=Decimal("0.50000000000000001"), unit="%",
              product_type="wash-off", assessment_date="2028-01-01")
    assert r.state == "applicable_exceeds"
    assert r.submitted_percent == "0.50000000000000001"



def test_unsupported_unit_is_missing_context():
    r = _eval("D4", percent=Decimal("0.5"), unit="ppm",
              product_type="wash-off", assessment_date="2027-01-01")
    assert r.state == "missing_context"


def test_washoff_only_D4_D5_not_D6():
    r = _eval("D6", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2027-01-01")
    assert r.state == "unsupported_use"


def test_unsupported_use_dry_cleaning():
    r = _eval("D5", percent=Decimal("0.5"), unit="%",
              product_type="dry-cleaning", assessment_date="2027-01-01")
    assert r.state == "unsupported_use"
    assert r.supported is False


def test_non_entry70_substance():
    r = _eval("Benzene", percent=Decimal("0.5"), unit="%",
              product_type="wash-off", assessment_date="2027-01-01")
    assert r.state == "not_entry70_substance"
    assert r.supported is False


def test_source_not_active_blocks_evaluation():
    """An empty / historical CosIng-only snapshot gives no entry 70 result."""
    r = evaluate_entry70("D4", percent=Decimal("0.5"), unit="%",
                         product_type="wash-off", assessment_date="2027-01-01",
                         verified_sources={})
    assert r.state == "source_not_active"
    assert r.supported is False
    assert "not source-backed" in (r.reason or "")


def test_source_record_without_timestamp_uses_effective_snapshot_time():
    """A promoted source record with NO per-source data_updated_at must have the
    effective loaded snapshot timestamp threaded into the evidence provenance,
    rather than null nested timestamps (Part B regression)."""
    from eu_reg_lists.annex17_entry70 import (ENTRY70_SOURCE, ENTRY70_SOURCE_KIND,
                                              evaluate_entry70)
    # Active compatible record but deliberately WITHOUT a per-source
    # ``data_updated_at`` (the promoted source's case).
    record = {
        "present": True, "accepted": True, "kind": "reach-legal-act",
        "files": {"OJ.pdf": {"sha256": ENTRY70_SOURCE["document_sha256"],
                             "byte_len": 1}},
        "retrieved_at": "2026-01-02T00:00:00+00:00",
        "mapping": {
            "mapping_version": ENTRY70_SOURCE["mapping_version"],
            "document_sha256": ENTRY70_SOURCE["document_sha256"],
            "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
            "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
        },
    }
    r = evaluate_entry70(
        "D4", percent=Decimal("0.5"), unit="%", product_type="wash-off",
        assessment_date="2028-01-01", basis="ready_for_use",
        verified_sources={ENTRY70_SOURCE_KIND: record},
        snapshot_data_updated_at="2026-10-14T07:32:13.087198+00:00")
    assert r.state == "applicable_exceeds"
    d = r.to_dict()
    # Effective snapshot time is threaded into provenance + the citation field.
    assert d["data_updated_at"] == "2026-10-14T07:32:13.087198+00:00"
    assert d["provenance"]["data_updated_at"] == \
        "2026-10-14T07:32:13.087198+00:00"
    # Snapshot time remains distinct from retrieval (download) time.
    assert d["provenance"]["retrieved_at"] == "2026-01-02T00:00:00+00:00"
    # Snapshot time is never substituted for the publication date.
    assert d["source"]["publication_date"] == "2024-05-17"


def test_evaluator_without_loaded_snapshot_time_falls_back_to_source():
    """Direct evaluator callers that omit the effective snapshot time fall back
    to the source record's own timestamp (legacy path), never invent one."""
    record = {
        "present": True, "accepted": True,
        "data_updated_at": "2026-01-01T00:00:00+00:00",
        "mapping": {
            "mapping_version": ENTRY70_SOURCE["mapping_version"],
            "document_sha256": ENTRY70_SOURCE["document_sha256"],
            "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
            "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
        },
    }
    r = evaluate_entry70(
        "D4", percent=Decimal("0.5"), unit="%", product_type="wash-off",
        assessment_date="2028-01-01", basis="ready_for_use",
        verified_sources={ENTRY70_SOURCE_KIND: record},
        snapshot_data_updated_at=None)
    assert r.to_dict()["data_updated_at"] == "2026-01-01T00:00:00+00:00"


def test_identify_substance_by_cas_ec_short():
    assert identify_substance("556-67-2") == "D4"
    assert identify_substance("EC 208-764-9") == "D5"
    assert identify_substance("d6") == "D6"
    assert identify_substance("Octamethylcyclotetrasiloxane") == "D4"
    assert identify_substance("Benzene") is None


def test_source_identity_present():
    assert ENTRY70_SOURCE["celex"] == "32024R1328"
    assert ENTRY70_SOURCE["entry"] == "70"
    assert ENTRY70_SOURCE["document_sha256"] == (
        "2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966"
    )
    assert MARKET_THRESHOLD == Decimal("0.1")


def test_mapping_content_hash_different_for_changed_mapping():
    """A legitimate semantic mapping change has a different reproducible identity."""
    from eu_reg_lists.annex17_entry70 import mapping_content_hash
    m1 = {"mapping_version": 1, "document_sha256": "a",
          "supported_substances": ["D4", "D5", "D6"],
          "supported_branches": ["washoff_cosmetic", "other_cosmetic"]}
    m2 = {"mapping_version": 2, "document_sha256": "a",
          "supported_substances": ["D4", "D5", "D6"],
          "supported_branches": ["washoff_cosmetic", "other_cosmetic"]}
    m3 = {"mapping_version": 1, "document_sha256": "b",
          "supported_substances": ["D4", "D5", "D6"],
          "supported_branches": ["washoff_cosmetic", "other_cosmetic"]}
    assert mapping_content_hash(m1) != mapping_content_hash(m2)
    assert mapping_content_hash(m1) != mapping_content_hash(m3)
    assert mapping_content_hash(m1) == mapping_content_hash(dict(m1))


def test_canonical_basis_maps_aliases():
    from eu_reg_lists.annex17_entry70 import _canonical_basis
    assert _canonical_basis("ready_for_use") == "ready_for_use"
    assert _canonical_basis("marketed_product") == "ready_for_use"
    assert _canonical_basis("raw_material") is None
    assert _canonical_basis(None) is None


def test_mapping_compatible_rejects_incompatible_records():
    """The release-gating check only accepts an internally-consistent, canonical
    mapping contract (not merely the module's existence)."""
    from eu_reg_lists.annex17_entry70 import _mapping_compatible
    base = {
        "present": True, "accepted": True,
        "mapping": {
            "mapping_version": 1, "document_sha256": "x",
            "supported_substances": ["D4", "D5", "D6"],
            "supported_branches": ["washoff_cosmetic", "other_cosmetic"],
        },
    }
    assert _mapping_compatible(base) is True
    assert _mapping_compatible(None) is False
    assert _mapping_compatible({"present": True, "accepted": False,
                                "mapping": base["mapping"]}) is False
    assert _mapping_compatible({**base, "mapping": {}}) is False
    assert _mapping_compatible({**base, "mapping": "not-a-dict"}) is False
    # missing document hash / incompatible version / branch set.
    no_doc = {**base, "mapping": {k: v for k, v in base["mapping"].items()
                                  if k != "document_sha256"}}
    assert _mapping_compatible(no_doc) is False
    bad_ver = {**base, "mapping": {**base["mapping"], "mapping_version": 999}}
    assert _mapping_compatible(bad_ver) is False
    bad_subs = {**base, "mapping": {**base["mapping"],
                                    "supported_substances": ["D4"]}}
    assert _mapping_compatible(bad_subs) is False
    bad_branches = {**base, "mapping": {**base["mapping"],
                                        "supported_branches": ["washoff_cosmetic"]}}
    assert _mapping_compatible(bad_branches) is False


def test_unsupported_product_type_is_unsupported_use():
    """A recognised non-cosmetic product type is an explicit unsupported use."""
    r = _eval("D5", percent=Decimal("0.5"), unit="%", product_type="device",
              assessment_date="2028-01-01")
    assert r.state == "unsupported_use"


def test_mapping_compatible_rejects_non_list_subs_branches():
    from eu_reg_lists.annex17_entry70 import _mapping_compatible
    base = {
        "present": True, "accepted": True,
        "mapping": {
            "mapping_version": 1, "document_sha256": "x",
            "supported_substances": ["D4", "D5", "D6"],
            "supported_branches": ["washoff_cosmetic", "other_cosmetic"],
        },
    }
    assert _mapping_compatible({**base, "mapping": {**base["mapping"],
                                                    "supported_substances": "D4"}}) is False
    assert _mapping_compatible({**base, "mapping": {**base["mapping"],
                                                    "supported_branches": "x"}}) is False


def test_source_record_and_accepted_mapping_inactive_paths():
    from eu_reg_lists.annex17_entry70 import source_record, accepted_source_mapping
    assert source_record({}) is None
    assert source_record({"reach-annex-xvii-entry70": {"present": True,
                                                       "accepted": False}}) is None
    assert accepted_source_mapping({}) is None
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE_KIND as KIND
    active = source_record(_active_sources())
    assert active is not None
    m = accepted_source_mapping(_active_sources())
    assert m and m["mapping_version"] == 1
