"""Tests for the official-export parsers + reconciliation used by normalize.

These use isolated tmp_path fixtures and NEVER touch the real data/raw or
data/processed (per the prior review's concern about tests rewriting the
workspace dataset).
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "data" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cosing_parser import parse_cosing_csv, parse_export_file  # noqa: E402
from echa_parser import parse_echa_csv, parse_echa_file  # noqa: E402


def _write_export(inputs_dir: Path, subdir: str, filename: str, content: str,
                  accepted: bool = True) -> Path:
    """Write an export file + a manifest entry marking it accepted/rejected.

    Manually-supplied exports must carry an explicit validation manifest; the
    normalize pipeline only parses files whose manifest says ``accepted`` and
    whose hash matches. This mirrors the real fetch manifest.
    """
    d = inputs_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_text(content)
    manifest_path = inputs_dir / "fetch_manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    manifest[subdir] = {
        "fetched": True, "accepted": accepted,
        "filename": filename,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return f


def _cosing_export(annex: str, rows: List[str]) -> str:
    """Build a real-schema CosIng annex export (annex in the preamble)."""
    header = ('"Reference Number","Chemical name / INN","CAS Number","EC Number",'
              'Regulation,"Other Directives/Regulations","SCCS opinions",'
              '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
              'CMR,"Update Date"')
    return ('"File creation date: 08/10/2026"\n'
            f'"ANNEX {annex}","Last update: 06/10/2026"\n'
            '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
            '"Substance identification"\n'
            f'{header}\n' + "\n".join(rows) + "\n")


def _cosing_export_iii(rows: List[str]) -> str:
    """Build a real Annex III export (restriction/condition columns present)."""
    header = ('"Reference Number","Chemical name / INN","Name of Common Ingredients Glossary",'
              '"CAS Number","EC Number","Product Type, body parts",'
              '"Maximum concentration in ready for use preparation","Other",'
              '"Wording of conditions of use and warnings","Regulation",'
              '"Other Directives/Regulations","SCCS opinions","Chemical/IUPAC Name",'
              '"Identified INGREDIENTS or substances e.g.","CMR","Update Date"')
    return ('"File creation date: 08/10/2026"\n'
            '"ANNEX III","Last update: 06/10/2026"\n'
            '"LIST OF SUBSTANCES WHICH COSMETIC PRODUCTS MUST NOT CONTAIN EXCEPT SUBJECT TO THE RESTRICTIONS LAID DOWN"\n'
            '"Substance identification",Restrictions\n'
            f'{header}\n' + "\n".join(rows) + "\n")


# ---------------------------------------------------------------------------
# CosIng parser.
# ---------------------------------------------------------------------------
def test_cosing_parses_verified_records_from_annex():
    sample = _cosing_export("II", [
        '1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010',
        '2,"Red 40",25956-17-6,247-368-0,"(EC) 2009/1223",,,,,,15/10/2010',
    ])
    recs = parse_cosing_csv(sample, "https://source")
    by_name = {r["name"]: r for r in recs}
    assert by_name["Zinc pyrithione"]["status"] == "banned"
    assert by_name["Zinc pyrithione"]["annex"] == "Annex II"
    assert by_name["Zinc pyrithione"]["resolution"].startswith("cosing")
    # In the real file every row in Annex II is a prohibited substance; a row is
    # not independently assigned a per-row status, so Red 40 in Annex II is banned.
    assert by_name["Red 40"]["status"] == "banned"
    assert by_name["Red 40"]["annex"] == "Annex II"


def test_cosing_skips_rows_without_recognized_annex():
    # In the real schema the annex comes from the preamble; no annex -> no rules.
    sample = ('"Reference Number","Chemical name / INN","CAS Number","EC Number",'
              'Regulation,"Other Directives/Regulations","SCCS opinions",'
              '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
              'CMR,"Update Date"\n'
              '1,"Something Unknown",1-1-1,,, ,,,,,15/10/2010\n')
    assert parse_cosing_csv(sample, "https://source") == []
    # A declaration with an unrecognised annex also produces no rules.
    assert parse_cosing_csv(_cosing_export("XI", ['1,"X",1-1-1,,, ,,,,']),
                            "https://source") == []


def test_cosing_export_file_dash_identifiers_are_absent(tmp_path):
    """The real export uses ``-`` for missing CAS/EC; those rows must still parse
    (name only) and must NOT be treated as a shared ``-`` identifier."""
    content = _cosing_export("II", [
        '1,"Alkyne alcohols, their esters, ethers and salts",-,-,"(EC) 2009/1223",,,,,,15/10/2010',
        '2,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010',
    ])
    f = tmp_path / "COSING_Annex_II_v2.txt"
    f.write_text(content)
    recs = parse_export_file(f, "https://source")
    by_name = {r["name"]: r for r in recs}
    # The name-only row parses even though CAS/EC are the placeholder '-'
    # (the parser passes the raw value; reconcile treats '-' as absent).
    assert by_name["Alkyne alcohols, their esters, ethers and salts"]["cas"] == "-"
    assert by_name["Alkyne alcohols, their esters, ethers and salts"]["status"] == "banned"
    assert by_name["Zinc pyrithione"]["cas"] == "13463-41-7"


def test_cosing_annex_from_dir_key_is_authoritative(tmp_path):
    """A source-dir annex that contradicts the export's self-declared annex is
    REJECTED (the declaration wins); we never misclassify a row's regime."""
    import pytest
    import normalize
    content = _cosing_export("III", ['1,"Boric acid",10043-35-3,233-139-2,"(EC) 2009/1223",,,,,,15/10/2010'])
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt", content)
    with pytest.raises(normalize.AnnexMismatchError):
        _build_report(tmp_path)
    # The parser itself raises the explicit, expected mismatch error.
    with pytest.raises(normalize.AnnexMismatchError):
        parse_export_file(tmp_path / "cosing-annex-ii" / "COSING_Annex_II_v2.txt",
                          "https://source", annex="ii")


