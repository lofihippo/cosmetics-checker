"""Release-boundary regression tests: ECHA deferral, coverage, and distribution.


These use ONLY synthetic markers under a ``data/raw/echa-*`` dir (never real
ECHA data) to verify that a deferred ECHA source is never treated as available
and never distributed, even when it is recorded as present+accepted in an input
tree's provenance or physically present in a formerly-accepted directory.
"""

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "tests"))

from eu_reg_lists.release_policy import (  # noqa: E402
    coverage_dict, effective_available, effective_not_included,
    release_blockers, deferred_with_raw_data_in_tree,
)
from eu_reg_lists.annex17_entry70 import (  # noqa: E402
    ENTRY70_DOCUMENT_FILE,
    ENTRY70_MAPPING_FILE,
    ENTRY70_SOURCE, ENTRY70_SOURCE_KIND,
    MAPPING_SCHEMA_VERSION,
    mapping_content_hash,
)
import fixtures  # noqa: E402
import snapshot  # noqa: E402
from normalize import compute_content_hash  # noqa: E402


def _sources(*present_accept):
    """Build a verified_sources dict from iterables of (key, present, accepted)."""
    return {
        k: {"kind": "cosing-annex" if k.startswith("cosing") else "echa",
             "present": bool(p), "accepted": bool(a), "files": {}}
        for k, p, a in present_accept
    }


def test_deferred_never_available_even_when_present_accepted():
    """A deferred ECHA source recorded present+accepted is NOT available."""
    src = _sources(("cosing-annex-ii", True, True),
                   ("echa-svhc", True, True))
    assert "echa-svhc" not in effective_available(src)
    assert "echa-svhc" in effective_not_included(src)
    assert "echa-svhc" in release_blockers(src)


def test_deferred_present_accepted_is_release_blocker():
    """A deferred source that verifies is still a release blocker."""
    src = _sources(("cosing-annex-ii", True, True), ("echa-annex-xiv", True, True))
    assert release_blockers(src) == ["echa-annex-xiv"]


def test_ordinary_cosing_not_blocked():
    src = _sources(("cosing-annex-ii", True, True), ("cosing-annex-iii", True, True))
    assert release_blockers(src) == []
    assert effective_available(src) == ["cosing-annex-ii", "cosing-annex-iii"]


def test_empty_snapshot_does_not_advertise_entry70():
    """An empty / historical CosIng-only snapshot advertises no entry 70."""
    cov = coverage_dict({})
    assert cov["annex17_entries"] == []
    assert cov["annex17_branches"] == []
    assert cov["annex17_source"] is None


def test_entry70_source_not_deferred_or_blocker():
    """The entry70 legal-act source is NOT an ECHA deferred source."""
    src = {ENTRY70_SOURCE_KIND: {"present": True, "accepted": True,
                                 "kind": "reach-legal-act", "files": {}}}
    assert ENTRY70_SOURCE_KIND not in effective_not_included(src)
    assert release_blockers(src) == []
    assert ENTRY70_SOURCE_KIND not in effective_available(src)  # not a rule list


def test_entry70_advertised_only_with_accepted_mapping():
    """Coverage advertises entry 70 only under an accepted mapping contract."""
    # Present+accepted but no compatible mapping contract -> NOT advertised.
    src = {ENTRY70_SOURCE_KIND: {"present": True, "accepted": True,
                                 "kind": "reach-legal-act", "files": {},
                                 "mapping": {"document_sha256": "wrong",
                                             "mapping_version": 1,
                                             "supported_branches": []}}}
    cov = coverage_dict(src)
    assert cov["annex17_entries"] == []
    # Compatible mapping contract -> advertised.
    src[ENTRY70_SOURCE_KIND]["mapping"] = {
        "document_sha256": ENTRY70_SOURCE["document_sha256"],
        "mapping_version": ENTRY70_SOURCE["mapping_version"],
        "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
        "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
    }
    cov = coverage_dict(src)
    assert cov["annex17_entries"] == ["70"]
    assert set(cov["annex17_branches"]) == {"washoff_cosmetic", "other_cosmetic"}
    assert cov["annex17_source"] == ENTRY70_SOURCE["source_url"]


