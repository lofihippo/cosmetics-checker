"""Fetch official regulatory export files into data/raw/ (best-effort).

IMPORTANT honesty rules:
  * We NEVER scrape ECHA or any official website. We only download *official
    export / downloadable files* at stable, documented URLs.
  * If a source cannot be reached, the pipeline records it as NOT FETCHED and
    leaves its classification unverified (it does not fabricate data).
  * Each fetched file is recorded with its SHA-256, content length, and the
    raw file lands in data/raw/<source>/.
  * A file is only accepted when it passes its kind-specific *validator*
    (non-empty, expected content signature). We never accept garbage as data.

Snapshot safety (a refresh must never wipe usable coverage):
  * A download is staged at ``data/raw/.incoming/<key>/`` first and validated
    there. It is only promoted to the ACTIVE file at ``data/raw/<key>/`` after
    it passes validation (the "last validated snapshot" is preserved on disk).
  * A failed/rejected attempt is recorded in ``data/raw/fetch_attempts.json``
    and does NOT overwrite the active manifest entry's ``accepted`` status.
    The active ``data/raw/fetch_manifest.json`` only gains an ``accepted:true``
    record for a source once that source's NEW file is validated AND the caller
    confirms the import succeeded (see ``commit_source``).
  * Therefore a transient 403 / HTML / validation failure leaves the previous
    validated snapshot and its ``accepted`` status untouched: the pipeline
    keeps its current verified coverage instead of dropping to zero.

Official sources configured:
  * ``eurlex-1223``   — EUR-Lex CELEX 32009R1223 (cosmetics Regulation text).
  * ``cosing-annex*``  — European Commission CosIng Annex exports (CSV/TSV).
  * ``echa-svhc``     — ECHA SVHC Candidate List export.
  * ``echa-annex-xiv``— ECHA Authorisation List (Annex XIV) export.
  * ``echa-annex-xvii``— ECHA Restrictions (Annex XVII) export.

**ECHA is intentionally DISABLED.** Per the project scope decision, ECHA
acquisition is deferred: we do not accept ECHA's portal terms, do not obtain or
ingest ECHA exports, and ECHA sources are `enabled=False`, so a normal
refresh (``fetch_all`` / the scheduled workflow) never contacts ECHA. ECHA
coverage is explicitly marked missing / "not included" throughout the API,
linter and docs — an absent ECHA list is a coverage gap, never a clean result.
ECHA entries are also excluded from the Phase 0 curated prototype (see
``build_eu_flags.py``).

On this network CosIng structured exports are not exposed at a stable
documented URL; these are recorded as not-fetched (never scraped). The parsers
are schema-compatible so CosIng ingestion is genuinely end-to-end. ECHA
parsing code remains present (``echa_parser.py``) so coverage/coverage-gap
semantics are preserved, but it is never reached by a default refresh.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"

UA = "Mozilla/5.0 (compatible; eu-compliance-tools/0.1)"
MANIFEST = RAW / "fetch_manifest.json"
ATTEMPTS = RAW / "fetch_attempts.json"
INCOMING = RAW / ".incoming"

# Stable, documented downloadable endpoints (not scraped pages).
SOURCES: Dict[str, Dict[str, Any]] = {
    "eurlex-1223": {
        "name": "Regulation (EC) No 1223/2009 (cosmetics legal text)",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/XML/?uri=CELEX:32009R1223",
        "filename": "32009R1223.xml",
        "kind": "legal-text-xml",
    },
    "echa-svhc": {
        "name": "ECHA SVHC Candidate List",
        "url": "https://echa.europa.eu/documents/10162/0/ECHA_SVHC_candidate_list.csv",
        "filename": "echa_svhc_candidate_list.csv",
        "kind": "echa-svhc",
        "enabled": False,
    },
    "echa-annex-xiv": {
        "name": "ECHA Authorisation List (Annex XIV)",
        "url": "https://echa.europa.eu/documents/10162/0/ech_annex_xiv.csv",
        "filename": "echa_annex_xiv.csv",
        "kind": "echa-annex-xiv",
        "enabled": False,
    },
    "echa-annex-xvii": {
        "name": "ECHA Restrictions (Annex XVII)",
        "url": "https://echa.europa.eu/documents/10162/0/echa_annex_xvii_restrictions.csv",
        "filename": "echa_annex_xvii.csv",
        "kind": "echa-annex-xvii",
        "enabled": False,
    },
    "cosing-annex-ii": {
        "name": "CosIng Annex II (substances prohibited in cosmetic products)",
        # The official CosIng Annex II export download (not the HTML list page).
        # Independently verified to return the Annex II export preamble/header.
        "url": "https://api.tech.ec.europa.eu/cosing20/1.0/api/annexes/II/export-csv",
        "filename": "COSING_Annex_II_v2.txt",
        "kind": "cosing-annex",
    },
    "cosing-annex-iii": {
        "name": "CosIng Annex III (substances cosmetic products must not contain except subject to restrictions)",
        # The CosIng annex list page is an Angular SPA; the CSV export is fetched
        # from this API endpoint when the user clicks "CSV". This is an official
        # export file download (not page scraping).
        "url": "https://api.tech.ec.europa.eu/cosing20/1.0/api/annexes/III/export-csv",
        "filename": "COSING_Annex_III_v2.txt",
        "kind": "cosing-annex",
    },
    # Annex IV-VI are conditional POSITIVE lists. Their presence is evidence of
    # the source list, not an overall clean/compliant determination; the
    # conditions are preserved but not auto-satisfied.
    "cosing-annex-iv": {
        "name": "CosIng Annex IV (colourants allowed in cosmetic products)",
        "url": "https://api.tech.ec.europa.eu/cosing20/1.0/api/annexes/IV/export-csv",
        "filename": "COSING_Annex_IV_v2.csv",
        "kind": "cosing-annex",
    },
    "cosing-annex-v": {
        "name": "CosIng Annex V (preservatives allowed in cosmetic products)",
        "url": "https://api.tech.ec.europa.eu/cosing20/1.0/api/annexes/V/export-csv",
        "filename": "COSING_Annex_V_v2.csv",
        "kind": "cosing-annex",
    },
    "cosing-annex-vi": {
        "name": "CosIng Annex VI (UV filters allowed in cosmetic products)",
        "url": "https://api.tech.ec.europa.eu/cosing20/1.0/api/annexes/VI/export-csv",
        "filename": "COSING_Annex_VI_v2.csv",
        "kind": "cosing-annex",
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_content(kind: str, data: bytes) -> bool:
    """Validate downloaded content for a given source kind.

    Returns True only if the content is plausibly the intended export (non-empty
    and a recognizable signature). We never accept an HTML error page as data.
    """
    if not data:
        return False
    head = data[:2048].decode("utf-8", errors="replace").lstrip().lower()
    if kind == "legal-text-xml":
        return "<" in head and ("xml" in head or "<?xml" in head)
    if kind.startswith("echa-") or kind == "cosing-annex":
        # a CSV/TSV export: header row present, no <html> wrapper
        if head.startswith("<") or "<html" in head:
            return False
        return any(c in head for c in (",", ";", "\t"))
    return True  # unknown kind: don't reject


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _staging_dest(key: str, out_dir: Path = RAW) -> Path:
    """Return the staging path for a key; the incoming dir is scratch-only."""
    return INCOMING if out_dir == RAW else (out_dir / ".incoming")


def _staging_manifest(out_dir: Path = RAW) -> Path:
    """Manifest describing validated staged files awaiting a successful import."""
    return RAW / "fetch_staging.json" if out_dir == RAW else (out_dir / "fetch_staging.json")


def fetch_source(key: str, out_dir: Path = RAW) -> Dict:
    """Download a single official export into a STAGING area (never the active
    file) and return provenance. Does NOT overwrite the active manifest.

    The active raw file + ``accepted`` record are only touched by
    ``commit_source`` after the caller confirms a successful import. A failed
    or rejected attempt is recorded in ``fetch_attempts.json`` and leaves the
    previous validated snapshot intact.
    """
    spec = SOURCES.get(key)
    if spec is None:
        raise KeyError(f"unknown source: {key}")
    if spec.get("enabled") is False:
        # Disabled source (e.g. ECHA, deferred): do NOT contact the network.
        # Record a non-fetch attempt so provenance shows why it is skipped.
        attempt = {
            "key": key, "name": spec["name"], "url": spec["url"],
            "fetched": False, "accepted": False,
            "error": "DisabledError",
            "detail": "source disabled (deferred); not contacted",
            "filename": spec["filename"], "kind": spec["kind"],
            "retrieved_at": _now_iso(),
        }
        _record_attempt(attempt, out_dir)
        return attempt
    stage_dir = _staging_dest(key, out_dir) / key
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(spec["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except Exception as exc:  # network / HTTP error -> record unavailable
        attempt = {
            "key": key, "name": spec["name"], "url": spec["url"],
            "fetched": False, "accepted": False,
            "error": type(exc).__name__, "detail": str(exc)[:300],
            "filename": spec["filename"], "kind": spec["kind"],
            "retrieved_at": _now_iso(),
        }
        _record_attempt(attempt, out_dir)
        return attempt
    valid = validate_content(spec["kind"], data)
    if not valid:
        attempt = {
            "key": key, "name": spec["name"], "url": spec["url"],
            "fetched": True, "accepted": False,
            "error": "ValidationError",
            "detail": "content did not match expected export format",
            "filename": spec["filename"], "kind": spec["kind"],
            "retrieved_at": _now_iso(),
        }
        _record_attempt(attempt, out_dir)
        return attempt
    # Validated -> land in the staging area (NOT the active file yet) and record
    # its integrity in a STAGING manifest (never the ACTIVE manifest).
    (stage_dir / spec["filename"]).write_bytes(data)
    report = {
        "key": key, "name": spec["name"], "url": spec["url"],
        "fetched": True, "accepted": True,
        "filename": spec["filename"], "kind": spec["kind"],
        "sha256": _sha256(data), "byte_len": len(data),
        "content_type": content_type,
        "retrieved_at": _now_iso(),
    }
    _write_json({**{k: v for k, v in _load_json(_staging_manifest(out_dir)).items()},
                 key: report},
                _staging_manifest(out_dir))
    return report


def _record_attempt(attempt: Dict, out_dir: Path = RAW) -> None:
    """Record a failed/rejected attempt separately from the active manifest."""
    attempts = _load_json(ATTEMPTS if out_dir == RAW else (out_dir / "fetch_attempts.json"))
    key = attempt["key"]
    prior = attempts.get(key, [])
    prior.append({k: v for k, v in attempt.items()})
    # cap history to the most recent 20 attempts per source.
    attempts[key] = prior[-20:]
    _write_json(attempts, ATTEMPTS if out_dir == RAW else (out_dir / "fetch_attempts.json"))


def commit_source(key: str, report: Dict, out_dir: Path = RAW) -> Dict:
    """Promote a validated staged download to the ACTIVE snapshot.

    Call this from the pipeline AFTER the new file has been successfully
    imported (e.g. after normalize produced verified rules for it).
    ``commit_source`` moves the staged file over the active one and updates
    the ACTIVE manifest entry to ``accepted:true``. It is a no-op for a
    report that is not ``accepted`` or is missing its staged file.
    """
    spec = SOURCES.get(key)
    if spec is None:
        return report
    if not report.get("accepted") or not report.get("sha256"):
        # never promote a rejected/incomplete attempt
        return report
    stage_dir = _staging_dest(key, out_dir) / key
    staged = stage_dir / spec["filename"]
    if not staged.exists():
        return report
    active_dir = (out_dir if out_dir != RAW else RAW) / key
    active_dir.mkdir(parents=True, exist_ok=True)
    # preserve any previous validated snapshot as `<filename>.prev` for audit.
    active = active_dir / spec["filename"]
    if active.exists():
        shutil.copy2(active, active_dir / f"{spec['filename']}.prev")
    shutil.move(str(staged), str(active))
    _update_active_manifest(key, report, out_dir)
    # The staged download has been committed: drop it from the staging manifest
    # so it isn't re-imported as a "fresh" download on the next run.
    staging = _load_json(_staging_manifest(out_dir))
    if key in staging:
        del staging[key]
        _write_json(staging, _staging_manifest(out_dir))
    return report


def _update_active_manifest(key: str, report: Dict, out_dir: Path = RAW) -> None:
    """Merge a committed source into the ACTIVE manifest (never regress it)."""
    manifest = _load_json(MANIFEST if out_dir == RAW else (out_dir / "fetch_manifest.json"))
    manifest[key] = dict(report)
    _write_json(manifest, MANIFEST if out_dir == RAW else (out_dir / "fetch_manifest.json"))


def persist_manifest(report: Dict, manifest: Path = MANIFEST) -> None:
    """Write a manifest. Deprecated/legacy helper for callers that build the
    whole manifest themselves; the fetch pipeline should use ``commit_source``
    so a failed attempt never regresses accepted coverage."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


def fetch_all(out_dir: Path = RAW, write_manifest: bool = True) -> Dict:
    """Fetch every configured source into staging; persist an ATTEMPTS log.

    This does NOT commit anything: no active raw file is overwritten and the
    active manifest is only changed when the pipeline ``commit_source`` after a
    successful import. Returns the per-key report dict.
    """
    report: Dict = {}
    for key in SOURCES:
        if SOURCES[key].get("enabled") is False:
            # Deferred source (e.g. ECHA): do not contact it on a normal refresh.
            report[key] = fetch_source(key, out_dir)  # records the skip, no network
            continue
        report[key] = fetch_source(key, out_dir)
    return report


if __name__ == "__main__":
    keys = sys.argv[1:] or list(SOURCES)
    report = {k: fetch_source(k) for k in keys}
    print(json.dumps(report, indent=2))

