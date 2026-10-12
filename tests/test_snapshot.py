"""Offline snapshot integrity. Regulatory information, not legal advice."""

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
