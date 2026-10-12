"""Isolated unattended snapshot restore/refresh/validate/bundle regressions.

Regulatory information, not legal advice.

These exercise the REAL maintained orchestration script
(``data/scripts/refresh_isolated.py``) end to end against small, clearly-marked
fixtures built from an isolated copy of the accepted real bundle. No live
regulatory fetch occurs; synthetic update rows are test fixtures only and are
NEVER promoted into live verified data.

Scenarios covered:
  - fresh work dir -> trusted baseline restore (missing/corrupt baseline fails)
  - no-change selects the IMMUTABLE baseline (metadata AND SQLite freshness kept)
  - unavailable / wrong-annex source refresh preserves baseline coverage/time
  - controlled changed export -> full candidate checks, rule-level reviewable
    diff, verified consumer bundle + digest that re-imports and checks
  - one failed source and one changed source -> failed source preserved
  - a lost rule with unchanged identity counts -> review_required
  - corrupt matching-digest bundle -> operational exit 2, no publish-ready artifact
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "data" / "scripts" / "refresh_isolated.py"
SNAP = ROOT / "data" / "scripts" / "snapshot.py"
CANDIDATE = "candidate"
PY = str((ROOT / ".venv" / "bin" / "python")) if (ROOT / ".venv" / "bin" / "python").exists() else "python3"


def _env():
    return {**os.environ, "PYTHONPATH": str(ROOT / "packages/eu-reg-lists")}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _meta_timestamp(tree: Path) -> str:
    return json.loads((tree / "data/processed/metadata.json").read_text())["data_updated_at"]


def _sqlite_ts(tree: Path) -> str:
    import sqlite3
    con = sqlite3.connect(str(tree / "data/processed/eu_reg_lists.sqlite"))
    try:
        row = con.execute(
            "SELECT v FROM metadata WHERE k='data_updated_at'").fetchone()
        return json.loads(row[0]) if row else ""
    finally:
        con.close()


@pytest.fixture(scope="module")
def baseline_bundle(tmp_path_factory):
    """Export the real accepted repo snapshot to a disposable bundle."""
    out = tmp_path_factory.mktemp("baseline") / "snapshot-bundle.zip"
    subprocess.run([PY, str(SNAP), "export", "--root", str(ROOT),
                    "--output", str(out)],
                   check=True, capture_output=True, text=True, env=_env())
    yield out, _sha256(out)


def _make_annex_ii_fixture(tmp_path: Path, add_row: bool) -> Path:
    """Copy the real Annex II export and (optionally) append a clearly marked row."""
    src = ROOT / "data" / "raw" / "cosing-annex-ii" / "COSING_Annex_II_v2.txt"
    dst = tmp_path / "COSING_Annex_II_v2.txt"
    shutil.copy2(src, dst)
    if add_row:
        with dst.open("a", encoding="utf-8") as fh:
            fh.write('1768,"Fixture Test Substance, Annex II",0000-00-0,,'
                     '"(EC) 2009/1223",,,,,,01/01/2026\n')
    return dst


def _make_wrong_annex_fixture(tmp_path: Path) -> Path:
    """Annex II content staged under an Annex III key triggers the expected
    source-validation fallback (coverage preserved)."""
    src = ROOT / "data" / "raw" / "cosing-annex-ii" / "COSING_Annex_II_v2.txt"
    dst = tmp_path / "wrong_annex_for_iii.txt"
    shutil.copy2(src, dst)
    return dst


def _make_annex_iii_fixture(tmp_path: Path, remove_ref: str) -> Path:
    """Copy the real Annex III export, removing the row whose Reference Number
    equals ``remove_ref`` (e.g. '45' = Benzyl alcohol), leaving other lists intact."""
    src = ROOT / "data" / "raw" / "cosing-annex-iii" / "COSING_Annex_III_v2.txt"
    dst = tmp_path / "COSING_Annex_III_v2.txt"
    lines = src.read_text().splitlines(keepends=True)
    kept = []
    removed = 0
    for line in lines:
        # Data rows: first field is the Reference Number (no surrounding quotes).
        if line.lstrip().startswith(f'{remove_ref},'):
            removed += 1
            continue
        kept.append(line)
    dst.write_text("".join(kept))
    assert removed >= 1, f"ref {remove_ref} not found in Annex III fixture"
    return dst


def _run(work: Path, baseline: Path, digest: str, *extra: str, out: Path | None = None):
    args = [PY, str(SCRIPT), "--work", str(work),
            "--baseline-url", f"file://{baseline}",
            "--baseline-sha256", digest, *extra]
    if out is not None:
        args += ["--out", str(out)]
    return subprocess.run(args, capture_output=True, text=True, timeout=600,
                          env=_env(), check=False)


def test_fresh_restore_then_no_change_preserves_freshness(baseline_bundle, tmp_path):
    """No-change SELECTS the immutable baseline; metadata AND SQLite freshness.
    The baseline bytes/timestamp are not rewritten by the candidate refresh."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    res = _run(work, baseline, digest)
    assert res.returncode == 0, res.stderr
    assert "[selected] baseline" in res.stdout
    assert "[no-change]" in res.stdout
    tree = work / "baseline"
    assert tree.exists()
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "no-change"
    assert result["selected_snapshot_label"] == "baseline"
    assert result["candidate_content_hash"] == result["baseline_content_hash"]
    # Both the metadata and SQLite timestamps equal the accepted baseline value.
    assert _meta_timestamp(tree) == "2026-10-10T02:31:39.763031+00:00"
    assert _sqlite_ts(tree) == "2026-10-10T02:31:39.763031+00:00"
    # No distribution bundle / review manifest was produced for no-change.
    assert not (work / "snapshot-bundle.zip").exists()
    assert not (work / "candidate-bundle.zip").exists()
    # A bounded attempt audit is retained.
    assert "attempt_summary" in result