# ---------------------------------------------------------------------------
# Chapter IV — Annex III restriction parsing (conditions preserved).
# ---------------------------------------------------------------------------
def test_cosing_iii_preserves_conditions_multiline_and_grouped():
    """Annex III rows carry product types, concentration limits, other
    restrictions, and warnings as separate, ordered, verbatim fragments."""
    content = _cosing_export_iii([
        ('2a,"Thioglycolic acid and its salts","THIOGLYCOLIC ACID",68-11-1,200-677-4,'
         '"(a) Hair products\n\n(b) Depilatories",'
         '"(a) (i) 8%    (ii) 11% \n\n(b) 5%",'
         '"(a) (i) General use ready for use pH 7 to 9.5",'
         '"Conditions of use:\n(a) (b) Avoid contact with eyes",'
         '"(EU) 2015/1190",,,"Thioglycolic acid and its salts",,,,,27/07/2020'),
    ])
    recs = parse_cosing_csv(content, "https://source", annex="iii")
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == "restricted"
    assert r["annex"] == "Annex III"
    assert r["resolution"].startswith("cosing")
    # reference number is alphanumeric and preserved as text.
    assert r["reference_number"] == "2a"
    # compact summary string (backward-compatible).
    assert "Maximum concentration" in (r["conditions"] or "")
    # structured fragments.
    frags = r["conditions_list"]
    fields = [f["field"] for f in frags]
    assert fields == ["product_types", "max_concentration", "other", "warnings"]
    assert "Hair products" in frags[0]["value"]
    assert "8%" in frags[1]["value"]
    assert "pH 7 to 9.5" in frags[2]["value"]
    assert "Avoid contact with eyes" in frags[3]["value"]
    # complete source row retained (16 columns, including unrecognised ones).
    assert len(r["source_row"]) == 16


def test_cosing_iii_preserves_warning_and_non_numeric_restriction():
    """A warning / non-numeric "other" restriction is kept, not dropped."""
    content = _cosing_export_iii([
        ('4,"Ammonia","AMMONIA",7664-41-7,231-635-3,,'
         '"6% (as NH3)",,"Above 2%: Contains ammonia","(EC) 2009/1223",,,,'
         ',,,,17/10/2010'),
    ])
    recs = parse_cosing_csv(content, "https://source", annex="iii")
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == "restricted"
    fields = [f["field"] for f in r["conditions_list"]]
    assert "max_concentration" in fields and "warnings" in fields
    assert "6% (as NH3)" in r["conditions_list"][0]["value"]
    assert "Contains ammonia" in r["conditions_list"][1]["value"]


def test_cosing_iii_empty_cells_yield_no_condition_fragments():
    content = _cosing_export_iii([
        '5,"Sodium nitrite","",7632-00-0,231-555-9,,,,,,'
        '"(EC) 2009/1223",,,,',
    ])
    recs = parse_cosing_csv(content, "https://source", annex="iii")
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == "restricted"
    # no condition columns populated -> conditions/conditions_list are None.
    assert r["conditions"] is None
    assert r["conditions_list"] is None


def test_combined_annex_headline_not_downgraded(tmp_path):
    """A substance in both Annex II (banned) and Annex III (restricted) keeps a
    banned headline; the Annex III restriction is retained as a separate rule."""
    import normalize
    # Annex II export declares Benzene banned.
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", [
                      '1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010']))
    # Annex III export declares Benzene restricted (with a concentration cap).
    _write_export(tmp_path, "cosing-annex-iii", "COSING_Annex_III_v2.txt",
                  _cosing_export_iii([
                      '14,"Benzene","",71-43-2,200-753-7,,'
                      '"0.1%",,"Warnings: avoid skin contact","(EC) 2009/1223",,,,'
                      ',,,,17/10/2010']))
    meta, subs, _out = _build_report(tmp_path)
    by_name = {s["name"]: s for s in subs}
    benzene = by_name["Benzene"]
    assert benzene["determination"] == "banned", benzene["determination"]
    annexes = sorted(r["annex"] for r in benzene["rules"])
    statuses = sorted(r["status"] for r in benzene["rules"])
    assert annexes == ["Annex II", "Annex III"]
    assert statuses == ["banned", "restricted"]
    # The Annex III rule kept its conditions list.
    annex3 = [r for r in benzene["rules"] if r["annex"] == "Annex III"][0]
    assert annex3["conditions"] and "0.1%" in annex3["conditions"]
    # Both sources are present and accepted in the combined snapshot.
    assert meta["verified_sources"]["cosing-annex-iii"]["accepted"] is True


# ---------------------------------------------------------------------------
# ECHA parsers (SVHC / Annex XIV / Annex XVII schemas).
# ---------------------------------------------------------------------------
def test_echa_svhc_parser():
    sample = ("Substance name,CAS number,EC number,Reason for inclusion,"
              "Date of inclusion\n"
              "Bisphenol A,80-05-7,201-245-8,CMR,2020-01-01\n"
              "Boric acid,10043-35-3,233-139-2,Reprotoxic,2010-01-01\n")
    recs = parse_echa_csv(sample, "svhc", "https://echa")
    by_name = {r["name"]: r for r in recs}
    # SVHC Candidate List membership is listing information requiring contextual
    # review, NOT a numeric restriction or a prohibition. Status is ``listed``.
    assert by_name["Bisphenol A"]["status"] == "listed"
    assert by_name["Bisphenol A"]["annex"] == "SVHC"
    assert by_name["Bisphenol A"]["conditions"] == "CMR"
    assert by_name["Boric acid"]["annex"] == "SVHC"


def test_echa_annex_xiv_parser():
    sample = ("Substance Name,CAS number,EC number,Sunset date\n"
              "Di(2-ethylhexyl) phthalate,117-81-7,204-211-0,2025-01-01\n")
    recs = parse_echa_csv(sample, "annex-xiv", "https://echa")
    assert recs[0]["annex"] == "Annex XIV"
    assert recs[0]["status"] == "restricted"
    assert recs[0]["date_included"] == "2025-01-01"