def test_deferred_raw_dir_detected_in_tree(tmp_path):
    root = tmp_path / "tree"
    d = root / "data" / "raw" / "echa-svhc"
    d.mkdir(parents=True)
    (d / "SYNTHETIC.csv").write_text("SYNTHETIC,PLACEHOLDER\n")
    assert deferred_with_raw_data_in_tree(root) == ["echa-svhc"]


def test_deferred_empty_dir_not_a_blocker(tmp_path):
    root = tmp_path / "tree"
    (root / "data" / "raw" / "echa-svhc").mkdir(parents=True)
    assert deferred_with_raw_data_in_tree(root) == []


def test_export_rejects_synthetic_deferred_raw_file(tmp_path):
    """The full reproduce case: a formerly-accepted echa-svhc raw file verifies
    as a well-formed snapshot but is REJECTED for release export."""
    tree = fixtures.build_tree(tmp_path / "tree")
    raw = tree / "data" / "raw"
    source = raw / "echa-svhc" / "SYNTHETIC.csv"
    source.parent.mkdir()
    source.write_text("SYNTHETIC PLACEHOLDER: NOT OFFICIAL ECHA DATA\n")
    manifest_path = raw / "fetch_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["echa-svhc"] = {"accepted": True, "fetched": True,
                             "filename": source.name,
                             "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                             "retrieved_at": "2026-01-01T00:00:00+00:00"}
    manifest_path.write_text(json.dumps(manifest))
    bundle = tmp_path / "synthetic.zip"
    with pytest.raises(ValueError, match="release-eligible"):
        snapshot.export_snapshot(tree, bundle)
    # Never leaves a success-shaped artifact.
    assert not bundle.exists()


def test_ordinary_cosing_fixture_still_releases(tmp_path):
    """An ordinary CosIng bundle still verifies and can be released."""
    tree = fixtures.build_tree(tmp_path / "tree")
    bundle = tmp_path / "ok.zip"
    report = snapshot.export_snapshot(tree, bundle)
    assert report["content_hash"]
    restored = tmp_path / "restored"
    rep = snapshot.verify_snapshot(bundle, restored)
    assert rep["count_verified_substances"] > 0


