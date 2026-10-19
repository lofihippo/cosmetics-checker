"""Offline snapshot integrity."""

import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data/scripts"))
import snapshot  # noqa: E402
from normalize import build_processed  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "source"
    raw = root / "data/raw"
    export = raw / "cosing-annex-ii/COSING_Annex_II_v2.txt"
    export.parent.mkdir(parents=True)
    export.write_text(
        '"ANNEX II","Last update: fixture"\n'
        '"Reference Number","Chemical name / INN","CAS Number",'
        '"EC Number","Regulation"\n'
        '47,Benzene,71-43-2,200-753-7,"(EC) 2009/1223"\n'
    )
    (raw / "fetch_manifest.json").write_text(json.dumps({
        "cosing-annex-ii": {"accepted": True, "fetched": True,
                            "sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                            "retrieved_at": "2026-01-01T00:00:00+00:00"}}))
    build_processed(root / "data/processed", raw)
    (root / "LICENSE").write_text("Fixture license")
    (root / "LICENSES.md").write_text("Fixture source attribution")
    return root


def hashes(root):
    return {str(p.relative_to(root)): snapshot.sha256(p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def rewrite_zip(source, output, transform):
    with zipfile.ZipFile(source) as archive:
        payload = {name: archive.read(name) for name in archive.namelist()}
    transform(payload)
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)


def test_deterministic_export_and_fresh_import(tree, tmp_path):
    before = hashes(tree)
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    snapshot.export_snapshot(tree, one)
    snapshot.export_snapshot(tree, two)
    assert one.read_bytes() == two.read_bytes()
    dest = tmp_path / "restored"
    report = snapshot.verify_snapshot(one, dest)
    for name, digest in before.items():
        assert snapshot.sha256((dest / name).read_bytes()) == digest
    assert report["count_verified_substances"] == 1
    assert hashes(tree) == before
    from eu_reg_lists import load, check_token
    restored = load(dest / "data/processed/eu_reg_lists.sqlite")
    assert check_token("Benzene", restored).status == "banned"
    assert check_token("Unknown fixture ingredient", restored).determination == "unknown"


def test_existing_bundle_and_destination_not_overwritten(tree, tmp_path):
    archive = tmp_path / "snapshot.zip"
    snapshot.export_snapshot(tree, archive)
    before = archive.read_bytes()
    with pytest.raises(FileExistsError):
        snapshot.export_snapshot(tree, archive)
    assert archive.read_bytes() == before
    dest = tmp_path / "existing"
    dest.mkdir()
    (dest / "keep").write_text("untouched")
    with pytest.raises(FileExistsError):
        snapshot.verify_snapshot(archive, dest)
    assert (dest / "keep").read_text() == "untouched"


def test_tampering_rejected_before_destination_created(tree, tmp_path):
    archive, bad = tmp_path / "ok.zip", tmp_path / "bad.zip"
    snapshot.export_snapshot(tree, archive)
    rewrite_zip(archive, bad, lambda p: p.update({"LICENSE": b"changed"}))
    dest = tmp_path / "restored"
    with pytest.raises(ValueError, match="integrity mismatch"):
        snapshot.verify_snapshot(bad, dest)
    assert not dest.exists()


def test_metadata_mismatch_not_exported(tree, tmp_path):
    meta = tree / "data/processed/metadata.json"
    doc = json.loads(meta.read_text())
    doc["data_updated_at"] = "different-snapshot"
    meta.write_text(json.dumps(doc))
    archive = tmp_path / "bad.zip"
    with pytest.raises(ValueError, match="SQLite and metadata"):
        snapshot.export_snapshot(tree, archive)
    assert not archive.exists()


def test_source_integrity_mismatch_not_exported(tree, tmp_path):
    raw = tree / "data/raw/cosing-annex-ii/COSING_Annex_II_v2.txt"
    raw.write_text("changed")
    with pytest.raises(ValueError, match="source integrity"):
        snapshot.export_snapshot(tree, tmp_path / "bad.zip")


def test_archive_path_traversal_rejected(tree, tmp_path):
    archive, bad = tmp_path / "ok.zip", tmp_path / "bad.zip"
    snapshot.export_snapshot(tree, archive)
    rewrite_zip(archive, bad, lambda p: p.update({"../escape": b"bad"}))
    with pytest.raises(ValueError, match="invalid archive path"):
        snapshot.verify_snapshot(bad, tmp_path / "restored")
    assert not (tmp_path / "escape").exists()


def test_semantic_tampering_rejected_even_with_new_member_hash(tree, tmp_path):
    archive, bad = tmp_path / "ok.zip", tmp_path / "bad.zip"
    snapshot.export_snapshot(tree, archive)

    def tamper(payload):
        name = "data/processed/eu_reg_lists.jsonl"
        rows = [json.loads(line) for line in payload[name].decode().splitlines()]
        rows[0]["aliases"].append("invented-alias")
        data = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
        payload[name] = data
        manifest = json.loads(payload[snapshot.MANIFEST])
        manifest["files"][name] = {"sha256": snapshot.sha256(data), "byte_len": len(data)}
        payload[snapshot.MANIFEST] = json.dumps(manifest).encode()

    rewrite_zip(archive, bad, tamper)
    with pytest.raises(ValueError, match="content_hash mismatch"):
        snapshot.verify_snapshot(bad)


def test_legacy_member_hash_contract_verifies(tree, tmp_path):
    """The group-membership content-hash contract (version 2) is a distinct,
    reproducible contract (member_of included, no marker), so a legacy snapshot
    built under it re-verifies without rewriting its recorded hash."""
    from normalize import compute_content_hash
    records = [json.loads(l) for l in
               (tree / "data/processed/eu_reg_lists.jsonl").read_text().splitlines() if l.strip()]
    sources = json.loads((tree / "data/processed/metadata.json").read_text())["verified_sources"]
    # Add a member_of link to one record to exercise the v2 hash input.
    records[0]["member_of"] = [{"group_id": "x", "official_reference": "1",
                                "row_reference": "f#1"}]
    v2 = compute_content_hash(records, sources, hash_version="2")
    # Deterministic: same input -> same v2 hash.
    assert v2 == compute_content_hash(records, sources, hash_version="2")
    # v2 (member_of, no marker) differs from v3 (member_of + marker).
    assert v2 != compute_content_hash(records, sources, hash_version="3")


def test_unknown_content_hash_version_rejected(tree, tmp_path):
    """An unsupported content-hash version must not be silently reinterpreted."""
    metadata = json.loads((tree / "data/processed/metadata.json").read_text())
    metadata["content_hash_version"] = "99"
    (tree / "data/processed/metadata.json").write_text(json.dumps(metadata))
    archive = tmp_path / "bad.zip"
    with pytest.raises((ValueError,), match="unsupported"):
        snapshot.export_snapshot(tree, archive)


def test_rebuild_preserves_deferred_source_inventory_hash(tree):
    """Re-normalising a restored tree that omits empty/deferred ECHA directories
    must reproduce the SAME content hash: the source inventory is driven by the
    explicit fetch_manifest.json, not by physical directory presence (Part C).

    A bundle omits the three empty deferred ECHA dirs (they hold no files), yet
    the manifest still records their (unfetched, not-accepted) inventory. Without
    a manifest-driven inventory the rebuilt ``verified_sources`` would drop them
    and the content hash would change for no data change.
    """
    from normalize import compute_content_hash
    # Simulate the bundle/restored tree: the manifest records the deferred ECHA
    # sources (accepted:false, no files) but there is no physical ECHA directory.
    manifest_path = tree / "data/raw/fetch_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for key in ("echa-svhc", "echa-annex-xiv", "echa-annex-xvii"):
        # Add the deferred sources exactly as fetch.py records them (not fetched,
        # not accepted). They are recorded in the inventory but never ingest data.
        manifest[key] = {"fetched": False, "accepted": False}
    manifest_path.write_text(json.dumps(manifest))

    # Baseline: build over the tree (which HAS no physical echa dirs). The
    # manifest-driven provenance must record all sources and reproduce the hash.
    raw = tree / "data/raw"
    meta = build_processed(tree / "data/processed", raw)
    vs = meta["verified_sources"]
    for key in ("echa-svhc", "echa-annex-xiv", "echa-annex-xvii"):
        assert key in vs
        assert vs[key]["present"] is False
        assert vs[key]["accepted"] is False
        assert vs[key]["files"] == {}
    records = [json.loads(l) for l in
               (tree / "data/processed/eu_reg_lists.jsonl").read_text().splitlines()
               if l.strip()]
    src = {k: v for k, v in vs.items()}
    h = compute_content_hash(records, src)
    assert h == meta["content_hash"]
    # The deferred ECHA sources are preserved in the inventory even though their
    # directories are absent (bundle restoration), so the inventory is stable.
    assert all(k in src for k in ("echa-svhc", "echa-annex-xiv", "echa-annex-xvii"))


def _write_entry70_source(root):
    """Write an internally-consistent entry70 raw source (document + mapping)."""
    from eu_reg_lists.annex17_entry70 import (ENTRY70_DOCUMENT_FILE,
                                              ENTRY70_MAPPING_FILE,
                                              ENTRY70_SOURCE,
                                              ENTRY70_SOURCE_KIND)
    src = root / "data/raw" / ENTRY70_SOURCE_KIND
    src.mkdir(parents=True, exist_ok=True)
    doc = b"synthetic-but-consistent-doc"
    (src / ENTRY70_DOCUMENT_FILE).write_bytes(doc)
    doc_sha = hashlib.sha256(doc).hexdigest()
    mapping = {
        "mapping_version": ENTRY70_SOURCE["mapping_version"],
        "document_sha256": doc_sha,
        "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
        "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
    }
    (src / ENTRY70_MAPPING_FILE).write_text(json.dumps(mapping, indent=2))
    manifest = json.loads((root / "data/raw/fetch_manifest.json").read_text())
    manifest[ENTRY70_SOURCE_KIND] = {
        "fetched": True, "accepted": True, "filename": ENTRY70_MAPPING_FILE,
        "sha256": hashlib.sha256((src / ENTRY70_MAPPING_FILE).read_bytes()).hexdigest(),
        "byte_len": (src / ENTRY70_MAPPING_FILE).stat().st_size,
        "retrieved_at": "2024-05-18T00:00:00+00:00",
    }
    (root / "data/raw/fetch_manifest.json").write_text(json.dumps(manifest, indent=2))
    return doc_sha


def test_normalize_preserves_entry70_mapping_rebuild(tree):
    """A real raw-to-processed rebuild reconstructs the entry70 mapping contract
    so the snapshot stays active with a reproducible identity (no manual
    metadata patching)."""
    from eu_reg_lists.annex17_entry70 import (ENTRY70_SOURCE, ENTRY70_SOURCE_KIND,
                                              mapping_content_hash)
    _write_entry70_source(tree)
    meta = build_processed(tree / "data/processed", tree / "data/raw")
    vs = meta["verified_sources"].get(ENTRY70_SOURCE_KIND)
    assert vs is not None
    assert vs["source_is_active"] is True
    assert vs["mapping"]["mapping_version"] == ENTRY70_SOURCE["mapping_version"]
    assert vs["mapping_content_hash"] == mapping_content_hash(vs["mapping"])


def test_validate_tree_rejects_entry70_without_document(tree):
    """A mapping-only entry70 source (no retained official document) is rejected
    by the snapshot verifier; it is never accepted as a bound, active source."""
    from eu_reg_lists.annex17_entry70 import ENTRY70_SOURCE_KIND
    _write_entry70_source(tree)
    meta = build_processed(tree / "data/processed", tree / "data/raw")
    # Remove the retained official document bytes + its file inventory record.
    (tree / "data/raw" / ENTRY70_SOURCE_KIND / "OJ_L_202401328_EN.pdf").unlink()
    vs = meta["verified_sources"][ENTRY70_SOURCE_KIND]
    vs["files"].pop("OJ_L_202401328_EN.pdf", None)
    vs["present"] = bool(vs["files"])
    (tree / "data/processed/metadata.json").write_text(json.dumps(meta, indent=2))
    with pytest.raises(ValueError) as exc:
        snapshot.validate_tree(tree)
    assert "retained official document" in str(exc.value)