def test_echa_annex_xvii_parser():
    sample = ("Name of substance,CAS number,EC number,Restriction\n"
              "Phenylmercuric nitrate,55-68-5,200-242-7,Prohibited in cosmetics\n")
    recs = parse_echa_csv(sample, "annex-xvii", "https://echa")
    assert recs[0]["annex"] == "Annex XVII"
    assert recs[0]["status"] == "restricted"
    assert recs[0]["conditions"] == "Prohibited in cosmetics"


def test_echa_parser_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_echa_csv("a,b\n1,2\n", "bad-kind", "https://echa")


def test_echa_export_file_parsing_tsv(tmp_path):
    sample = ("Substance name\tCAS number\tEC number\tReason for inclusion\n"
              "Formaldehyde\t50-00-0\t200-001-8\tCMR\n")
    p = tmp_path / "svhc.tsv"
    p.write_text(sample)
    recs = parse_echa_file(p, "svhc", "https://echa")
    assert recs and recs[0]["name"] == "Formaldehyde"
    # SVHC Candidate List membership is listing information, not a restriction.
    assert recs[0]["status"] == "listed"


# ---------------------------------------------------------------------------
# Fetch: honest provenance + content validation.
# ---------------------------------------------------------------------------
def test_fetch_source_reports_unavailable_honestly(tmp_path):
    import fetch
    fetch.SOURCES["test-bad"] = {
        "name": "Test unavailable", "url": "http://127.0.0.1:1/nope",
        "filename": "none.csv", "kind": "csv-export",
    }
    rep = fetch.fetch_source("test-bad", out_dir=tmp_path)
    assert rep["fetched"] is False
    assert "error" in rep


def test_fetch_source_rejects_html_error_page(tmp_path):
    import fetch
    # a source that returns an HTML error page must be rejected (not accepted).
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Error</body></html>")
        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        fetch.SOURCES["test-html"] = {
            "name": "HTML source", "url": f"http://127.0.0.1:{port}/x",
            "filename": "x.csv", "kind": "echa-svhc",
        }
        rep = fetch.fetch_source("test-html", out_dir=tmp_path)
        assert rep["accepted"] is False
        assert rep.get("error") == "ValidationError"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# Regression: a failed refresh must NOT wipe usable coverage (P1 snapshot safety).
# ---------------------------------------------------------------------------
def test_failed_refresh_preserves_verified_coverage(tmp_path):
    """Simulate a rejected refresh (fetch_source fails) and confirm the active
    accepted snapshot + verified coverage survive. The bug: persist_manifest
    overwrote the ACTIVE manifest with a rejected entry, so normalize dropped to
    zero verified substances."""
    import fetch
    import normalize

    raw = tmp_path / "raw"
    src = raw / "cosing-annex-ii"
    src.mkdir(parents=True)
    active = src / "COSING_Annex_II_v2.txt"
    content = ('"File creation date: 08/10/2026"\n'
               '"ANNEX II","Last update: 06/10/2026"\n'
               '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
               '"Substance identification"\n'
               '"Reference Number","Chemical name / INN","CAS Number","EC Number",'
               'Regulation,"Other Directives/Regulations","SCCS opinions",'
               '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
               'CMR,"Update Date"\n'
               '1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010\n')
    active.write_text(content)
    # ACTIVE manifest marks the snapshot accepted (as the real download did).
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(content.encode()),
        "byte_len": len(content), "retrieved_at": "2026-01-01T00:00:00+00:00",
    }}, raw / "fetch_manifest.json")

    baseline = normalize.build_processed(output_dir=tmp_path / "out0", inputs_dir=raw)
    assert baseline["count_verified_rules"] == 1  # the accepted snapshot works

    # Now simulate a FAILED refresh: point the source at an unreachable host.
    orig = fetch.SOURCES["cosing-annex-ii"]["url"]
    fetch.SOURCES["cosing-annex-ii"]["url"] = "http://127.0.0.1:1/nope"
    try:
        attempt = fetch.fetch_source("cosing-annex-ii", out_dir=raw)
    finally:
        fetch.SOURCES["cosing-annex-ii"]["url"] = orig
    assert attempt["accepted"] is False  # the refresh itself is rejected

    # The failed attempt is recorded separately, NOT in the active manifest.
    attempts = fetch._load_json(raw / "fetch_attempts.json")
    assert attempts["cosing-annex-ii"][-1]["accepted"] is False
    active_entry = fetch._load_json(raw / "fetch_manifest.json")["cosing-annex-ii"]
    assert active_entry["accepted"] is True  # active manifest NOT overwritten
    assert fetch._sha256(active.read_bytes()) == active_entry["sha256"]  # file intact

    # Re-normalize after the failed refresh -> verified coverage is preserved.
    after = normalize.build_processed(output_dir=tmp_path / "out1", inputs_dir=raw)
    assert after["count_verified_rules"] == 1
    assert after["content_hash"] == baseline["content_hash"]