def _inject_entry70_source(tree):
    """Attach an accepted, internally-consistent entry 70 source/mapping record
    to a snapshot tree's verified_sources.

    Deterministic (no real official PDF bytes needed for unit tests): writes a
    synthetic retained document + mapping JSON into the raw source dir, sets the
    matching accepted fetch_manifest entry, and records a mapping contract whose
    document hash matches those retained bytes. The snapshot integrity verifier
    binds the recorded mapping to the retained bytes; document *authenticity*
    (the real Regulation 2024/1328 bytes) is a separately verified candidate
    property, not assumed here."""
    meta = json.loads((tree / "data/processed/metadata.json").read_text())
    source_file = ENTRY70_MAPPING_FILE
    doc_file = ENTRY70_DOCUMENT_FILE
    # Retained bytes: a synthetic official-document placeholder + the mapping.
    doc_bytes = b"synthetic-entry70-document-bytes"
    doc_sha = hashlib.sha256(doc_bytes).hexdigest()
    mapping = {
        "mapping_version": ENTRY70_SOURCE["mapping_version"],
        "document_sha256": doc_sha,
        "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
        "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
    }
    source_dir = tree / "data/raw" / ENTRY70_SOURCE_KIND
    source_dir.mkdir(parents=True, exist_ok=True)
    mapping_bytes = json.dumps(mapping, indent=2).encode("utf-8")
    (source_dir / doc_file).write_bytes(doc_bytes)
    (source_dir / source_file).write_bytes(mapping_bytes)
    mapping_sha = hashlib.sha256(mapping_bytes).hexdigest()
    # A matching accepted fetch_manifest entry so snapshot integrity checks pass.
    manifest = json.loads((tree / "data/raw/fetch_manifest.json").read_text())
    manifest[ENTRY70_SOURCE_KIND] = {
        "fetched": True, "accepted": True, "filename": source_file,
        "sha256": mapping_sha, "byte_len": len(mapping_bytes),
        "retrieved_at": "2024-05-18T00:00:00+00:00",
    }
    (tree / "data/raw/fetch_manifest.json").write_text(json.dumps(manifest, indent=2))
    meta["verified_sources"][ENTRY70_SOURCE_KIND] = {
        "kind": "reach-legal-act",
        "present": True, "accepted": True, "fetched": True, "retrieval_error": None,
        "files": {
            doc_file: {"sha256": doc_sha, "byte_len": len(doc_bytes)},
            source_file: {"sha256": mapping_sha, "byte_len": len(mapping_bytes)},
        },
        "data_updated_at": meta["data_updated_at"],
        "retrieved_at": "2024-05-18T00:00:00+00:00",
        "mapping": {**mapping, "document_sha256": doc_sha},
        "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        "mapping_content_hash": mapping_content_hash({**mapping, "document_sha256": doc_sha}),
    }
    meta["snapshot"] = meta["verified_sources"]
    # Recompute the content hash over substances + sources (the entry 70 source
    # record is part of the reproducible contract) and rewrite the SQLite
    # metadata so SQLite/metadata.json stay consistent (as snapshot validates).
    records = [json.loads(line) for line in
               (tree / "data/processed/eu_reg_lists.jsonl").read_text().splitlines()
               if line.strip()]
    meta["content_hash"] = compute_content_hash(records, meta["verified_sources"])
    import normalize as _norm
    _norm._write_sqlite(records, meta, tree / "data/processed/eu_reg_lists.sqlite")
    (tree / "data/processed/metadata.json").write_text(json.dumps(meta, indent=2))


def test_entry70_source_roundtrips_in_bundle(tmp_path):
    """A snapshot carrying an accepted entry 70 source record exports, verifies
    and reimports with the mapping intact (reproducible results outside the
    working checkout)."""
    tree = fixtures.build_tree(tmp_path / "tree")
    _inject_entry70_source(tree)
    # Re-verify the modified tree before export.
    snapshot.validate_tree(tree)
    bundle = tmp_path / "candidate.zip"
    report = snapshot.export_snapshot(tree, bundle)
    restored = tmp_path / "restored"
    rep = snapshot.verify_snapshot(bundle, restored)
    assert rep["content_hash"] == report["content_hash"]
    # Reimported snapshot carries the entry 70 source record and advertises it.
    meta = json.loads((restored / "data/processed/metadata.json").read_text())
    assert meta["verified_sources"][ENTRY70_SOURCE_KIND]["accepted"] is True
    cov = coverage_dict(meta["verified_sources"])
    assert cov["annex17_entries"] == ["70"]
    assert set(cov["annex17_branches"]) == {"washoff_cosmetic", "other_cosmetic"}


def test_entry70_binding_rejects_missing_official_document(tmp_path):
    """A source with only a mapping JSON (no retained official document) fails
    verification — it is never accepted as an active, bound entry 70 source."""
    tree = fixtures.build_tree(tmp_path / "tree")
    _inject_entry70_source(tree)
    # Delete the retained official document; the mapping/metadata stay.
    (tree / "data/raw" / ENTRY70_SOURCE_KIND / ENTRY70_DOCUMENT_FILE).unlink()
    meta = json.loads((tree / "data/processed/metadata.json").read_text())
    src = meta["verified_sources"][ENTRY70_SOURCE_KIND]
    del src["files"][ENTRY70_DOCUMENT_FILE]
    (tree / "data/processed/metadata.json").write_text(json.dumps(meta, indent=2))
    with pytest.raises(ValueError) as exc:
        snapshot.validate_tree(tree)
    assert "retained official document" in str(exc.value)