def test_missing_or_corrupt_baseline_fails_clearly(baseline_bundle, tmp_path):
    baseline, _digest = baseline_bundle
    work = tmp_path / "work"
    res = _run(work, tmp_path / "missing.zip", _digest, "--restore-only")
    assert res.returncode == 2
    assert "refresh failed" in res.stderr
    # Wrong digest.
    work2 = tmp_path / "work2"
    res2 = _run(work2, baseline, "0" * 64, "--restore-only")
    assert res2.returncode == 2
    assert "digest mismatch" in res2.stderr
    # No publish-ready result was created.
    assert not (work2 / "refresh-result.json").exists()


def test_corrupt_baseline_matching_digest_exits_2(baseline_bundle, tmp_path):
    """A corrupt ZIP whose bytes match the trusted SHA-256 (BadZipFile) must
    return operational exit 2, no traceback, no publish-ready artifact."""
    baseline, _ = baseline_bundle
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip at all")
    corrupt_digest = _sha256(corrupt)
    work = tmp_path / "work"
    res = _run(work, corrupt, corrupt_digest)
    assert res.returncode == 2, res.stdout
    assert "Traceback" not in res.stderr
    assert "refresh failed" in res.stderr
    assert not (work / "refresh-result.json").exists()
    assert not (work / CANDIDATE).exists()