def test_rejected_staged_annex_mismatch_falls_back_to_active_annex3(tmp_path):
    """A validated staged Annex II export configured as ``cosing-annex-iii`` must
    be REJECTED by the parser (declared-annex mismatch) but the refresh must NOT
    abort; normalize records the expected source-validation failure and falls
    back to the accepted ACTIVE Annex III snapshot."""
    import fetch
    import normalize

    raw = tmp_path / "raw"
    src = raw / "cosing-annex-iii"
    src.mkdir(parents=True)
    active = src / "COSING_Annex_III_v2.txt"
    content = _cosing_export_iii([
        '14,"Hydroquinone","HYDROQUINONE",123-31-9,204-617-8,"Artificial nail systems",'
        '"0.02% (after mixing for use)","Professional use","For professional use only",'
        '"(EC) 2013/344",,,,,,,,14/05/2013',
    ])
    active.write_text(content)
    fetch._write_json({"cosing-annex-iii": {
        "key": "cosing-annex-iii", "name": "CosIng Annex III",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_III_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(content.encode()),
        "byte_len": len(content), "retrieved_at": "2026-01-01T00:00:00+00:00",
    }}, raw / "fetch_manifest.json")

    baseline = normalize.build_processed(output_dir=tmp_path / "out0", inputs_dir=raw)
    assert baseline["count_verified_rules"] == 1
    active_entry = fetch._load_json(raw / "fetch_manifest.json")["cosing-annex-iii"]
    active_hash = active_entry["sha256"]

    # A validated staged ANNEX II export wrongly placed under cosing-annex-iii.
    staged = raw / ".incoming" / "cosing-annex-iii"
    staged.mkdir(parents=True)
    staged_file = staged / "COSING_Annex_III_v2.txt"
    staged_file.write_text(_cosing_export("II", [
        '1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']))
    fetch._write_json({"cosing-annex-iii": {
        "key": "cosing-annex-iii", "name": "CosIng Annex III",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_III_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(staged_file.read_bytes()),
        "byte_len": staged_file.stat().st_size,
        "retrieved_at": "2026-08-10T00:00:00+00:00",
    }}, raw / "fetch_staging.json")

    after = normalize.build_processed(output_dir=tmp_path / "out1", inputs_dir=raw)
    # Active Annex III coverage is preserved (NOT wiped, NOT aborted).
    assert after["count_verified_rules"] == baseline["count_verified_rules"] == 1
    # The expected source-validation failure was recorded (not swallowed broadly).
    failures = after.get("source_validation_failures", [])
    assert any(f["error"] == "AnnexMismatchError" and f["action"] == "rejected_candidate_kept_active_coverage"
               for f in failures), failures
    # The rejected candidate was NOT promoted; active hashes unchanged.
    assert fetch._load_json(raw / "fetch_manifest.json")["cosing-annex-iii"]["sha256"] == active_hash
    assert active.read_text() == content
    # The still-active Annex III substance survived the refresh.
    subs = [json.loads(l) for l in (tmp_path / "out1" / "eu_reg_lists.jsonl").read_text().splitlines() if l.strip()]
    assert any(s["name"] == "Hydroquinone" and s["determination"] == "restricted" for s in subs)


def test_successful_refresh_promotes_new_snapshot(tmp_path):
    """A validated new download is imported and promoted to the ACTIVE snapshot
    only after a successful import; the active manifest updates to the new hash."""
    import fetch
    import normalize

    raw = tmp_path / "raw"
    src = raw / "cosing-annex-ii"
    src.mkdir(parents=True)
    active = src / "COSING_Annex_II_v2.txt"
    old = ('"File creation date: 01/01/2026"\n'
           '"ANNEX II","Last update: 01/01/2026"\n'
           '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
           '"Substance identification"\n'
           '"Reference Number","Chemical name / INN","CAS Number","EC Number",'
           'Regulation,"Other Directives/Regulations","SCCS opinions",'
           '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
           'CMR,"Update Date"\n'
           '1,"Old substance",1-1-1,,,"(EC) 2009/1223",,,,,15/10/2010\n')
    active.write_text(old)
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(old.encode()),
        "byte_len": len(old), "retrieved_at": "2026-01-01T00:00:00+00:00",
    }}, raw / "fetch_manifest.json")

    # A NEW validated staged download arrives with different content.
    new = ('"File creation date: 08/10/2026"\n'
           '"ANNEX II","Last update: 06/10/2026"\n'
           '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
           '"Substance identification"\n'
           '"Reference Number","Chemical name / INN","CAS Number","EC Number",'
           'Regulation,"Other Directives/Regulations","SCCS opinions",'
           '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
           'CMR,"Update Date"\n'
           '1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010\n')
    staged = raw / ".incoming" / "cosing-annex-ii"
    staged.mkdir(parents=True)
    (staged / "COSING_Annex_II_v2.txt").write_text(new)
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(new.encode()),
        "byte_len": len(new), "retrieved_at": "2026-08-10T00:00:00+00:00",
    }}, raw / "fetch_staging.json")

    after = normalize.build_processed(output_dir=tmp_path / "out", inputs_dir=raw)
    assert after["count_verified_rules"] == 1
    # The new content was imported AND promoted to the active snapshot.
    assert active.read_text() == new
    active_entry = fetch._load_json(raw / "fetch_manifest.json")["cosing-annex-ii"]
    assert active_entry["accepted"] is True
    assert active_entry["sha256"] == fetch._sha256(new.encode())
    # The committed source is no longer staged.
    assert "cosing-annex-ii" not in fetch._load_json(raw / "fetch_staging.json")