def test_entry70_binding_rejects_changed_mapping_version_same_content_hash(tmp_path):
    """A metadata mapping version changed from the raw mapping file (while the
    raw mapping file still says the old version) fails verification — a mapping
    change is not silently accepted under the same content-hash identity."""
    tree = fixtures.build_tree(tmp_path / "tree")
    _inject_entry70_source(tree)
    meta = json.loads((tree / "data/processed/metadata.json").read_text())
    src = meta["verified_sources"][ENTRY70_SOURCE_KIND]
    # Change ONLY the recorded metadata mapping version; the raw mapping file
    # still records version 1 and the content hash is recomputed over the same
    # substances/sources internal hashes (so the hash itself is unchanged unless
    # the binding is enforced).
    src["mapping"]["mapping_version"] = 999
    src["mapping_content_hash"] = mapping_content_hash(src["mapping"])
    (tree / "data/processed/metadata.json").write_text(json.dumps(meta, indent=2))
    with pytest.raises(ValueError) as exc:
        snapshot.validate_tree(tree)
    assert "mapping version" in str(exc.value)


def _write_real_entry70_raw(tree):
    """Write a real-document entry70 raw source (matching OJ hash bytes would
    require the retained PDF; for the unit test we write the SAME retained bytes
    referenced by the module by copying the amending act from .local-reference if
    present, else a deterministic stand-in that is internally consistent)."""
    import shutil
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE
    src_dir = tree / "data/raw" / ENTRY70_SOURCE_KIND
    src_dir.mkdir(parents=True, exist_ok=True)
    real = Path(ROOT) / ".local-reference" / "independent-annex-xvii-review-2026-10-13" \
        / "official-ministry-copy-2024-1328.pdf"
    if real.is_file():
        shutil.copy2(real, src_dir / ENTRY70_DOCUMENT_FILE)
    else:
        (src_dir / ENTRY70_DOCUMENT_FILE).write_bytes(
            b"\x00" * 8)  # stand-in; internal consistency is what this test asserts
    doc_sha = hashlib.sha256((src_dir / ENTRY70_DOCUMENT_FILE).read_bytes()).hexdigest()
    mapping = {
        "mapping_version": ENTRY70_SOURCE["mapping_version"],
        "document_sha256": doc_sha,
        "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
        "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
    }
    (src_dir / ENTRY70_MAPPING_FILE).write_text(json.dumps(mapping, indent=2))
    return doc_sha, mapping


def test_normalize_rebuild_preserves_active_mapping(tmp_path):
    """A real raw-to-processed rebuild of an entry70 source reconstructs the
    mapping contract, so the snapshot stays active with a reproducible identity."""
    import normalize as _norm
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE
    tree = fixtures.build_tree(tmp_path / "tree")
    doc_sha, mapping = _write_real_entry70_raw(tree)
    raw_dir = tree / "data/raw"
    manifest = json.loads((raw_dir / "fetch_manifest.json").read_text())
    manifest[ENTRY70_SOURCE_KIND] = {
        "fetched": True, "accepted": True, "filename": ENTRY70_MAPPING_FILE,
        "sha256": hashlib.sha256((raw_dir / ENTRY70_SOURCE_KIND / ENTRY70_MAPPING_FILE)
                                 .read_bytes()).hexdigest(),
        "byte_len": (raw_dir / ENTRY70_SOURCE_KIND / ENTRY70_MAPPING_FILE).stat().st_size,
        "retrieved_at": "2024-05-18T00:00:00+00:00",
    }
    (raw_dir / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2))
    meta = _norm.build_processed(inputs_dir=raw_dir, output_dir=tree / "data/processed")
    vs = meta["verified_sources"].get(ENTRY70_SOURCE_KIND)
    assert vs and vs["source_is_active"] is True
    assert vs["mapping"]["mapping_version"] == ENTRY70_SOURCE["mapping_version"]
    assert vs["mapping_content_hash"] == mapping_content_hash(vs["mapping"])