def test_unavailable_source_preserves_coverage(baseline_bundle, tmp_path):
    """A source that refreshes to an unparseable/wrong-annex candidate keeps its
    last accepted coverage and the baseline snapshot timestamp."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    fixture = _make_wrong_annex_fixture(tmp_path)
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-iii={fixture}")
    assert res.returncode == 0, res.stderr
    assert "[no-change]" in res.stdout
    tree = work / "baseline"
    # The accepted Annex III file hash is the preserved baseline one.
    accepted = json.loads((tree / "data/raw/fetch_manifest.json").read_text())
    assert accepted["cosing-annex-iii"]["accepted"] is True
    ii_iii = tree / "data/raw/cosing-annex-iii/COSING_Annex_III_v2.txt"
    assert _sha256(ii_iii) == _sha256(ROOT / "data/raw/cosing-annex-iii/COSING_Annex_III_v2.txt")


def test_controlled_changed_export_produces_verified_bundle(baseline_bundle, tmp_path):
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    fixture = _make_annex_ii_fixture(tmp_path, add_row=True)
    res = _run(work, baseline, digest, "--source-file",
               f"cosing-annex-ii={fixture}", out=out)
    assert res.returncode == 0, res.stderr
    assert "[diff]" in res.stdout
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "changed"
    assert result["selected_snapshot_label"] == "candidate"
    assert "cosing-annex-ii" in result["diff"]["per_source"]
    assert result["diff"]["per_source"]["cosing-annex-ii"]["added"]
    assert not result["diff"]["review_required"]
    assert result["promotion_ready"] is True
    bundle = out / "snapshot-bundle.zip"
    assert bundle.exists()
    # The recorded digest matches the bundle.
    digest_rec = (out / "snapshot-bundle.zip.sha256").read_text().split()[0]
    assert digest_rec == _sha256(bundle)
    # The produced bundle re-verifies and re-imports cleanly.
    verify = subprocess.run([PY, str(SNAP), "verify", str(bundle)],
                            capture_output=True, text=True, timeout=300,
                            env=_env(), check=False)
    assert verify.returncode == 0, verify.stderr
    rep = json.loads(verify.stdout)
    assert rep["count_substances"] == 2376
    assert rep["count_verified_substances"] == 2299
    # A review manifest is shipped with the artifact.
    assert (out / "review-manifest.json").exists()
    rv = json.loads((out / "review-manifest.json").read_text())
    assert rv["status"] == "changed"
    assert "diff" in rv and "attempt_summary" in rv


def test_mixed_failed_and_changed_source_preserves_failed(baseline_bundle, tmp_path):
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    changed = _make_annex_ii_fixture(tmp_path, add_row=True)
    failed = _make_wrong_annex_fixture(tmp_path)
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-ii={changed}",
               "--source-file", f"cosing-annex-iii={failed}",
               out=out)
    assert res.returncode == 0, res.stderr
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "changed"
    assert "cosing-annex-ii" in result["diff"]["per_source"]
    # The failed source's accepted file is unchanged (preserved baseline bytes).
    f = work / "baseline/data/raw/cosing-annex-iii/COSING_Annex_III_v2.txt"
    assert _sha256(f) == _sha256(ROOT / "data/raw/cosing-annex-iii/COSING_Annex_III_v2.txt")


def test_lost_rule_with_unchanged_identity_counts_marks_review(baseline_bundle, tmp_path):
    """Removing a single Annex III restriction (ref 45, Benzyl alcohol) keeps
    identity counts unchanged but must mark the candidate review_required."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    fixture = _make_annex_iii_fixture(tmp_path, remove_ref="45")
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-iii={fixture}", out=out)
    assert res.returncode == 0, res.stderr
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "changed"
    diff = result["diff"]
    # Identity counts unchanged (the rule was on an already-known substance).
    assert diff["count_substances_candidate"] == diff["count_substances_baseline"]
    assert diff["count_verified_candidate"] == diff["count_verified_baseline"]
    # The removed rule IS reported per-source and marks review_required.
    gone = diff["per_source"]["cosing-annex-iii"]["removed"]
    assert any("45" == r["official_reference"] for r in gone)
    assert diff["review_required"] is True
    assert result["promotion_ready"] is False
    # A corrupt/risky candidate still exports a verifiable bundle but flags review.
    assert (out / "snapshot-bundle.zip").exists()


def test_invalid_candidate_leaves_repo_baseline_untouched(baseline_bundle, tmp_path):
    """A run that fails (e.g. bad baseline digest) never produces a bundle and
    never writes into the live checkout."""
    baseline, _ = baseline_bundle
    work = tmp_path / "work"
    before = {
        p.name: _sha256(p) for p in (ROOT / "data/processed").glob("*") if p.is_file()
    }
    res = _run(work, baseline, "0" * 64)
    assert res.returncode == 2
    after = {
        p.name: _sha256(p) for p in (ROOT / "data/processed").glob("*") if p.is_file()
    }
    assert before == after


def test_export_failure_leaves_baseline_and_exits_2(baseline_bundle, tmp_path, monkeypatch):
    """A bundling/export failure on a valid changed candidate returns exit 2 and
    leaves the baseline tree AND live checkout untouched (no promotion-ready
    artifact)."""
    sys.path.insert(0, str(ROOT / "data/scripts"))
    import refresh_isolated as ri

    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    fixture = _make_annex_ii_fixture(tmp_path, add_row=True)
    # make the output dir a FILE so export_snapshot's mkdir/write fails.
    out.write_text("not a directory")
    argv = ["--work", str(work), "--baseline-url", f"file://{baseline}",
            "--baseline-sha256", digest,
            "--source-file", f"cosing-annex-ii={fixture}", "--out", str(out)]
    rc = ri.main(argv)
    assert rc == 2
    assert not (work / "candidate/snapshot-bundle.zip").exists()
    # Live checkout unchanged.
    assert (ROOT / "data/processed/metadata.json").exists()