def test_header_only_staged_import_does_not_wipe_coverage(tmp_path):
    """A staged export that passes validation but parses to NO rules must fall
    back to the preserved active snapshot (not drop verified coverage to 0)."""
    import fetch
    import normalize

    raw = tmp_path / "raw"
    src = raw / "cosing-annex-ii"
    src.mkdir(parents=True)
    active = src / "COSING_Annex_II_v2.txt"
    content = ('"File creation date: 08/10/2026"\n'
               '"ANNEX II","Last update: 06/10/2026"\n'
               '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
               '"Substance identification"\n'
               '"Reference Number","Chemical name / INN","CAS Number","EC Number",'
               'Regulation,"Other Directives/Regulations","SCCS opinions",'
               '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
               'CMR,"Update Date"\n'
               '1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010\n')
    active.write_text(content)
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(content.encode()),
        "byte_len": len(content), "retrieved_at": "2026-01-01T00:00:00+00:00",
    }}, raw / "fetch_manifest.json")

    baseline = normalize.build_processed(output_dir=tmp_path / "out0", inputs_dir=raw)
    assert baseline["count_verified_rules"] == 1

    # A HEADER-ONLY staged export (validates: it has the CSV header/commas, but
    # parses to no rules) is staged + marked accepted in the STAGING manifest.
    header_only = '\n'.join(content.split("\n")[:5]) + "\n"
    staged = raw / ".incoming" / "cosing-annex-ii"
    staged.mkdir(parents=True)
    (staged / "COSING_Annex_II_v2.txt").write_text(header_only)
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(header_only.encode()),
        "byte_len": len(header_only), "retrieved_at": "2026-08-10T00:00:00+00:00",
    }}, raw / "fetch_staging.json")

    after = normalize.build_processed(output_dir=tmp_path / "out1", inputs_dir=raw)
    # Coverage from the preserved ACTIVE snapshot must survive.
    assert after["count_verified_rules"] == baseline["count_verified_rules"] == 1
    # The header-only staged file must NOT have been promoted (no rules).
    active_entry = fetch._load_json(raw / "fetch_manifest.json")["cosing-annex-ii"]
    assert active_entry["sha256"] == fetch._sha256(content.encode())


def test_staged_only_source_discovered_in_empty_raw(tmp_path):
    """A source present ONLY in staging (fresh raw tree, no active dir yet) is
    discovered and imported, then promoted to the active snapshot."""
    import fetch
    import normalize

    raw = tmp_path / "raw"
    content = ('"File creation date: 08/10/2026"\n'
               '"ANNEX II","Last update: 06/10/2026"\n'
               '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
               '"Substance identification"\n'
               '"Reference Number","Chemical name / INN","CAS Number","EC Number",'
               'Regulation,"Other Directives/Regulations","SCCS opinions",'
               '"Chemical/IUPAC Name","Identified INGREDIENTS or substances e.g.",'
               'CMR,"Update Date"\n'
               '1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010\n')
    staged = raw / ".incoming" / "cosing-annex-ii"
    staged.mkdir(parents=True)
    (staged / "COSING_Annex_II_v2.txt").write_text(content)
    fetch._write_json({"cosing-annex-ii": {
        "key": "cosing-annex-ii", "name": "CosIng Annex II",
        "url": "http://x", "fetched": True, "accepted": True,
        "filename": "COSING_Annex_II_v2.txt", "kind": "cosing-annex",
        "sha256": fetch._sha256(content.encode()),
        "byte_len": len(content), "retrieved_at": "2026-08-10T00:00:00+00:00",
    }}, raw / "fetch_staging.json")

    out = normalize.build_processed(output_dir=tmp_path / "out", inputs_dir=raw)
    assert out["count_verified_rules"] == 1
    # The staged source was promoted to the active snapshot after import.
    assert (raw / "cosing-annex-ii" / "COSING_Annex_II_v2.txt").exists()
    assert "cosing-annex-ii" not in fetch._load_json(raw / "fetch_staging.json")


# ---------------------------------------------------------------------------
# Normalize reconciliation (isolated tmp inputs, never workspace).
# ---------------------------------------------------------------------------
def _build_report(inputs_dir: Path):
    import normalize
    # Output goes under the same pytest tmp base as `inputs_dir` (which is
    # writable), not a hardcoded /tmp path (blocked in some sandboxes).
    out = inputs_dir.parent / f"{inputs_dir.name}_out"
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    meta = normalize.build_processed(output_dir=out, inputs_dir=inputs_dir)
    subs = [json.loads(l) for l in (out / "eu_reg_lists.jsonl")
            .read_text().splitlines() if l.strip()]
    return meta, subs, out


def test_normalize_promotes_verified_from_cosing_export(tmp_path):
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']))
    meta, subs, out = _build_report(tmp_path)
    assert meta["count_verified_rules"] == 1
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "banned"
    assert zp["rules"][0]["annex"] == "Annex II"
    assert zp["rules"][0]["resolution"].startswith("cosing")


def test_normalize_synonym_matches_cas_matches_existing(tmp_path):
    """Butylphenyl methylpropional (Lilial's CAS) must attach to Lilial, not
    create a new identity or leave Lilial unknown."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Butylphenyl methylpropional",80-54-6,201-289-8,"(EC) 2009/1223",,,,,,15/10/2010']))
    meta, subs, _out = _build_report(tmp_path)
    by_name = {s["name"]: s for s in subs}
    lilial = by_name.get("Lilial")
    assert lilial is not None
    assert lilial["determination"] == "banned"
    assert lilial["cas"] == "80-54-6"
    assert len(lilial["rules"]) == 1
    # The synonym did not create a separate identity.
    assert "Butylphenyl methylpropional" not in (s["name"] for s in subs)


def test_normalize_placeholder_only_identifiers_are_absent(tmp_path):
    """Rows whose CAS/EC are placeholder-only lists (``- / -``, ``-/-``, ``- -``,
    ``- -- -``) must NOT be indexed as identity evidence. Such shared
    placeholders used to collide and create false quarantines; real
    multi-substance identifiers with digits are preserved verbatim."""
    import normalize
    # A row whose EC is the placeholder-only ``- / -`` while CAS is real must
    # attach to its own identity (not be quarantined against an unrelated one
    # that also carried a placeholder-only EC).
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", [
                      '1,"4,5-Diamino-1-methylpyrazole and its HCl salt, when used as a substance in hair dye products",20055-01-0 / 21616-59-1,- / -,"(EC) 2009/1223",,,,,,15/10/2010',
                      '2,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010',
                  ]))
    meta, subs, _out = _build_report(tmp_path)
    # No false quarantine from the shared placeholder-only EC.
    assert meta["count_quarantined"] == 0, meta["reconcile"]["quarantined"]
    dm = [s for s in subs if s["name"].startswith("4,5-Diamino-1-methylpyrazole")][0]
    # The placeholder-only EC must be normalised to absent (None), not "- / -".
    assert dm["ec"] is None
    assert dm["cas"] == "20055-01-0 / 21616-59-1"  # real multi-substance kept verbatim
    assert dm["determination"] == "banned"


def test_normalize_name_with_conflicting_cas_is_quarantined(tmp_path):
    """A row named Zinc pyrithione but with a different CAS must be quarantined,
    not silently promoted onto the wrong substance."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",9999-99-9,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']))
    meta, subs, _out = _build_report(tmp_path)
    assert meta["count_verified_rules"] == 0
    assert len(meta["reconcile"]["quarantined"]) == 1
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "unknown"
    assert zp["rules"] == []  # not promoted


