"""Isolated unattended snapshot restore/refresh/validate/bundle orchestration.

Regulatory information, not legal advice.

This is the reviewable end-to-end path for refreshing the verified regulatory
snapshot WITHOUT ever writing into the live checkout. It restores a trusted
baseline bundle into a run-owned, IMMUTABLE baseline tree, builds a SEPARATE
candidate tree for refresh, validates the complete candidate, produces a
rule-level review diff and -- for a genuine, promotion-ready change -- a
consumer-compatible snapshot ZIP + SHA-256 + concise manifest/diff.

It reuses the existing `fetch` (staging-first, active-snapshot fallback),
`normalize.build_processed` (explicit inputs/output dirs) and `snapshot`
(export/import/verify) tooling. It does NOT implement a second parser, evaluator
or content-hash algorithm, and it never fetches or normalizes into the live
workspace snapshot.

Regulatory truthfulness / safety:
  * Unknown/absent is never clean; a failed or unavailable source keeps its last
    accepted coverage; retrieval timestamps stay separate from data freshness.
  * A semantically unchanged or fully-failed refresh SELECTS the accepted
    baseline (marking it as the chosen snapshot) without producing a newly-dated
    distribution snapshot or mutating the returned tree's freshness.
  * Rule-level changes are compared per source using a stable source identity +
    official reference + substance identity, with disambiguation for repeated
    official references. A removed rule (e.g. a single Annex III restriction
    whose identity counts are otherwise unchanged) is reported as a reduction
    and marks the candidate ``review_required``, not silently treated as clean.
  * A candidate is distinct from promotion-readiness: it must fully validate to
    be a valid candidate, and must not contain a consequential reduction to be
    promotion-ready.
  * Synthetic update fixtures are only ever supplied as explicit offline source
    files (``--source-file KEY=PATH``); they never become live verified data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "data" / "scripts"))

import fetch as fetch_mod  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402
from normalize import build_processed  # noqa: E402

DISCLAIMER = "Regulatory information, not legal advice."
MANIFEST = snapshot_mod.MANIFEST  # "snapshot-manifest.json"
DIST_NAME = "snapshot-bundle.zip"
SHA_FILE = "snapshot-bundle.zip.sha256"
BASELINE_DIR = "baseline"
CANDIDATE_DIR = "candidate"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256(p.read_bytes())


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(data: dict, p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _write_audit(work: Path, *, status: str, message: str,
                 attempted: bool, baseline_content_hash: Optional[str] = None,
                 candidate_content_hash: Optional[str] = None,
                 detail: Optional[dict] = None,
                 promotion_ready: bool = False) -> dict:
    """Persist a concise, typed outcome audit for EVERY run outcome.

    ``attempted`` distinguishes an operation that attempted a refresh from one
    that failed before staging. A failure is recorded with its error message and
    is NEVER marked promotion-ready (``promotion_ready`` stays False unless the
    caller explicitly sets it on a successful outcome). This audit is
    uploaded/exported separately from any verified candidate bundle (see
    ``export_candidate``) so a no-change/failure run still leaves a durable trace.
    """
    audit = {
        "status": status,               # no-change | changed | failure
        "attempted": attempted,
        "message": message,
        "baseline_content_hash": baseline_content_hash,
        "candidate_content_hash": candidate_content_hash,
        "promotion_ready": promotion_ready,
        "detail": detail,
        "audited_at": _now_iso(),
        "disclaimer": DISCLAIMER,
    }
    _write_json(audit, work / "refresh-audit.json")
    return audit


def _download_to(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``.

    Supports ``http(s)`` and ``file://`` (used for local/isolated fixtures so a
    trusted baseline can be restored without a live host). Raises on network or
    read failure; the caller verifies the digest afterwards.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        src = Path(url[len("file://"):])
        shutil.copyfile(src, dest)
        return
    req = urllib.request.Request(url, headers={"User-Agent": fetch_mod.UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    dest.write_bytes(data)


# ---------------------------------------------------------------------------
# Restore a trusted baseline into a run-owned, IMMUTABLE tree.
# ---------------------------------------------------------------------------
def restore_baseline(bundle_url: str, expected_sha256: str, dest: Path) -> dict:
    """Download the trusted baseline bundle, verify the SHA-256, verify + import.

    The bundle is acquired into ``dest`` (a run-owned directory, never the live
    checkout) and imported into ``dest/baseline``, which is treated as IMMUTABLE:
    it is never normalized in place. A missing/corrupt baseline (bad URL, digest
    mismatch, failed full verify/import) raises, so a refresh never starts from
    zero coverage or a curated seed.
    """
    dest.mkdir(parents=True, exist_ok=True)
    acquire_dir = dest / "acquire"
    acquire_dir.mkdir(parents=True, exist_ok=True)
    bundle = acquire_dir / "baseline-bundle.zip"
    _download_to(bundle_url, bundle)
    actual = _sha256_file(bundle)
    if actual != expected_sha256:
        raise ValueError(
            f"baseline digest mismatch (expected {expected_sha256}, got {actual})")
    baseline = dest / BASELINE_DIR
    if baseline.exists():
        raise FileExistsError(f"baseline work tree already exists: {baseline}")
    report = snapshot_mod.verify_snapshot(bundle, baseline)
    report["baseline_bundle_sha256"] = expected_sha256
    report["restored_at"] = _now_iso()
    _write_json(report, dest / "baseline-restore.json")
    return report


def _copy_tree(src: Path, dst: Path) -> None:
    """Byte-copy a snapshot tree (used to build an immutable baseline copy)."""
    if dst.exists():
        raise FileExistsError(f"candidate tree already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


# ---------------------------------------------------------------------------
# Offline source fixture injection (test/controlled-update, never live data).
# ---------------------------------------------------------------------------
def stage_source_fixtures(paths: Dict[str, Path], raw_dir: Path,
                          offline: bool) -> List[Dict]:
    """Copy explicit source fixture files into the isolated tree's staging area
    and record them in the staging manifest.

    Used for acceptance tests and controlled changes; each file is an official
    export-shaped fixture (marked as such in the retrieval provenance).
    """
    reports: List[Dict] = []
    for key, path in sorted(paths.items()):
        spec = fetch_mod.SOURCES.get(key)
        if spec is None:
            raise KeyError(f"unknown source key: {key}")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"fixture source file not found: {path}")
        data = path.read_bytes()
        valid = fetch_mod.validate_content(spec["kind"], data)
        if not valid:
            raise ValueError(f"fixture for {key} is not a valid {spec['kind']} export")
        stage_dir = fetch_mod._staging_dest(key, raw_dir) / key  # noqa: SLF001
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / spec["filename"]).write_bytes(data)
        report = {
            "key": key, "name": spec["name"], "url": spec["url"],
            "fetched": True, "accepted": True,
            "filename": spec["filename"], "kind": spec["kind"],
            "sha256": _sha256(data), "byte_len": len(data),
            "content_type": "application/octet-stream",
            "retrieved_at": _now_iso(),
            "fixture": True,  # explicitly marked; never mixed into live data
        }
        reports.append(report)
    # Merge fixtures into the STAGING manifest (never the ACTIVE manifest).
    staging = fetch_mod._load_json(fetch_mod._staging_manifest(raw_dir))
    for report in reports:
        staging[report["key"]] = report
    fetch_mod._write_json(staging, fetch_mod._staging_manifest(raw_dir))  # noqa: SLF001
    return reports


# ---------------------------------------------------------------------------
# Rule-level identity helpers.
# ---------------------------------------------------------------------------
def _rule_identity(record: dict, rule: dict) -> dict:
    """Return a STABLE identity for a rule.

    Identity = source + official reference + substance identity (name/cas/ec).
    Row positions and file-row locators are PROVENANCE, NOT identity: a change
    that only renumbers/reorders rows must not fabricate removals/additions.
    Repeated official references across substances are disambiguated by the
    substance identity; if still ambiguous, deterministic duplicate handling is
    applied by the caller (see ``_rule_index``).
    """
    raw = rule.get("raw") or {}
    source_kind = raw.get("source_kind") or record.get("sources", [None])[0]
    ref = (raw.get("official_reference") or raw.get("reference_number")
           or raw.get("row_reference"))
    return {
        "source": source_kind,
        "official_reference": str(ref),
        "substance_name": record.get("name"),
        "cas": record.get("cas"),
        "ec": record.get("ec"),
    }


# ``source_row`` columns that carry SUBSTANTIVE regulatory scope / exception /
# restriction / condition wording (and substance identity), which must be part of
# the semantic content fingerprint. Columns that are pure provenance are
# deliberately EXCLUDED: ``Reference Number`` (a row/entry locator that is
# already the rule's identity) and ``Update Date`` (a transient data-freshness
# value, not regulatory content). Row positions / file-row locators are stored
# separately (``row_reference``/``entry_number``) and never affect the key.
_SOURCE_ROW_SEMANTIC_FIELDS = (
    "Chemical name / INN",
    "Chemical name",
    "Chemical/IUPAC Name",
    "Name of Common Ingredients Glossary",
    "Identified INGREDIENTS or substances e.g.",
    "CAS Number",
    "EC Number",
    "Maximum concentration in ready for use preparation",
    "Product Type, body parts",
    "Wording of conditions of use and warnings",
    "Other",
    "Other Directives/Regulations",
    "CMR",
    "SCCS opinions",
)


def _rule_content_key(record: dict, rule: dict) -> str:
    """Fingerprint the rule's *semantic content* only (no identity/provenance).

    Conditions/scope/status/regulation/resolution changes produce a different
    content key and are reported as a change. Substantive scope/exception and
    restriction text in ``raw.source_row`` (e.g. Annex II "with the exception of
    entry 14 in Annex III" for Hydroquinone, or Annex III/VI maximum
    concentrations and warnings wording) is INCLUDED. Row positions
    (``source_row["Reference Number"]`` / ``row_reference`` / ``entry_number``)
    and transient acquisition metadata are NOT content and never affect this key.
    """
    raw = rule.get("raw") or {}
    srow = raw.get("source_row") or {}
    return json.dumps({
        "regulation": rule.get("regulation"),
        "conditions": rule.get("conditions"),
        "conditions_list": raw.get("conditions_list"),
        "status": rule.get("status"),
        "annex": rule.get("annex"),
        "resolution": rule.get("resolution"),
        "effective_date": rule.get("effective_date"),
        # Substantive source-row scope/exception/restriction text. Only the
        # curated semantic columns are compared; provenance columns are skipped.
        "source_scope": {
            field: srow.get(field) for field in _SOURCE_ROW_SEMANTIC_FIELDS
            if srow.get(field)
        },
    }, sort_keys=True, ensure_ascii=False)


def _rule_index(work: Path) -> Dict[str, dict]:
    """Index rules by stable identity, keeping the semantic content key.

    Returns ``{identity_key: {"identity": ..., "content": sorted_content_keys,
    "rule_count": n}}``. Repeated identities are handled deterministically by
    sorting their content keys, so rule order/duplication does not affect the
    comparison outcome. ``rule_count`` is the number of INDIVIDUAL rules sharing
    that identity (distinct rules can share an identity; identity count is NOT
    the rule count).
    """
    out: Dict[str, dict] = {}
    for record in _records(work):
        for rule in record.get("rules", []):
            ident = _rule_identity(record, rule)
            key = json.dumps(ident, sort_keys=True, ensure_ascii=False)
            entry = out.setdefault(key, {"identity": ident, "content": [], "rule_count": 0})
            entry["content"].append(_rule_content_key(record, rule))
            entry["rule_count"] += 1
    for entry in out.values():
        entry["content"] = sorted(set(entry["content"]))
    return out


def _records(work: Path) -> List[dict]:
    jsonl = work / "data" / "processed" / "eu_reg_lists.jsonl"
    if not jsonl.exists():
        return []
    return [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]


def _coverage(meta: dict) -> dict:
    sources = meta.get("verified_sources", {})
    return {
        "count_substances": meta.get("count_substances"),
        "count_verified_substances": meta.get("count_verified_rules"),
        "count_identity_only": meta.get("count_identity_only"),
        # Accepted coverage is the set of sources with ``accepted`` True.
        "accepted_sources": sorted(
            key for key, info in sources.items() if info.get("accepted")),
        "per_source": {
            key: {
                "kind": info.get("kind"),
                "accepted": info.get("accepted"),
                "files": {
                    name: {"sha256": f.get("sha256"), "byte_len": f.get("byte_len")}
                    for name, f in (info.get("files") or {}).items()
                },
            }
            for key, info in sorted(sources.items())
        },
    }


def build_diff(baseline_meta: dict, candidate_meta: dict,
               baseline_work: Path, candidate_work: Path) -> dict:
    """Compare source/rule/coverage between the baseline and the candidate.

    Emits per-source rule additions/removals/changes (using stable identity +
    official reference disambiguation), condition/scope changes, and accepted
    coverage transitions. A candidate with a consequential reduction is marked
    ``review_required``.
    """
    base_cov = _coverage(baseline_meta)
    cand_cov = _coverage(candidate_meta)
    base_rule_index = _rule_index(baseline_work)
    cand_rule_index = _rule_index(candidate_work)

    # Stable-identity comparison. An identity present in both is matched BEFORE
    # comparing content: a change to row positions alone (reorder/renumber) does
    # not fabricate additions/removals. An identity present in only one tree is a
    # genuine addition or removal.
    added_keys = sorted(set(cand_rule_index) - set(base_rule_index))
    removed_keys = sorted(set(base_rule_index) - set(cand_rule_index))
    # An identity present in BOTH but whose semantic content differs is a change.
    changed_keys = sorted(
        key for key in (set(base_rule_index) & set(cand_rule_index))
        if base_rule_index[key]["content"] != cand_rule_index[key]["content"])

    def _src(key_list: List[str]) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {}
        for key in key_list:
            ident = (cand_rule_index.get(key) or base_rule_index[key])["identity"]
            k = ident.get("source") or "unknown"
            out.setdefault(k, []).append(ident)
        return out

    per_source: Dict[str, dict] = {}
    for key in sorted(set(list(_src(added_keys)) + list(_src(removed_keys))
                          + list(_src(changed_keys)))):
        per_source[key] = {
            "added": _src(added_keys).get(key, []),
            "removed": _src(removed_keys).get(key, []),
            "changed": _src(changed_keys).get(key, []),
        }

    base_sources = set(base_cov["per_source"])
    cand_sources = set(cand_cov["per_source"])
    lost_sources = sorted(key for key in (base_sources - cand_sources)
                          )  # inventory presence
    # Accepted-coverage transitions (a source that is in the inventory but no
    # longer accepted contributes NO usable rules -> a real loss).
    lost_accepted = sorted(key for key in base_cov["accepted_sources"]
                           if key not in cand_cov["accepted_sources"])
    added_accepted = sorted(key for key in cand_cov["accepted_sources"]
                            if key not in base_cov["accepted_sources"])

    changes = {
        "content_hash_changed":
            base_cov != cand_cov
            or baseline_meta.get("content_hash") != candidate_meta.get("content_hash"),
        "count_substances_baseline": base_cov["count_substances"],
        "count_substances_candidate": cand_cov["count_substances"],
        "count_verified_baseline": base_cov["count_verified_substances"],
        "count_verified_candidate": cand_cov["count_verified_substances"],
        "identity_only_baseline": base_cov["count_identity_only"],
        "identity_only_candidate": cand_cov["count_identity_only"],
        # Per-source counts of stable IDENTITIES (a distinct rule group entry
        # sharing a source+reference+substance identity is one identity). Total
        # identities = sum of these.
        "rule_counts_baseline": dict(Counter(
            entry["identity"]["source"] or "unknown"
            for entry in base_rule_index.values())),
        "rule_counts_candidate": dict(Counter(
            entry["identity"]["source"] or "unknown"
            for entry in cand_rule_index.values())),
        # Per-source counts of INDIVIDUAL RULES. Distinct rules can share an
        # identity (e.g. Annex III reports 379 rules across a smaller number of
        # identity groups); ``rule_counts_*`` is the identity count, this is the
        # true rule count.
        "individual_rule_counts_baseline": dict(Counter(
            entry["identity"]["source"] or "unknown"
            for entry in base_rule_index.values() for _ in range(entry["rule_count"]))),
        "individual_rule_counts_candidate": dict(Counter(
            entry["identity"]["source"] or "unknown"
            for entry in cand_rule_index.values() for _ in range(entry["rule_count"]))),
        "per_source": per_source,
        "rules_added_refs": added_keys,
        "rules_removed_refs": removed_keys,
        "rules_changed_refs": changed_keys,
        # Actual before/after semantic content for each changed rule identity.
        # Row positions are provenance and are NOT part of this diff.
        "rule_changes": {
            key: {
                "identity": cand_rule_index[key]["identity"],
                "before_content": base_rule_index[key]["content"],
                "after_content": cand_rule_index[key]["content"],
            }
            for key in changed_keys
        },
        "source_lists_present_only_in_baseline": lost_sources,
        "source_lists_present_only_in_candidate": sorted(cand_sources - base_sources),
        "accepted_sources_only_in_baseline": lost_accepted,
        "accepted_sources_only_in_candidate": added_accepted,
    }

    # Consequential reductions -> review_required.
    reductions = []
    if cand_cov["count_verified_substances"] < base_cov["count_verified_substances"]:
        reductions.append("verified-substance coverage decreased")
    if cand_cov["count_substances"] < base_cov["count_substances"]:
        reductions.append("substance count decreased")
    for key in lost_accepted:
        reductions.append(f"accepted source list lost from coverage: {key}")
    for key in per_source:
        if per_source[key]["removed"]:
            reductions.append(
                f"rule removed from {key}: "
                + ", ".join(f"{r['official_reference']}({r['substance_name']})"
                            for r in per_source[key]["removed"]))
    for key in per_source:
        if per_source[key]["changed"]:
            reductions.append(
                f"rule changed in {key}: "
                + ", ".join(f"{r['official_reference']}({r['substance_name']})"
                            for r in per_source[key]["changed"]))
    changes["consequential_reductions"] = reductions
    changes["review_required"] = bool(reductions)
    return changes


# ---------------------------------------------------------------------------
# Refresh -> validate -> compare -> bundle.
# ---------------------------------------------------------------------------
def refresh_in_tree(work: Path, offline_sources: Optional[Dict[str, Path]] = None,
                    live: bool = False) -> dict:
    """Stage sources into the candidate raw dir, then build_processed there.

    Never touches the live checkout or the immutable baseline. Returns the
    candidate metadata.
    """
    raw_dir = work / "data" / "raw"
    processed_dir = work / "data" / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # A bundle round-trip omits EMPTY source directories (sources with no
    # accepted files, e.g. ECHA lists that were never fetched). Recreate them.
    for key in fetch_mod._load_json(raw_dir / "fetch_manifest.json"):  # noqa: SLF001
        (raw_dir / key).mkdir(parents=True, exist_ok=True)
    if offline_sources:
        stage_source_fixtures(offline_sources, raw_dir, offline=True)
    if live:
        fetch_mod.fetch_all(out_dir=raw_dir)
    meta = build_processed(output_dir=processed_dir, inputs_dir=raw_dir)
    return meta


def validate_candidate(work: Path) -> dict:
    """Run the full snapshot tree-verification contract on the candidate tree."""
    meta, files = snapshot_mod.validate_tree(work)
    return {
        "content_hash": meta["content_hash"],
        "data_updated_at": meta["data_updated_at"],
        "count_substances": meta["count_substances"],
        "count_verified_substances": meta["count_verified_rules"],
        "files_verified": len(files),
        "disclaimer": DISCLAIMER,
    }


def export_candidate(work: Path, out_dir: Path, review: dict) -> dict:
    """Produce the consumer-compatible snapshot ZIP + SHA-256 + review evidence.

    Writes the verified bundle + digest, then a concise review manifest
    (status/diff/attempt summary) so a reviewer has the baseline-to-candidate
    difference alongside the artifact (not just the archive's internal manifest).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / DIST_NAME
    report = snapshot_mod.export_snapshot(work, bundle)
    digest = _sha256_file(bundle)
    (out_dir / SHA_FILE).write_text(f"{digest}  {DIST_NAME}\n")
    # Re-import + re-verify the produced bundle before marking it distributable.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="eu-candidate-verify-") as tmp:
        verify_dir = Path(tmp) / "verify-import"
        _report = snapshot_mod.verify_snapshot(bundle, verify_dir)
    report["bundle_sha256"] = digest
    report["bundle_reimport_verified"] = True
    report["disclaimer"] = DISCLAIMER
    # Persist a bounded review manifest (status + diff + attempt summary). It is
    # a review aid, NOT the bundle integrity manifest (which lives inside the ZIP).
    _write_json({
        "status": review.get("status", "changed"),
        "selected_snapshot_label": review.get("selected_snapshot_label"),
        "baseline_content_hash": review.get("baseline_content_hash"),
        "candidate_content_hash": review.get("candidate_content_hash"),
        "candidate_verified": review.get("candidate_verified"),
        "review_required": review.get("review_required", False),
        "promotion_ready": review.get("promotion_ready", False),
        "diff": review.get("diff"),
        "attempt_summary": review.get("attempt_summary"),
        "disclaimer": DISCLAIMER,
    }, out_dir / "review-manifest.json")
    # A concise audit is written SEPARATELY from the verified candidate ZIP, so
    # a reviewer can inspect the outcome without downloading/unpacking the bundle.
    _write_json({
        "status": review.get("status", "changed"),
        "attempted": True,
        "message": "candidate refresh completed and exported",
        "baseline_content_hash": review.get("baseline_content_hash"),
        "candidate_content_hash": review.get("candidate_content_hash"),
        "promotion_ready": review.get("promotion_ready", False),
        "review_required": review.get("review_required", False),
        "bundle_sha256": digest,
        "disclaimer": DISCLAIMER,
    }, out_dir / "refresh-audit.json")
    return report


def _attempt_summary(work: Path) -> Dict[str, List[dict]]:
    """Return the bounded per-source attempt summary from the candidate's
    fetch_attempts.json (and the shared cheap fields), excluding attempt bodies."""
    attempts_path = work / "data" / "raw" / "fetch_attempts.json"
    attempts = fetch_mod._load_json(attempts_path)  # noqa: SLF001
    out: Dict[str, List[dict]] = {}
    for key, entries in sorted(attempts.items()):
        out[key] = [
            {k: e.get(k) for k in ("fetched", "accepted", "error", "detail",
                                   "retrieved_at") if k in e}
            for e in entries[-5:]  # bounded: most recent 5 per source
        ]
    return out


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True,
                        help="run-owned work directory (never the live checkout)")
    parser.add_argument("--baseline-url", required=True,
                        help="trusted baseline snapshot bundle URL")
    parser.add_argument("--baseline-sha256", required=True,
                        help="independently-trusted SHA-256 of the baseline bundle")
    parser.add_argument("--live", action="store_true",
                        help="refresh against live official URLs (network)")
    parser.add_argument("--source-file", action="append", default=[],
                        metavar="KEY=PATH",
                        help="stage an offline official-export fixture for KEY")
    parser.add_argument("--restore-only", action="store_true",
                        help="restore the baseline and stop")
    parser.add_argument("--out", type=Path, default=None,
                        help="output dir for a candidate bundle + review evidence")
    args = parser.parse_args(argv)
    try:
        work = args.work.resolve()
        if work == ROOT or work in ROOT.parents:
            print("error: work directory must not be the live checkout", file=sys.stderr)
            return 2
        restore_rep = restore_baseline(args.baseline_url, args.baseline_sha256, work)
        print(f"[restore] {json.dumps(restore_rep, indent=2)}")
        if args.restore_only:
            return 0
        baseline = work / BASELINE_DIR
        baseline_meta = _read_json(baseline / "data/processed/metadata.json")
        baseline_hash = baseline_meta["content_hash"]

        offline_sources: Dict[str, Path] = {}
        for item in args.source_file:
            key, _, path = item.partition("=")
            if not key or not path:
                print(f"error: invalid --source-file {item!r}", file=sys.stderr)
                return 2
            offline_sources[key] = Path(path)

        # Build a SEPARATE candidate tree from an immutable baseline copy.
        candidate = work / CANDIDATE_DIR
        _copy_tree(baseline, candidate)
        refresh_in_tree(candidate, offline_sources=offline_sources,
                        live=args.live)
        candidate_meta = _read_json(candidate / "data/processed/metadata.json")
        val = validate_candidate(candidate)
        print(f"[candidate-verify] {json.dumps(val, indent=2)}")

        # The DIFF is computed against the immutable baseline BEFORE deciding
        # promotion. On no-change we select the baseline (freshness preserved).
        diff = build_diff(baseline_meta, candidate_meta, baseline, candidate)

        selected = baseline if val["content_hash"] == baseline_hash else candidate
        selected_label = "baseline" if selected == baseline else "candidate"
        result = {
            "status": "no-change" if selected == baseline else "changed",
            "selected_snapshot": str(selected),
            "selected_snapshot_label": selected_label,
            "baseline_content_hash": baseline_hash,
            "candidate_content_hash": val["content_hash"],
            "candidate_verified": val,
            "diff": diff,
            "disclaimer": DISCLAIMER,
        }
        if selected == baseline:
            # no-change: SELECT the immutable baseline; never rewrite its freshness.
            result["selected_snapshot_timestamp"] = baseline_meta["data_updated_at"]
            result["attempt_summary"] = _attempt_summary(candidate)
            result["promotion_ready"] = True
            print("[no-change] candidate is semantically unchanged; selecting baseline")
            print(f"[selected] {selected_label} (unchanged; baseline timestamp "
                  f"{baseline_meta['data_updated_at']})")
            _write_audit(
                work, status="no-change", attempted=True,
                message="candidate refresh completed; selecting immutable baseline",
                baseline_content_hash=baseline_hash,
                candidate_content_hash=val["content_hash"],
                promotion_ready=True,
                detail={"selected_snapshot_label": selected_label})
            _write_json(result, work / "refresh-result.json")
            return 0

        # Changed candidate:
        print(f"[diff] {json.dumps(diff, indent=2)}")
        if diff["review_required"]:
            print("[review] consequential reductions surfaced; candidate requires "
                  "review before promotion", file=sys.stderr)
        result["review_required"] = diff["review_required"]
        result["promotion_ready"] = (not diff["review_required"])
        result["attempt_summary"] = _attempt_summary(candidate)
        _write_audit(
            work, status="changed", attempted=True,
            message=("candidate refresh produced a change"
                     + ("" if result["promotion_ready"] else " requiring review")),
            baseline_content_hash=baseline_hash,
            candidate_content_hash=val["content_hash"],
            promotion_ready=result["promotion_ready"],
            detail={"review_required": diff["review_required"],
                    "promotion_ready": result["promotion_ready"]})
        if args.out:
            export = export_candidate(candidate, args.out, result)
            print(f"[export] {json.dumps(export, indent=2)}")
            result["export"] = export
        _write_json(result, work / "refresh-result.json")
        return 0
    except (ValueError, FileExistsError, KeyError, FileNotFoundError, OSError,
            zipfile.BadZipFile) as exc:
        # A corrupt archive (BadZipFile) is an expected operational failure:
        # return the documented exit 2 with a concise diagnostic, no traceback.
        # Persist a failure audit so the outcome is never silently lost.
        try:
            _write_audit(work, status="failure", attempted=False,
                         message=f"refresh failed: {exc}", detail={"error": str(exc)})
        except Exception:
            # The audit is best-effort; the exit code and stderr are authoritative.
            pass
        print(f"refresh failed: {exc}\n{DISCLAIMER}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