def test_build_diff_surfaces_lost_accepted_source(tmp_path):
    """build_diff must surface a lost ACCEPTED source for review, not silently
    treat it as clean, and uses accepted coverage not just dictionary presence."""
    sys.path.insert(0, str(ROOT / "data/scripts"))
    import refresh_isolated as ri

    def meta(sub_total, ver_total, sources, accepted):
        return {
            "content_hash": "h",
            "count_substances": sub_total,
            "count_verified_rules": ver_total,
            "verified_sources": {
                key: {"kind": "cosing-annex", "accepted": (key in accepted),
                      "files": {f"{key}.csv": {"sha256": "x", "byte_len": 1}}}
                for key in sources
            },
        }

    # Baseline: both sources accepted. Candidate: annex-ii STILL in inventory but
    # no longer accepted (accepted coverage lost), annex-iii removed from inventory.
    base = meta(10, 8, ["cosing-annex-ii", "cosing-annex-iii"],
                ["cosing-annex-ii", "cosing-annex-iii"])
    cand = meta(9, 7, ["cosing-annex-ii"], accepted=[])
    # Empty work trees -> no rule records (diff focuses on coverage transitions).
    basep = tmp_path / "b"; candp = tmp_path / "c"
    (basep / "data/processed").mkdir(parents=True)
    (candp / "data/processed").mkdir(parents=True)
    diff = ri.build_diff(base, cand, basep, candp)
    assert "cosing-annex-ii" in diff["accepted_sources_only_in_baseline"]
    assert any("accepted source list lost" in r and "cosing-annex-ii" in r
               for r in diff["consequential_reductions"])
    assert diff["review_required"] is True