def test_normalize_no_exports_leaves_unknown(tmp_path):
    meta, subs, _out = _build_report(tmp_path)  # empty inputs dir
    assert meta["count_verified_rules"] == 0
    assert all(s["determination"] == "unknown" for s in subs)


def test_normalize_content_hash_deterministic(tmp_path):
    """Identical inputs produce identical content_hash (only data_updated_at
    changes), so refresh detection never false-positives."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']))
    import normalize
    m1 = normalize.build_processed(output_dir=tmp_path / "a", inputs_dir=tmp_path)
    m2 = normalize.build_processed(output_dir=tmp_path / "b", inputs_dir=tmp_path)
    assert m1["content_hash"] == m2["content_hash"]
    # timestamps may differ but hash must not.
    assert m1["data_updated_at"] != m2["data_updated_at"] or \
        m1["content_hash"] == m2["content_hash"]


# ---------------------------------------------------------------------------
# Real Annex IV / V / VI schemas (conditional POSITIVE lists).
# ---------------------------------------------------------------------------
# Annex IV uses a distinct substance-name header ("Chemical name") and a
# combined Colour-index / glossary column. Annex V/VI use "Chemical name / INN"
# plus a separate "Name of Common Ingredients Glossary". All carry the same
# ordered condition columns.
_POSITIVE_HEADER_IV = (
    '"Reference Number","Chemical name",'
    '"Colour index Number / Name of Common Ingredients Glossary",'
    '"CAS Number","EC Number",Color,"Product Type, body parts",'
    '"Maximum concentration in ready for use preparation","Other",'
    '"Wording of conditions of use and warnings","Regulation",'
    '"Other Directives/Regulations","SCCS opinions","Chemical/IUPAC Name",'
    '"Identified INGREDIENTS or substances e.g.","CMR","Update Date"')
_POSITIVE_HEADER_V_VI = (
    '"Reference Number","Chemical name / INN",'
    '"Name of Common Ingredients Glossary","CAS Number","EC Number",'
    '"Product Type, body parts","Maximum concentration in ready for use preparation",'
    '"Other","Wording of conditions of use and warnings","Regulation",'
    '"Other Directives/Regulations","SCCS opinions","Chemical/IUPAC Name",'
    '"Identified INGREDIENTS or substances e.g.","CMR","Update Date"')


def _cosing_positive_export(annex: str, rows: List[str]) -> str:
    header = _POSITIVE_HEADER_IV if annex == "IV" else _POSITIVE_HEADER_V_VI
    return ('"File creation date: 09/10/2026"\n'
            f'"ANNEX {annex}","Last update: 06/10/2026"\n'
            f'"LIST OF {"COLORANTS" if annex == "IV" else "PRESERVATIVES" if annex == "V" else "UV FILTERS"} ALLOWED IN COSMETIC PRODUCTS"\n'
            '"Substance identification",Conditions\n'
            f'{header}\n' + "\n".join(rows) + "\n")


def test_cosing_iv_parses_chemical_name_and_ci():
    """Annex IV uses a different name header and a Colour Index column; the
    parser must recognise the name and keep the CI as identity evidence."""
    content = _cosing_positive_export("IV", [
        ('1,"Sodium tris(1,2-naphthoquinone 1-oximato-O,O\')ferrate(1-)",'
         '"CI 10006",16143-80-9,240-299-7,Green,,"Rinse-off product",,,'
         '"(EC) 2009/1223",,,"Sodium tris...",,CI 10006,14/08/2020'),
    ])
    recs = parse_cosing_csv(content, "https://source", annex="iv")
    assert len(recs) == 1
    r = recs[0]
    assert r["name"] == "Sodium tris(1,2-naphthoquinone 1-oximato-O,O')ferrate(1-)"
    assert r["ci"] == "CI 10006"
    assert r["status"] == "allowed_with_conditions"
    assert r["annex"] == "Annex IV"
    # The condition-column value (here a product-type restriction in the max_conc
    # column) is preserved verbatim, never parsed into a concentration verdict.
    frags = r["conditions_list"]
    assert any(f["field"] == "max_concentration" and "Rinse-off product" in f["value"]
               for f in frags)


def test_cosing_v_vi_parse_conditions_and_glossary():
    """Annex V/VI parse their name, glossary, and ordered condition fragments."""
    v = parse_cosing_csv(_cosing_positive_export("V", [
        '1,"Benzoic acid and its sodium salt","BENZOIC ACID; SODIUM BENZOATE",'
        '"65-85-0 / 532-32-1","200-618-2 / 208-534-8",'
        '"a) Rinse-off products, except oral care products\nb) Oral care products",'
        '"0,5 % (acid)",,,"(EC) 2009/1223",,,,'],), "https://source", annex="v")
    assert len(v) == 1
    r = v[0]
    assert r["name"] == "Benzoic acid and its sodium salt"
    assert r["status"] == "allowed_with_conditions"
    assert r["cas"].startswith("65-85-0 / 532-32-1")
    assert any(f["field"] == "product_types" and "Oral care products" in f["value"]
               for f in r["conditions_list"])
    assert any(f["field"] == "max_concentration" and "0,5 %" in f["value"]
               for f in r["conditions_list"])
    # Common-ingredients-glossary is sourced as an alias at reconciliation; the
    # parser keeps the raw glossary value in the source row.
    assert r["source_row"].get("Name of Common Ingredients Glossary") == "BENZOIC ACID; SODIUM BENZOATE"


def test_normalize_iv_v_vi_add_conditional_rules_and_preserve_ii_iii(tmp_path):
    """Ingesting Annex IV+V+VI adds conditional positive-list rules while the
    existing Annex II/III evidence is preserved (no loss, no downgrade)."""
    import normalize
    # Annex II (banned) + Annex III (restricted) active.
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Benzene",71-43-2,200-753-7,"(EC) 2009/1223",,,,,,15/10/2010']))
    _write_export(tmp_path, "cosing-annex-iii", "COSING_Annex_III_v2.txt",
                  _cosing_export_iii([
                      '14,"Hydroquinone","HYDROQUINONE",123-31-9,204-617-8,"Artificial nail systems",'
                      '"0.02% (after mixing for use)","Professional use","For professional use only",'
                      '"(EC) 2013/344",,,,,,,,14/05/2013']))
    # Annex IV + V + VI as conditional positive lists.
    _write_export(tmp_path, "cosing-annex-iv", "COSING_Annex_IV_v2.csv",
                  _cosing_positive_export("IV", [
                      '1,"Zinc oxide","CI 77947",1314-13-2,215-222-5,White,,"25%",,'
                      '"(EU) 2017/1413",,,,,']))
    _write_export(tmp_path, "cosing-annex-v", "COSING_Annex_V_v2.csv",
                  _cosing_positive_export("V", [
                      '1,"Benzoic acid and its sodium salt",,,,"65-85-0 / 532-32-1",,'
                      '"0,5 % (acid)",,,"(EC) 2009/1223",,,,,']))
    _write_export(tmp_path, "cosing-annex-vi", "COSING_Annex_VI_v2.csv",
                  _cosing_positive_export("VI", [
                      '2,"Benzophenone-3","BENZOPHENONE-3",131-57-7,205-031-5,'
                      '"Face products",6%,,,"(EC) 2009/1223",,,,,']))
    meta, subs, _out = _build_report(tmp_path)
    by_name = {s["name"]: s for s in subs}
    # Annex II/III evidence retained.
    benzene = by_name["Benzene"]
    assert benzene["determination"] == "banned"
    assert any(r["annex"] == "Annex II" for r in benzene["rules"])
    hydro = by_name["Hydroquinone"]
    assert any(r["annex"] == "Annex III" for r in hydro["rules"])
    # New positive-list entries carry the conditional status + conditions.
    zno = by_name["Zinc oxide"]
    assert any(r["annex"] == "Annex IV" and r["status"] == "allowed_with_conditions"
               for r in zno["rules"])
    assert zno["determination"] == "allowed_with_conditions"
    # New source kinds present and accepted.
    for k in ("cosing-annex-iv", "cosing-annex-v", "cosing-annex-vi"):
        assert meta["verified_sources"][k]["accepted"] is True


def test_normalize_positive_list_is_not_clean_verdict(tmp_path):
    """A substance present only in a conditional positive list (Annex IV/V/VI)
    is still a conditional finding — the linter must flag it, never clean it."""
    # Simulate via the normalized registry + ingredient_report.
    import eu_reg_lists as erl
    from eu_ingredient_linter import ingredient_report
    _write_export(tmp_path, "cosing-annex-vi", "COSING_Annex_VI_v2.csv",
                  _cosing_positive_export("VI", [
                      '2,"Benzophenone-3","BENZOPHENONE-3",131-57-7,205-031-5,'
                      '"Face products",6%,,,"(EC) 2009/1223",,,,,']))
    _write_export(tmp_path, "cosing-annex-iv", "COSING_Annex_IV_v2.csv",
                  _cosing_positive_export("IV", [
                      '1,"Zinc oxide","CI 77947",1314-13-2,215-222-5,White,,25%,,'
                      '"(EU) 2017/1413",,,,,']))
    meta, subs, _out = _build_report(tmp_path)
    reg = erl.load(str(_out / "eu_reg_lists.sqlite"))
    rep = ingredient_report("Benzophenone-3", reg)
    assert rep["clean"] is False
    assert rep["verdict"] == "FLAGGED"
    r = rep["results"][0]
    assert any(rr["status"] == "allowed_with_conditions" for rr in r.get("rules", []))


# ---------------------------------------------------------------------------
# Regression: rejected-file enforcement (P1).
# ---------------------------------------------------------------------------
def test_rejected_export_produces_no_verified_rules(tmp_path):
    """A file whose manifest says accepted=false must NOT produce rules even
    if it parses cleanly."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']),
                  accepted=False)
    meta, subs, _out = _build_report(tmp_path)
    assert meta["count_verified_rules"] == 0
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "unknown"
    assert zp["rules"] == []


def test_hash_mismatch_export_produces_no_verified_rules(tmp_path):
    """A manifest hash that disagrees with the file must cause rejection."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']),
                  accepted=True)
    # corrupt the manifest hash after writing the file
    manifest_path = tmp_path / "fetch_manifest.json"
    m = json.loads(manifest_path.read_text())
    m["cosing-annex-ii"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(m))
    meta, subs, _out = _build_report(tmp_path)
    assert meta["count_verified_rules"] == 0
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "unknown"


def test_missing_hash_export_is_rejected(tmp_path):
    """An accepted manifest record with NO sha256 must NOT be trusted."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']),
                  accepted=True)
    manifest_path = tmp_path / "fetch_manifest.json"
    m = json.loads(manifest_path.read_text())
    del m["cosing-annex-ii"]["sha256"]
    manifest_path.write_text(json.dumps(m))
    meta, subs, _out = _build_report(tmp_path)
    assert meta["count_verified_rules"] == 0
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "unknown"
    assert zp["rules"] == []


# ---------------------------------------------------------------------------
# Regression: identifier ambiguity quarantine (P1).
# ---------------------------------------------------------------------------
def test_ambiguous_shared_cas_is_quarantined(tmp_path):
    """CAS 9067-32-7 is shared by Hyaluronic acid and Sodium hyaluronate; a row
    naming Sodium hyaluronate must attach to it, not silently to the first."""
    content = _cosing_export("IV", ['1,"Sodium hyaluronate",9067-32-7,,,"(EC) 2009/1223",,,,,15/10/2010'])
    _write_export(tmp_path, "cosing-annex-iv", "COSING_Annex_IV_v2.txt", content)
    meta, subs, _out = _build_report(tmp_path)
    by_name = {s["name"]: s for s in subs}
    sodium = by_name["Sodium hyaluronate"]
    hyal = by_name["Hyaluronic acid"]
    # Sodium hyaluronate (exact name) gets the rule; Hyaluronic acid stays unknown.
    assert sodium["determination"] == "allowed_with_conditions"
    assert sodium["rules"] and sodium["rules"][0]["annex"] == "Annex IV"
    assert hyal["determination"] == "unknown"
    assert hyal["rules"] == []