def _write_records(tree: Path, records: list):
    p = tree / "data" / "processed"
    p.mkdir(parents=True, exist_ok=True)
    (p / "eu_reg_lists.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


def _meta(total):
    return {
        "content_hash": "h",
        "count_substances": total,
        "count_verified_rules": total,
        "count_identity_only": 0,
        "verified_sources": {
            "cosing-annex-ii": {"kind": "cosing-annex", "accepted": True,
                                "files": {"a.csv": {"sha256": "x", "byte_len": 1}}},
        },
    }


def _rule_record(ref, row, status="banned", conditions=None):
    return {
        "id": f"id-{ref}", "name": f"Substance {ref}", "cas": None, "ec": None,
        "sources": ["cosing-annex-ii"],
        "rules": [{
            "status": status, "annex": "Annex II", "regulation": "(EC) 2009/1223",
            "source_url": "https://x", "source_updated_at": None,
            "conditions": conditions, "resolution": "cosing-annex-ii", "raw": {
                "source_kind": "cosing-annex-ii", "official_reference": ref,
                "reference_number": ref, "row_reference": f"file#row{row}",
                "source_file": "a.csv", "source_sha256": "x",
            },
        }],
    }


def test_reorder_only_row_change_does_not_fabricate_removals(tmp_path):
    """Changing ONLY the file-row locators (reorder/renumber) must not produce
    removals/additions or a review-required reduction."""
    sys.path.insert(0, str(ROOT / "data/scripts"))
    import refresh_isolated as ri
    basep = tmp_path / "b"; candp = tmp_path / "c"
    _write_records(basep, [_rule_record("47", 1), _rule_record("48", 2)])
    # Same identities, same content, but row positions swapped/renumbered.
    _write_records(candp, [_rule_record("47", 99), _rule_record("48", 100)])
    diff = ri.build_diff(_meta(2), _meta(2), basep, candp)
    assert (diff["per_source"].get("cosing-annex-ii", {}) or {}).get("removed", []) == []
    assert (diff["per_source"].get("cosing-annex-ii", {}) or {}).get("added", []) == []
    assert (diff["per_source"].get("cosing-annex-ii", {}) or {}).get("changed", []) == []
    assert diff["review_required"] is False
    assert diff["rule_counts_baseline"]["cosing-annex-ii"] == 2
    assert diff["rule_counts_candidate"]["cosing-annex-ii"] == 2
    assert diff["individual_rule_counts_baseline"]["cosing-annex-ii"] == 2
    assert diff["individual_rule_counts_candidate"]["cosing-annex-ii"] == 2


def test_condition_change_is_reported_as_change_with_before_after(tmp_path):
    """A real semantic change (conditions) is reported as a per-source change with
    before/after content, and marks review_required."""
    sys.path.insert(0, str(ROOT / "data/scripts"))
    import refresh_isolated as ri
    basep = tmp_path / "b"; candp = tmp_path / "c"
    _write_records(basep, [_rule_record("47", 1, conditions="limit 1%")])
    _write_records(candp, [_rule_record("47", 1, conditions="limit 2%")])
    diff = ri.build_diff(_meta(1), _meta(1), basep, candp)
    changed = diff["per_source"]["cosing-annex-ii"]["changed"]
    assert len(changed) == 1
    assert changed[0]["official_reference"] == "47"
    assert diff["review_required"] is True
    assert len(diff["rule_changes"]) == 1
    rc = next(iter(diff["rule_changes"].values()))
    assert any("limit 1%" in c for c in rc["before_content"])
    assert any("limit 2%" in c for c in rc["after_content"])


def test_no_change_and_failure_writes_audit(baseline_bundle, tmp_path):
    """Every outcome persists a separate refresh-audit.json; a failure audit is
    never promotion-ready (it was not exported as a candidate)."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    res = _run(work, baseline, digest)
    assert res.returncode == 0, res.stderr
    audit = json.loads((work / "refresh-audit.json").read_text())
    assert audit["status"] == "no-change"
    assert audit["attempted"] is True
    assert audit["promotion_ready"] is True

    work2 = tmp_path / "work2"
    res2 = _run(work2, baseline, "0" * 64)
    assert res2.returncode == 2
    audit2 = json.loads((work2 / "refresh-audit.json").read_text())
    assert audit2["status"] == "failure"
    assert audit2["attempted"] is False
    assert audit2["promotion_ready"] is False
    assert "refresh failed" in audit2["message"]


def test_changed_candidate_writes_separate_audit(baseline_bundle, tmp_path):
    """A changed candidate exports BOTH a review-manifest and a separate concise
    audit (independent from the verified bundle)."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    fixture = _make_annex_ii_fixture(tmp_path, add_row=True)
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-ii={fixture}", out=out)
    assert res.returncode == 0, res.stderr
    audit = json.loads((out / "refresh-audit.json").read_text())
    assert audit["status"] == "changed"
    assert audit["promotion_ready"] is True
    assert "bundle_sha256" in audit
    assert (out / "snapshot-bundle.zip").exists()


# ---------------------------------------------------------------------------
# End-to-end isolated-candidate regressions for the semantic diff, rule counting
# and audit retention. These use the REAL refresh_isolated.main pipeline over a
# modified candidate, so the final promotion policy (not just a diff-helper
# probe) is exercised.
# ---------------------------------------------------------------------------
def _make_hydroquinone_annex_ii_scope_fixture(tmp_path, changed: bool) -> Path:
    """Copy the real Annex II export and, when ``changed``, alter only
    Hydroquinone's qualification/exception text in the 'Chemical name / INN'
    column (a substantive scope change). Row number / metadata is untouched.

    Operates on raw bytes and preserves the original line endings so that an
    UNMODIFIED fixture is byte-identical to the live source file (the refresh
    compares parsed content, not raw bytes, but a byte-identical unmodified
    fixture guarantees a no-change outcome)."""
    src = ROOT / "data" / "raw" / "cosing-annex-ii" / "COSING_Annex_II_v2.txt"
    dst = tmp_path / ("COSING_Annex_II_v2_scope.txt" if changed else "COSING_Annex_II_v2.txt")
    if not changed:
        # Byte-identical copy (no newline normalisation).
        shutil.copy2(src, dst)
        return dst
    out_lines = []
    src_bytes = src.read_bytes()
    for line in src_bytes.splitlines(keepends=True):
        # Hydroquinone's Annex II row: Reference Number 1339.
        if line.startswith(b"1339,"):
            inner = line.replace(
                b"with the exception of entry 14 in Annex III",
                b"with the exception of entry 14 in Annex III (as amended)")
            if b"as amended" not in inner:
                raise AssertionError("Hydroquinone scope text not found in fixture")
            line = inner
        out_lines.append(line)
    dst.write_bytes(b"".join(out_lines))
    return dst


def test_isolated_scope_qualification_change_surfaces_review(baseline_bundle, tmp_path):
    """A change to Hydroquinone's Annex II exception/qualification text (which
    lives in ``source_row['Chemical name / INN']``) MUST be surfaced as a
    per-source change under the conservative promotion policy, not silently
    dropped as a no-change."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    fixture = _make_hydroquinone_annex_ii_scope_fixture(tmp_path, changed=True)
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-ii={fixture}", out=out)
    assert res.returncode == 0, res.stderr
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "changed"
    assert result["selected_snapshot_label"] == "candidate"
    diff = result["diff"]
    changed = diff["per_source"]["cosing-annex-ii"]["changed"]
    # The changed Hydroquinone identity qualifies by official reference 1339.
    assert any("1339" == r["official_reference"] for r in changed)
    assert diff["review_required"] is True
    assert result["promotion_ready"] is False
    # The before/after content shows the qualification text difference.
    rc = next(iter(diff["rule_changes"].values()))
    assert any("entry 14 in Annex III" in c for c in rc["before_content"])
    assert any("as amended" in c for c in rc["after_content"])


def test_isolated_no_change_candidate_does_not_surface_scope_false_positive(baseline_bundle, tmp_path):
    """An unmodified candidate must NOT surface a scope change (guard against
    false positives when the qualification text is identical)."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    fixture = _make_hydroquinone_annex_ii_scope_fixture(tmp_path, changed=False)
    res = _run(work, baseline, digest,
               "--source-file", f"cosing-annex-ii={fixture}")
    assert res.returncode == 0, res.stderr
    result = json.loads((work / "refresh-result.json").read_text())
    assert result["status"] == "no-change"
    assert result["selected_snapshot_label"] == "baseline"
    assert not result["diff"]["review_required"]


def test_rule_counts_identity_vs_individual_rules(tmp_path):
    """``rule_counts_*`` reports distinct rule IDENTITIES while
    ``individual_rule_counts_*`` reports the true individual rule count (distinct
    rules can share an identity)."""
    import refresh_isolated as ri
    basep = tmp_path / "b"; candp = tmp_path / "c"
    # One identity carrying TWO distinct rules (same source+ref+substance).
    rec_two = {
        "id": "id-47", "name": "Substance 47", "cas": None, "ec": None,
        "sources": ["cosing-annex-ii"],
        "rules": [
            {"status": "banned", "annex": "Annex II", "regulation": "(EC) 2009/1223",
             "source_url": "https://x", "source_updated_at": None,
             "conditions": None, "resolution": "cosing-annex-ii", "raw": {
                 "source_kind": "cosing-annex-ii", "official_reference": "47",
                 "reference_number": "47", "row_reference": "file#row1",
                 "source_file": "a.csv", "source_sha256": "x"}},
            {"status": "restricted", "annex": "Annex II", "regulation": "(EC) 2009/1223",
             "source_url": "https://x", "source_updated_at": None,
             "conditions": "max 1%", "resolution": "cosing-annex-ii", "raw": {
                 "source_kind": "cosing-annex-ii", "official_reference": "47",
                 "reference_number": "47", "row_reference": "file#row2",
                 "source_file": "a.csv", "source_sha256": "x"}},
        ],
    }
    _write_records(basep, [rec_two])
    # Candidate identical.
    _write_records(candp, [rec_two])
    diff = ri.build_diff(_meta(1), _meta(1), basep, candp)
    # One identity group, but TWO individual rules.
    assert diff["rule_counts_baseline"]["cosing-annex-ii"] == 1
    assert diff["individual_rule_counts_baseline"]["cosing-annex-ii"] == 2
    assert diff["individual_rule_counts_candidate"]["cosing-annex-ii"] == 2
    assert not diff["review_required"]  # unchanged, no fabricated reduction