def test_ambiguous_identifier_without_name_is_quarantined(tmp_path):
    """A row with CAS 9067-32-7 but NO matching name must be quarantined."""
    content = _cosing_export("IV", ['1,"Some unknown name",9067-32-7,,,"(EC) 2009/1223",,,,,15/10/2010'])
    _write_export(tmp_path, "cosing-annex-iv", "COSING_Annex_IV_v2.txt", content)
    meta, subs, _out = _build_report(tmp_path)
    # two substances share the CAS -> ambiguous -> quarantine (no promotion).
    assert meta["count_verified_rules"] == 0
    assert len(meta["reconcile"]["quarantined"]) == 1


def test_identifier_only_conflict_is_quarantined(tmp_path):
    """A row matched by CAS but carrying a CONFLICTING EC (no name match) must
    be quarantined, not silently attached to the single CAS candidate."""
    content = _cosing_export("II", ['1,"Unknown synonym",13463-41-7,999-999-9,"(EU) 2021/1902",,,,,15/10/2010'])
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt", content)
    meta, subs, _out = _build_report(tmp_path)
    # CAS 13463-41-7 uniquely IDs Zinc pyrithione, but EC 999-999-9 conflicts.
    assert meta["count_verified_rules"] == 0
    assert len(meta["reconcile"]["quarantined"]) == 1
    zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
    assert zp["determination"] == "unknown"
    assert zp["rules"] == []


# ---------------------------------------------------------------------------
# Regression: ECHA distinct-rule retention + source URL (P1).
# ---------------------------------------------------------------------------
def test_echa_distinct_restrictions_retained():
    sample = ("Substance name,CAS number,EC number,Reason for inclusion,"
              "Date of inclusion\n"
              "Boric acid,10043-35-3,233-139-2,Condition A,2010-01-01\n"
              "Boric acid,10043-35-3,233-139-2,Condition B,2010-01-01\n")
    recs = parse_echa_csv(sample, "svhc", "https://echa")
    conditions = sorted(r["conditions"] for r in recs)
    assert conditions == ["Condition A", "Condition B"]


def test_normalize_skips_disabled_echa_source(tmp_path):
    """ECHA sources are deliberately disabled (AGENTS.md #9). A file placed in
    an ECHA raw dir with an accepted manifest must still be SKIPPED — ECHA data
    is never ingested into the release snapshot."""
    _write_export(tmp_path, "echa-svhc", "svhc.csv",
                  "Substance name,CAS number,EC number,Reason for inclusion\n"
                  "Boric acid,10043-35-3,233-139-2,Reprotoxic\n")
    meta, subs, _out = _build_report(tmp_path)
    # No SVHC rule is ingested; only identity-only curated entries may remain.
    assert all(s["determination"] != "listed" for s in subs)
    assert meta["count_verified_rules"] == 0
    assert all(not any(r["annex"] == "SVHC" for r in s["rules"]) for s in subs)


def test_cosing_and_echa_resolve_with_cosing_only(tmp_path):
    """A CosIng export is ingested; an ECHA source is skipped as disabled, so a
    CosIng-only snapshot never mixes ECHA evidence into the release."""
    _write_export(tmp_path, "cosing-annex-ii", "COSING_Annex_II_v2.txt",
                  _cosing_export("II", ['1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",,,,,,15/10/2010']))
    _write_export(tmp_path, "echa-svhc", "b.csv",
                  "Substance name,CAS number,EC number,Reason for inclusion\n"
                  "Boric acid,10043-35-3,233-139-2,Reprotoxic\n")
    meta, subs, _out = _build_report(tmp_path)
    urls = {s["rules"][0]["source_url"] for s in subs if s["rules"]}
    assert all("echa" not in u for u in urls)  # no ECHA rule ingested
    assert any("ec.europa" in u or "cosing" in u for u in urls)  # CosIng present
    assert meta["count_verified_rules"] == 1


# ---------------------------------------------------------------------------
# Regression: content hash covers aliases/E-numbers/CI (P2).
# ---------------------------------------------------------------------------
def test_content_hash_sensitive_to_non_identity_fields(tmp_path):
    import normalize
    from normalize import compute_content_hash
    base_subs = [{"id": "x", "name": "Red 40", "aliases": ["e129"],
                  "cas": None, "ec": None, "e_number": "E129", "ci": None,
                  "determination": "unknown", "sources": [], "rules": []}]
    h1 = compute_content_hash(base_subs, {})
    # change an alias -> hash must change
    changed_aliases = [dict(base_subs[0], aliases=["e129", "new-alias"])]
    h2 = compute_content_hash(changed_aliases, {})
    assert h1 != h2
    # change E-number -> hash must change
    changed_enum = [dict(base_subs[0], e_number="E999")]
    h3 = compute_content_hash(changed_enum, {})
    assert h1 != h3
