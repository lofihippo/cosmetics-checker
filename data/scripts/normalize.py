"""Normalize official-export raw files into SQLite + JSONL + metadata.

This is the Phase 1 ingest path. It reads the official export files in
``data/raw/<source>/`` and produces:
  * ``data/processed/eu_reg_lists.jsonl``  (one substance per line)
  * ``data/processed/eu_reg_lists.sqlite`` (queryable normalized store)
  * ``data/processed/metadata.json``       (counts, source hashes, data_updated_at)

Identity reconciliation (honest, no silent drops):
  * A curated identity (Phase 0) is a recognised name with ``determination:
    "unknown"`` and no rules. It is NEVER promoted to a verified record without
    source evidence.
  * Verified rules come only from official export files (CosIng, ECHA).
  * A parsed export row is matched to a curated identity by **CAS / EC first**,
    then by an explicit name/alias mapping. Rows whose CAS/EC conflict with an
    existing identity's CAS/EC are **quarantined** (reported), never silently
    re-attached to the wrong substance.
  * Rules carry a full source manifest: file, sha256, row/reference, retrieval
    status, and applicable dates.

Source manifests:
  * ``data/raw/fetch_manifest.json`` records every fetch attempt (fetched ok,
    accepted, or not-fetched with error).
  * ``data/processed/metadata.json`` ``verified_sources`` records per-source file
    hashes and retrieval status, and each rule records its specific source file
    + hash + row.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from cosing_parser import AnnexMismatchError

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

CURATED = ROOT / "data" / "eu_flags.json"
FETCH_MANIFEST = RAW / "fetch_manifest.json"

PROCESSED_JSONL = PROCESSED / "eu_reg_lists.jsonl"
PROCESSED_SQLITE = PROCESSED / "eu_reg_lists.sqlite"
METADATA = PROCESSED / "metadata.json"

# Per-source-dir canonical citation URL (source-specific, not a shared fallback).
SOURCE_SPECS = {
    "eurlex-1223": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32009R1223",
    "cosing": "https://ec.europa.eu/growth/tools-databases/cosing/",
    "echa-svhc": "https://echa.europa.eu/candidate-list-table",
    "echa-annex-xiv": "https://echa.europa.eu/regulations/reach/authorisation",
    "echa-annex-xvii": "https://echa.europa.eu/regulations/reach/restriction",
}


def _source_url_for(key: str) -> str:
    """Return the canonical citation URL for a source dir key."""
    return SOURCE_SPECS.get(key, SOURCE_SPECS.get("cosing"))


def _rule_source_url(key: str, row: Dict[str, Any]) -> str:
    """Return the source-specific URL for a parsed rule.

    ECHA rules use the ECHA citation URL; CosIng rules use the CosIng homepage.
    """
    if key.startswith("echa-"):
        return SOURCE_SPECS.get(key, SOURCE_SPECS["echa-svhc"])
    if key.startswith("cosing-annex"):
        return SOURCE_SPECS["cosing"]
    return row.get("url") or _source_url_for(key)


def _file_accepted(manifest_entry: Optional[Dict[str, Any]], f: Path) -> "tuple[bool, str]":
    """Decide whether a raw export file may be parsed into verified rules.

    A file is accepted only when its manifest entry says ``accepted==true`` AND
    it records a SHA-256 that matches the actual file. An ``accepted`` entry with
    NO recorded hash is rejected (we never accept trust without verifiable
    integrity), and a hash that disagrees is rejected. Files with no manifest
    (e.g. manually placed exports) are rejected — they need an explicit
    validation/import step.
    """
    if manifest_entry is None:
        return False, "no-manifest"
    if not manifest_entry.get("accepted"):
        return False, "rejected-in-manifest"
    # Integrity is mandatory: an accepted entry must carry a recorded hash.
    mhash = manifest_entry.get("sha256")
    if not mhash:
        return False, "missing-hash"
    if _sha256(f.read_bytes()) != mhash:
        return False, "hash-mismatch"
    return True, "ok"


def _staged_dir(raw_dir: Path, key: str) -> Path:
    """Return the staging dir for ``key`` under ``raw_dir``."""
    return raw_dir / ".incoming" / key


def _staging_manifest_entry(key: str, raw_dir: Path) -> Optional[Dict[str, Any]]:
    """Return a source's entry from the STAGING manifest (validated downloads)."""
    import fetch as fetch_mod
    doc = fetch_mod._load_json(fetch_mod._staging_manifest(raw_dir))
    return doc.get(key)


def _iter_sources(raw_dir: Path) -> List[tuple]:
    """Yield ``(key, source_file_or_dir)`` for every known source.

    Discovers sources from BOTH active source directories and the staging area
    (``.incoming``), deduplicated by key so a source present in both is yielded
    once (with its active dir). This lets a first-time fetch into an empty raw
    tree be imported, not silently skipped.
    """
    keys: List[str] = []
    active = {}
    if raw_dir.exists():
        for d in sorted(raw_dir.iterdir()):
            if not d.is_dir() or d.name == ".incoming":
                continue
            k = d.name
            if _kind_from_dir(k) and k not in active:
                active[k] = d
                keys.append(k)
    # staged keys only (not already active)
    staged_dir = raw_dir / ".incoming"
    if staged_dir.exists():
        for d in sorted(staged_dir.iterdir()):
            if not d.is_dir():
                continue
            k = d.name
            if _kind_from_dir(k) and k not in active:
                active[k] = d
                keys.append(k)
    return [(k, active[k]) for k in sorted(keys)]


def _import_staged_or_active(src_dir: Path, active_dir: Path, key: str,
                             kind: str, source_url: str, raw_dir: Path,
                             manifest_path: Path,
                             ver) -> Optional[bool]:
    """Import a source, staging-first with active-snapshot fallback.

    Returns True if rules were imported from staging (which was then promoted),
    False if rules came from the preserved active snapshot, or None if no usable
    rules came from either (source skipped).

    A candidate staged export that declares a conflicting annex (an expected,
    source-validation failure, e.g. an Annex II file staged under ``cosing-annex-iii``)
    is recorded on ``ver`` (``ver.source_validation_failures``) and does NOT
    abort the refresh — we fall back to the accepted active snapshot instead.
    """
    # --- staged download first ---
    staged_files = _valid_staged_files(key, raw_dir)
    for f in staged_files:
        entry = _staging_manifest_entry(key, raw_dir)
        try:
            rules = _rules_from_file(f, key, kind, source_url, entry, raw_dir)
        except AnnexMismatchError as exc:
            # Expected source-validation failure: record it, do NOT promote, and
            # fall back to the accepted active snapshot. Never catch broadly.
            ver.source_validation_failures.append({
                "source_kind": key,
                "source_file": f.name,
                "source_sha256": _sha256(f.read_bytes()),
                "error": "AnnexMismatchError",
                "detail": str(exc),
                "action": "rejected_candidate_kept_active_coverage",
            })
            continue
        if rules:
            for row in rules:
                ver.add_all([row])
            _promote_staged(f, active_dir, key, entry)
            return True
    # no usable staged rules -> fall back to the preserved ACTIVE snapshot
    # (prevents a header-only / bad staged file from wiping coverage).
    active_files = [f for f in sorted(active_dir.iterdir())
                    if f.suffix.lower() in (".csv", ".tsv", ".txt", ".xml")] \
        if active_dir.exists() else []
    imported_active = False
    for f in active_files:
        entry = _manifest_entry(key, manifest_path)
        rules = _rules_from_file(f, key, kind, source_url, entry, raw_dir)
        if rules:
            for row in rules:
                ver.add_all([row])
            imported_active = True
    return True if imported_active else None


def _valid_staged_files(key: str, raw_dir: Path) -> List[Path]:
    """Return validated staged files for ``key`` (accepted + integrity OK)."""
    staged = _staged_dir(raw_dir, key)
    if not staged.exists():
        return []
    out = []
    for f in sorted(staged.iterdir()):
        if f.suffix.lower() not in (".csv", ".tsv", ".txt", ".xml"):
            continue
        entry = _staging_manifest_entry(key, raw_dir)
        ok, _reason = _file_accepted(entry, f)
        if ok:
            out.append(f)
    return out


def _rules_from_file(f: Path, key: str, kind: str, source_url: str,
                     manifest_entry: Optional[Dict[str, Any]],
                     raw_dir: Path) -> List[Dict[str, Any]]:
    """Parse a single export file into rule rows carrying provenance.

    Returns [] when the file is not accepted (no manifest / hash mismatch /
    rejected) or parses to no usable rules. The hash is recorded so provenance
    is traceable to the exact bytes that were imported.
    """
    if manifest_entry is None:
        return []
    file_ok, reason = _file_accepted(manifest_entry, f)
    if not file_ok:
        return []
    rows = list(_parse_export_file(f, kind, source_url, key))
    if not rows:
        return []
    out = []
    for idx, row in enumerate(rows):
        row["source_kind"] = key
        row["source_file"] = f.name
        row["source_sha256"] = _sha256(f.read_bytes())
        row["row_reference"] = f"{f.name}#{idx + 1}"
        row["entry_number"] = idx + 1
        row["reference_number"] = row.get("reference_number")
        row["fetched"] = bool(manifest_entry.get("fetched"))
        row["accepted"] = file_ok
        row["retrieved_at"] = manifest_entry.get("retrieved_at")
        row["source_url"] = _rule_source_url(key, row)
        out.append(row)
    return out


def _promote_staged(f: Path, active_dir: Path, key: str,
                    manifest_entry: Optional[Dict[str, Any]]) -> None:
    """Promote a successfully-imported staged file to the active snapshot.

    Called only AFTER the staged file's rows have been added to the verified
    index (i.e. the import succeeded). A staged file living under
    ``raw/.incoming/<key>/`` is moved into ``raw/<key>/`` and the ACTIVE
    manifest entry is updated so subsequent runs keep ingesting it. Failed or
    rejected staged files are never promoted.
    """
    staged_dir = f.parent
    if staged_dir == active_dir:
        return  # not staged; already the active snapshot
    try:
        import fetch as fetch_mod
    except Exception:
        return
    # Recover the commitment report from the STAGING manifest (the active
    # manifest must not be touched until the import has actually succeeded).
    staging_manifest = fetch_mod._load_json(fetch_mod._staging_manifest(active_dir.parent))
    report = staging_manifest.get(key)
    if not report:
        return
    report = dict(report)
    report.setdefault("filename", f.name)
    report["sha256"] = _sha256(f.read_bytes())
    report["accepted"] = True
    report["fetched"] = True
    try:
        fetch_mod.commit_source(key, report, out_dir=active_dir.parent)
    except Exception:
        pass


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Curated identities (Phase 0) => recognised names, NO verified rules.
# ---------------------------------------------------------------------------
def _load_curated_identities() -> List[Dict[str, Any]]:
    if not CURATED.exists():
        return []
    doc = json.loads(CURATED.read_text(encoding="utf-8"))
    out = []
    for s in doc.get("substances", []):
        out.append({
            "id": s.get("id"), "name": s.get("name"),
            "aliases": s.get("aliases") or [],
            "cas": _norm_id(s.get("cas")), "ec": _norm_id(s.get("ec")),
            "e_number": s.get("e_number"), "ci": s.get("ci"),
            "determination": "unknown", "rules": [],
        })
    return out


def _is_placeholder_text(v: str) -> bool:
    """Return True if a value is shared placeholder calligraphy (no alphanumeric
    content), e.g. ``-``, ``- / -``, ``n/a`` — used to reject empty glossary /
    identifier fields from becoming identity evidence."""
    s = str(v).strip().lower()
    if not s:
        return True
    return not any(ch.isalnum() for ch in s)


# Delimiters that separate multiple identifiers in a single source cell. The
# real CosIng exports use ``/`` and ``;`` (and occasionally a trailing ``;``)
# to join several identifiers for a grouped entry. We split ONLY on these
# explicit separators — never on spaces/hyphens that are part of one identifier.
_ID_SEPARATORS = re.compile(r"[/;]")
# A bracketed source marker trailing a real identifier (``13463-67-7[1]``,
# ``236-675-5[2]``) denotes which source part the identifier indexes. It is
# not part of the identifier value; we strip a trailing ``[N]`` marker but keep
# the raw value in the source row.
_BRACKET_MARKER = re.compile(r"\[\s*\d+\s*\]\s*$")
# A Colour Index identifier looks like ``CI 77891`` / ``C.I. 77891``.
_CI_RE = re.compile(r"\bC\.?\s*I\.?\s*(\d+)\b", re.IGNORECASE)
_GLOSSARY_SEP = re.compile(r"[,;/]")


def _id_split(value: Any) -> List[str]:
    """Split a raw CAS/EC cell into cleaned identifier tokens.

    Each token is stripped of trailing ``;``/``/`` and a trailing ``[N]`` source
    marker. A token that contains no digit (or is a placeholder like ``-``) is
    NOT an identifier and is dropped. This is a *bounded* normalizer for the
    syntax actually observed in the official exports; it never guesses a token
    from free text.
    """
    if value is None:
        return []
    s = str(value).strip()
    if not s or not any(ch.isdigit() for ch in s):
        return []
    out: List[str] = []
    for part in _ID_SEPARATORS.split(s):
        token = part.strip().strip(";").strip()
        token = _BRACKET_MARKER.sub("", token).strip()
        if not token or not any(ch.isdigit() for ch in token):
            continue
        if token in ("-", "—", "–"):
            continue
        out.append(token)
    return out


def _id_set(value: Any) -> "set[str]":
    """Return the set of cleaned identifier tokens for a raw cell."""
    return set(_id_split(value))


def _norm_id(v: Any) -> Optional[str]:
    """Normalise an identifier (CAS/EC) to a canonical value, or None if absent.

    The real CosIng export uses ``-`` / ``—`` (em-dash) and combinations such as
    ``- / -``, ``-/-``, ``- -``, ``- -- -`` to mean "not specified / not
    applicable"; those are NOT identifiers and never enter the lookup index.

    Real identifiers always contain at least one digit. A cell holding several
    identifiers (``65-85-0 / 532-32-1``) is reduced to a canonical, delimiter-
    joined list with each token cleaned (trailing ``;`` and ``[N]`` markers
    removed). Bounded syntax normalisation only — we never split on space or
    hyphen that belongs to a single identifier.
    """
    tokens = _id_split(v)
    if not tokens:
        return None
    return " / ".join(tokens)


def _norm_id_list(v: Any) -> List[str]:
    """Return the ordered list of cleaned identifier tokens (empty if absent)."""
    return _id_split(v)


def _norm_ci(v: Any) -> Optional[str]:
    """Return a canonical CI identifier from an Annex IV combined cell.

    The colour-index column mixes CI numbers with glossary names; only the
    explicitly-designated ``CI <digits>`` tokens are used as CI evidence. A
    combined cell with no CI token returns None. Multiple CI tokens are joined
    with `` / ``.
    """
    tokens = _ci_extract(v)
    if not tokens:
        return None
    return " / ".join(tokens)


def _new_substance_id(name: str, by_id: Dict[str, Any]) -> str:
    """Return a unique substance id for a new entry (disambiguating duplicates)."""
    base = name.lower().replace(" ", "-") or "unnamed"
    candidate = base
    counter = 2
    while candidate in by_id:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _retain_standalone(substances: List[Dict[str, Any]],
                       cur_index: Dict[str, Any],
                       by_id: Dict[str, Any],
                       verification: dict,
                       report: ReconcileReport,
                       name: str, cas: Optional[str], ec: Optional[str],
                       row: Dict[str, Any],
                       unresolved_candidate: Optional[str] = None,
                       unresolved_reason: str = "") -> str:
    """Retain a source row as its own standalone regulatory entry.

    Used when a row's identity is uncertain (an alias or partial identifier
    overlap reaches a candidate but the association is not proven). The row's
    rule is preserved as its own entry --- never silently merged or dropped ---
    and the proposed (unproven) association is recorded as unresolved.
    """
    glossary = _glossary_alias(row)
    new = {
        "id": _new_substance_id(name, by_id),
        "name": name, "aliases": [glossary] if glossary else [],
        "cas": cas, "ec": ec, "e_number": None,
        "ci": _norm_ci(row.get("ci")),
        "determination": row["status"],
        "rules": [_rule(row)],
        "sources": [row.get("source_kind") or "export"],
    }
    substances.append(new)
    by_id[new["id"]] = new
    if cas:
        cur_index.setdefault(f"cas:{cas}", set()).add(new["id"])
    if ec:
        cur_index.setdefault(f"ec:{ec}", set()).add(new["id"])
    if new["ci"]:
        cur_index.setdefault(f"ci:{new['ci']}", set()).add(new["id"])
    cur_index.setdefault(f"name:{name.lower()}", set()).add(new["id"])
    _register_verification(verification, new, cas, ec, name)
    report.new_created += 1
    report.unresolved_memberships.append({
        "row_name": name, "cas": cas, "ec": ec,
        "official_reference": row.get("reference_number"),
        "annex": row.get("annex"),
        "source_kind": row.get("source_kind"),
        "source_file": row.get("source_file"),
        "row_reference": row.get("row_reference"),
        "unresolved_candidate": unresolved_candidate,
        "reason": unresolved_reason or "identity not proven",
    })
    return new["id"]


def _ci_extract(value: Any) -> List[str]:
    """Extract Colour Index identifiers from an Annex IV combined cell.

    The Annex IV ``Colour index Number / Name of Common Ingredients Glossary``
    cell mixes CI numbers with glossary names (e.g. ``TITANIUM DIOXIDE/CI 77891``,
    ``ZINC OXIDE``). Only the explicitly-designated ``CI <digits>`` tokens are
    taken as CI evidence — the descriptive cell is never treated as a CI
    identifier itself. Order is preserved; duplicates are removed.
    """
    if value is None:
        return []
    s = str(value)
    out: List[str] = []
    seen: "set[str]" = set()
    for m in _CI_RE.finditer(s):
        tok = f"CI {m.group(1)}"
        if tok.lower() not in seen:
            seen.add(tok.lower())
            out.append(tok)
    return out


# Member-enumeration columns. Annex III/V/VI use a separate ``Name of Common
# Ingredients Glossary`` column; Annex IV uses the combined ``Colour index
# Number / Name of Common Ingredients Glossary`` column that also enumerates
# member names. The auxiliary ``Identified INGREDIENTS or substances e.g.``
# column lists related names/CI identifiers but is NOT authoritative member
# enumeration for a group (it may restate the entry's own name and often
# repeats tokens), so it is NOT used to decide group membership.
_GLOSSARY_COLS = (
    "Name of Common Ingredients Glossary",
    "Colour index Number / Name of Common Ingredients Glossary",
)


def _is_ci_token(tok: str) -> bool:
    """Return True if a glossary token is itself a Colour Index identifier
    (e.g. ``CI 77891``), which is identity evidence, not a member name."""
    return bool(_CI_RE.fullmatch(tok.strip()))


def _glossary_names(row: Dict[str, Any]) -> List[str]:
    """Return the explicit member names enumerated in a source row.

    Uses only the source's designated member-enumeration columns
    (``Name of Common Ingredients Glossary``, the Annex IV combined colour-index /
    glossary column, and ``Identified INGREDIENTS or substances e.g.``).
    The ``Chemical name / INN`` column is the group descriptor, not a member
    enumeration, so it is NOT used here. Pure CI identifier tokens (``CI 77891``)
    are identity evidence, not member names, and are excluded. Each token is the
    exact source value (case preserved for provenance, compared case-
    insensitively at match time).
    """
    src = row.get("source_row") or {}
    out: List[str] = []
    for col in _GLOSSARY_COLS:
        val = (src.get(col) or "").strip()
        if not val:
            continue
        for part in _GLOSSARY_SEP.split(val):
            tok = part.replace("\n", " ").strip()
            if not tok or _is_placeholder_text(tok):
                continue
            if _is_ci_token(tok):
                continue
            out.append(tok)
    return out


def _member_name_tokens(row: Dict[str, Any]) -> List[str]:
    """Deduplicated member-name tokens from a source row (lowercased)."""
    seen: "set[str]" = set()
    out: List[str] = []
    for n in _glossary_names(row):
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Administrative rows (e.g. the CosIng "Moved or deleted" placeholder).
# ---------------------------------------------------------------------------
_ADMIN_NAME_MARKERS = (
    "moved or deleted",
)


def _is_admin_row(name: str) -> bool:
    """Return True if an export row is an explicit administrative placeholder
    rather than a real substance entry.

    Only conservative, explicit markers are recognised — we never classify an
    arbitrary unusual substance name as an administrative row. A "Moved or
    deleted" row carries no chemical identity (no real CAS/EC/name) and must
    NOT become an active prohibition/restriction/allowance. Its source
    reference is preserved in the import accounting (skipped_admin_rows) but it
    is excluded from active findings.
    """
    n = (name or "").strip().lower()
    return n in _ADMIN_NAME_MARKERS


# ---------------------------------------------------------------------------
# Export parsing (CosIng + ECHA) into verified-rule records.
# ---------------------------------------------------------------------------
def _load_parser(name: str):
    import sys
    sys.path.insert(0, str(ROOT / "data" / "scripts"))
    __import__(name)
    return sys.modules[name]


def _parse_export_file(path: Path, kind: str, source_url: str,
                       key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse one raw export file into verified-rule records using the right
    parser for its source kind."""
    if path.suffix.lower() in (".csv", ".tsv", ".txt"):
        if kind.startswith("echa-"):
            echa = _load_parser("echa_parser")
            sub = kind.replace("echa-", "")  # svhc / annex-xiv / annex-xvii
            return echa.parse_echa_file(path, sub, source_url)
        if kind == "cosing-annex":
            cos = _load_parser("cosing_parser")
            # The annex (II/III/IV/V/VI) is encoded in the source-dir key
            # (e.g. ``cosing-annex-ii``). The real export's preamble declares it
            # too; pass it explicitly so the parser never has to guess.
            annex_num = None
            if key and key.startswith("cosing-annex-"):
                annex_num = key[len("cosing-annex-"):]
            return cos.parse_export_file(path, source_url, annex=annex_num)
    return []


# ---------------------------------------------------------------------------
# Reconciliation: match an export rule to a curated identity (or add new).
# ---------------------------------------------------------------------------
class ReconcileReport:
    """Tracks identity reconciliation outcomes for audit & tests."""

    def __init__(self) -> None:
        self.matched_by_identifier = 0
        self.matched_by_name = 0
        self.new_created = 0
        self.quarantined: List[Dict[str, Any]] = []
        self.skipped_admin: List[Dict[str, Any]] = []
        self.group_links: List[Dict[str, Any]] = []
        self.member_links: List[Dict[str, Any]] = []
        self.unresolved_memberships: List[Dict[str, Any]] = []


# Form-qualifying markers that indicate a distinct physical form of a substance
# (e.g. zinc oxide vs zinc oxide (nano)). These are bounded, documented markers;
# we never generalise beyond them.
_FORM_QUALIFIER_MARKERS = ("(nano)",)


def _is_form_qualified_name(name: str) -> bool:
    """Return True if a source name carries an explicit form qualifier."""
    if not name:
        return False
    low = name.lower()
    return any(marker in low for marker in _FORM_QUALIFIER_MARKERS)


def _is_group_or_form_row(row: Dict[str, Any]) -> bool:
    """Return True if a source row denotes a *group* or *form* entry.
    A row is a group/form entry when it explicitly enumerates MORE than one
    member NAME (via the designated glossary columns), OR carries MORE than one
    CAS/EC identifier token. Such a row represents either a group of distinct
    members (e.g. ``Benzoic acid and its sodium salt``) or a colourant form with
    several CAS entries (e.g. ``Iron Oxide red``). Per the conservative policy it
    is NOT auto-attached to an existing identity by overlapping identifier
    overlap alone; it is kept as its own regulatory entry and its member names
    are linked explicitly.

    A single-identifier, single-name row is an ordinary substance and is
    reconciled by exact identifier/name evidence as before.
    """
    member_tokens = _group_member_tokens(row)
    if len(member_tokens) > 1:
        return True
    # Multi-identifier within a single dimension (not one CAS + one EC, which
    # is a normal single substance): a colourant form with several CAS entries
    # (e.g. ``Iron Oxide red``) or a nano entry with several indexed ids.
    if len(_id_split(row.get("cas"))) > 1 or len(_id_split(row.get("ec"))) > 1:
        return True
    # A form-qualified name (e.g. ``Zinc oxide (nano)``) is a distinct form even
    # when it shares the identifier of the bulk form; it must not collapse into
    # the bare-form identity.
    if _is_form_qualified_name((row.get("name") or "")):
        return True
    return False


def _is_group_form_candidate(sub: Dict[str, Any]) -> bool:
    """Return True if a substance is a group/form/multi-identifier entry.

    Used by reconciliation to distinguish a genuine identity contradiction
    (against a simple single-identifier substance) from form/group-sharing
    (against an entry that itself carries several identifiers or member names),
    in which case an identifier overlap is candidate evidence, not proof of the
    same identity.
    """
    if len(_id_split(sub.get("cas"))) > 1 or len(_id_split(sub.get("ec"))) > 1:
        return True
    # A curated or parsed substance that carries a multi-value CI is also a form
    # / grouped colour entry.
    if len(_ci_extract(sub.get("ci"))) > 1:
        return True
    # A colourant with a single Colour Index identifier is also a form: the same
    # CI colour exists across several pigment/lake/salt CAs-fnumbered entries, so
    # an overlapping CI with a differing CAS is form-sharing, not same identity.
    if _ci_extract(sub.get("ci")):
        return True
    return False


def _reconcile(substances: List[Dict[str, Any]],
               cur_index: Dict[str, Any],
               verification: dict,
               by_id: Optional[Dict[str, Any]] = None) -> ReconcileReport:
    """Merge verified rules from exports into the curated identity list.

    ``cur_index`` maps ``cas:<id>`` / ``ec:<id>`` / ``name:<name>`` to a *set*
    of substance **ids** (an identifier may be shared by several curated
    identities). ``by_id`` maps id -> substance dict. ``verification`` tracks
    already-verified exports.

    For each export row we collect ALL candidate matches from identifier and
    name/alias evidence. If the evidence is consistent (all evidence points to
    one substance) we attach the rule; if ambiguous/conflicting we **quarantine**
    the row instead of silently choosing the first.
    """
    if by_id is None:
        by_id = {s["id"]: s for s in substances}
    report = ReconcileReport()
    for row in verification.raw:
        name = (row.get("name") or "").strip()
        cas = _norm_id(row.get("cas"))
        ec = _norm_id(row.get("ec"))
        ci = _norm_id(row.get("ci"))
        if not name and not cas and not ci:
            continue

        # An explicit administrative placeholder (e.g. "Moved or deleted") is
        # NOT a substance entry: it must not create an active prohibition,
        # restriction or allowance. Its source reference is preserved in the
        # import accounting (skipped_admin) but it is excluded as a finding.
        if _is_admin_row(name):
            report.skipped_admin.append({
                "name": name, "cas": cas, "ec": ec,
                "source_kind": row.get("source_kind"),
                "source_file": row.get("source_file"),
                "row_reference": row.get("row_reference"),
                "official_reference": row.get("reference_number"),
                "annex": row.get("annex"),
            })
            continue

        # A group or form entry (multiple member names OR multiple CAS/EC) is
        # kept as its own regulatory entry; it is NOT auto-attached to an
        # existing identity by identifier overlap alone. Its member names are
        # linked explicitly in the group-linking pass. This preserves form and
        # nano/non-nano distinctions instead of collapsing them.
        if _is_group_or_form_row(row):
            # If the row ALSO matches a curated identity by its exact, full,
            # unambiguous identifier set AND has a single member name, we still
            # keep it standalone (conservative). Otherwise create the entry.
            glossary = _glossary_alias(row)
            new = {
                "id": _new_substance_id(name, by_id),
                "name": name, "aliases": [glossary] if glossary else [],
                "cas": cas, "ec": ec, "e_number": None,
                "ci": _norm_ci(row.get("ci")),
                "determination": row["status"],
                "rules": [_rule(row)],
                "sources": [row.get("source_kind") or "export"],
            }
            substances.append(new)
            by_id[new["id"]] = new
            if cas:
                cur_index.setdefault(f"cas:{cas}", set()).add(new["id"])
            if ec:
                cur_index.setdefault(f"ec:{ec}", set()).add(new["id"])
            if new["ci"]:
                cur_index.setdefault(f"ci:{new['ci']}", set()).add(new["id"])
            cur_index.setdefault(f"name:{name.lower()}", set()).add(new["id"])
            _register_verification(verification, new, cas, ec, name)
            report.group_links.append({
                "group_id": new["id"], "group_name": name,
                "official_reference": row.get("reference_number"),
                "annex": row.get("annex"),
                "source_kind": row.get("source_kind"),
                "source_file": row.get("source_file"),
                "row_reference": row.get("row_reference"),
            })
            report.new_created += 1
            continue

        # Gather candidate id-sets from every piece of evidence.
        id_candidates: Set[str] = set()
        if cas:
            id_candidates |= _candidate_ids(cur_index, f"cas:{cas}")
        if ec:
            id_candidates |= _candidate_ids(cur_index, f"ec:{ec}")
        if ci:
            id_candidates |= _candidate_ids(cur_index, f"ci:{ci}")

        # Determine whether the row matched a candidate by its exact CANONICAL
        # name (strong evidence) versus only by an alias or an identifier.
        name_candidates: Set[str] = set()
        exact_name_match: Optional[str] = None
        if name:
            low_name = name.lower()
            # An exact canonical-name match hits a substance whose registry NAME
            # equals the row name. (Aliases are ALSO indexed under ``name:``, so
            # the ``name:`` index alone cannot distinguish an alias from the
            # canonical name; check the substance's own ``name`` field.)
            for sid, sub in by_id.items():
                if (sub.get("name") or "").strip().lower() == low_name:
                    exact_name_match = sid
                    break
            canon_sids = _candidate_ids(cur_index, f"name:{low_name}")
            name_candidates |= canon_sids
        for alias_key in _find_alias_keys(name, cur_index):
            name_candidates |= _candidate_ids(cur_index, alias_key)

        # --- Exact canonical-name match (strong evidence). ---
        # A contradiction against the registry name's own identifiers is a
        # genuine conflict (e.g. a row that says "Zinc pyrithione" but has a
        # different CAS): quarantine (ambiguous/conflicting) rather than attach.
        if exact_name_match is not None:
            target = by_id.get(exact_name_match)
            if target is None:
                continue
            conflict, why = _identifier_conflict(target, cas, ec, ci)
            if conflict:
                report.quarantined.append({
                    "name": name, "cas": cas, "ec": ec,
                    "reason": f"name/identifier mismatch: {why}",
                    "name_candidate": exact_name_match,
                })
                continue
            report.matched_by_name += 1
            _attach_rule(target, row)
            _register_verification(verification, target, cas, ec, name)
            continue

        # --- Alias or identifier match (weaker evidence). ---
        # A single unambiguous candidate whose identifiers FULLY agree with the
        # row is attached. Otherwise (partial overlap, or an alias that reaches a
        # broad/group identity) the identity is uncertain --- we retain the row
        # as its own standalone entry and record the proposed association as
        # unresolved rather than silently merging or dropping it.
        if name_candidates and not id_candidates:
            # Alias-only match. If exactly one alias candidate and identifiers
            # fully agree, attach; else retain standalone + unresolved.
            chosen = next(iter(name_candidates)) if len(name_candidates) == 1 else None
            if chosen is not None:
                target = by_id.get(chosen)
                if target is not None:
                    conflict, why = _identifier_conflict(target, cas, ec, ci)
                    if not conflict:
                        report.matched_by_name += 1
                        _attach_rule(target, row)
                        _register_verification(verification, target, cas, ec, name)
                        continue
                    # Alias reached a candidate but identifiers disagree: the
                    # association is uncertain (e.g. a colourant form matching a
                    # broad alias) -> retain standalone.
                    _retain_standalone(substances, cur_index, by_id, verification,
                                       report, name, cas, ec, row,
                                       unresolved_candidate=chosen,
                                       unresolved_reason="alias/identifier overlap not proven")
                    continue
            report.quarantined.append({
                "name": name, "cas": cas, "ec": ec,
                "reason": "ambiguous name (multiple substances)",
                "name_candidates": sorted(name_candidates),
            })
            continue

        # Identifier-only match.
        if id_candidates:
            if len(id_candidates) == 1:
                target = by_id.get(next(iter(id_candidates)))
                if target is not None:
                    conflict, why = _identifier_conflict(target, cas, ec, ci)
                    if conflict:
                        # A conflict against a simple single-identifier substance
                        # is a GENUINE identity contradiction (e.g. CAS uniquely
                        # identifies Zinc pyrithione but the row carries a
                        # different EC) -> quarantine (ambiguity rejection).
                        # A conflict against a GROUP / multi-identifier / form
                        # entry is form-sharing (e.g. bulk Gold vs Gold (nano)),
                        # not proof of same identity -> retain standalone.
                        if _is_group_form_candidate(target):
                            _retain_standalone(
                                substances, cur_index, by_id, verification,
                                report, name, cas, ec, row,
                                unresolved_candidate=next(iter(id_candidates)),
                                unresolved_reason=f"form/group identifier overlap not proven: {why}")
                        else:
                            report.quarantined.append({
                                "name": name, "cas": cas, "ec": ec,
                                "reason": f"identifier conflict: {why}",
                                "id_candidates": sorted(id_candidates),
                            })
                        continue
                    report.matched_by_identifier += 1
                    _attach_rule(target, row)
                    _register_verification(verification, target, cas, ec, name)
                    continue
            report.quarantined.append({
                "name": name, "cas": cas, "ec": ec,
                "reason": "ambiguous identifier (multiple substances)",
                "id_candidates": sorted(id_candidates),
            })
            continue

        # No evidence -> create a new substance carrying the verified rule.
        glossary = _glossary_alias(row)
        new = {
            "id": _new_substance_id(name, by_id),
            "name": name, "aliases": [glossary] if glossary else [],
            "cas": cas, "ec": ec, "e_number": None, "ci": _norm_ci(row.get("ci")),
            "determination": row["status"],
            "rules": [_rule(row)],
            "sources": [row.get("source_kind") or "export"],
        }
        substances.append(new)
        by_id[new["id"]] = new
        if cas:
            cur_index.setdefault(f"cas:{cas}", set()).add(new["id"])
        if ec:
            cur_index.setdefault(f"ec:{ec}", set()).add(new["id"])
        if ci:
            cur_index.setdefault(f"ci:{ci}", set()).add(new["id"])
        cur_index.setdefault(f"name:{name.lower()}", set()).add(new["id"])
        _register_verification(verification, new, cas, ec, name)
        report.new_created += 1
    return report


def _candidate_ids(cur_index: Dict[str, Any], key: str) -> Set[str]:
    """Return the set of substance ids a cur_index key maps to."""
    val = cur_index.get(key)
    if val is None:
        return set()
    if isinstance(val, set):
        return set(val)
    return {val}


# ---------------------------------------------------------------------------
# Explicit source-backed group membership (Part A).
# ---------------------------------------------------------------------------
# A group entry is a source row that enumerates member names (via the
# designated ``Name of Common Ingredients Glossary`` / ``Identified INGREDIENTS``
# columns) and/or carries multiple identifiers. We only link by an *explicit*
# member-name token — never by assuming CAS/EC/name lists align by position.
def _group_member_tokens(row: Dict[str, Any]) -> List[str]:
    """Explicit member-name tokens for a source row (lowercased, deduped)."""
    return _member_name_tokens(row)


def _group_id_tokens(row: Dict[str, Any]) -> "set[str]":
    """Explicit identifier tokens for a source row (CAS/EC, cleaned)."""
    out: "set[str]" = set()
    for t in _id_split(row.get("cas")):
        out.add(t)
    for t in _id_split(row.get("ec")):
        out.add(t)
    return out


def _link_group_memberships(substances: List[Dict[str, Any]],
                            cur_index: Dict[str, Any],
                            by_id: Dict[str, Any],
                            report: ReconcileReport) -> None:
    """Link explicit source-backed group members to member substances.

    Runs AFTER reconciliation so both the group substance and the member
    identities exist. Group classification is consistent with
    ``_is_group_or_form_row``: a row is linked as a GROUP only when it
    explicitly enumerates **more than one distinct member name** in the
    authoritative glossary columns. A single-member, single-identifier row is
    an ordinary substance, not a group (so it never links to itself).

    For each group member name we resolve against the identity index:
      * exactly ONE identity != the group's own id -> a member link;
      * the group's own id (self) -> SKIPPED (no self-membership),
        recorded as unresolved to make it explicit;
      * multiple identities -> unresolved (ambiguous);
      * no identity -> unresolved (recognised but not declared).

    The link is bound to the specific source rule (source_kind + official
    reference + row_reference) that the membership comes from, so a group entry
    that records a link on one rule does NOT inherit an unrelated rule stored on
    the same record (see ``Registry.group_rules_for``).
    """
    seen_group_links: "set[tuple]" = set()
    for sub in substances:
        gid = sub.get("id") or sub.get("name")
        for r in sub.get("rules", []):
            raw = r.get("raw") or {}
            srow = raw.get("source_row") or {}
            member_tokens = _group_member_tokens({"source_row": srow})
            # Only a genuine distinct-member group is member-linked.
            if len(member_tokens) <= 1:
                continue
            source_key = (str(raw.get("source_kind") or ""),
                          str(raw.get("official_reference") or raw.get("reference_number") or ""),
                          str(raw.get("row_reference") or ""))
            g_ref = {
                "group_id": gid,
                "group_name": sub.get("name"),
                "official_reference": raw.get("official_reference"),
                "reference_number": raw.get("reference_number"),
                "annex": r.get("annex"),
                "source_kind": raw.get("source_kind"),
                "source_file": raw.get("source_file"),
                "row_reference": raw.get("row_reference"),
                "source_url": r.get("source_url"),
                "source_key": source_key,
            }
            for tok in member_tokens:
                ids = _candidate_ids(cur_index, f"name:{tok}")
                ids = ids - {gid}
                if not ids:
                    report.unresolved_memberships.append({
                        "group_id": gid, "member_name": tok,
                        "reason": ("member name resolves only to the group itself "
                                   "(self-link) or not found among identities"),
                        "group": g_ref,
                    })
                    continue
                if len(ids) > 1:
                    report.unresolved_memberships.append({
                        "group_id": gid, "member_name": tok,
                        "reason": "ambiguous member name (multiple identities)",
                        "member_candidates": sorted(ids),
                        "group": g_ref,
                    })
                    continue
                member_id = next(iter(ids))
                member = by_id.get(member_id)
                if member is None:
                    continue
                link_key = (gid, member_id, *source_key)
                if link_key in seen_group_links:
                    continue
                seen_group_links.add(link_key)
                existing = member.setdefault("member_of", [])
                already = any(e.get("group_id") == gid
                             and e.get("source_key") == source_key
                             for e in existing)
                if not already:
                    existing.append(dict(g_ref, member_name=tok))
                report.member_links.append({
                    "group_id": gid, "member_id": member_id,
                    "member_name": tok,
                    "official_reference": raw.get("official_reference"),
                    "annex": r.get("annex"),
                    "source_kind": raw.get("source_kind"),
                    "source_file": raw.get("source_file"),
                    "row_reference": raw.get("row_reference"),
                })


def _identifier_conflict(target: Dict[str, Any], cas: Optional[str],
                         ec: Optional[str], ci: Optional[str] = None) -> "tuple[bool, str]":
    """Return (True, reason) if the row's CAS/EC/CI conflicts with the substance's
    stored identifiers, else (False, '')."""
    if cas and target.get("cas") and cas != target["cas"]:
        return True, f"cas {cas} != {target['cas']}"
    if ec and target.get("ec") and ec != target["ec"]:
        return True, f"ec {ec} != {target['ec']}"
    if ci and target.get("ci") and ci != target["ci"]:
        return True, f"ci {ci} != {target['ci']}"
    return False, ""


def _index_keys(sub: Dict[str, Any]) -> List[str]:
    """Return the cur_index keys for a substance (identity name + aliases + ids)."""
    keys = []
    if sub.get("cas"):
        keys.append(f"cas:{sub['cas']}")
    if sub.get("ec"):
        keys.append(f"ec:{sub['ec']}")
    if sub.get("ci"):
        keys.append(f"ci:{sub['ci']}")
    if sub.get("name"):
        keys.append(f"name:{sub['name'].lower()}")
    for a in (sub.get("aliases") or []):
        if a:
            keys.append(f"name:{a.lower()}")
    return keys


def _find_alias_keys(name: str, cur_index: Dict[str, Any]) -> List[str]:
    """Return the cur_index ``name:`` keys that resolve to ``name``.

    Aliases are already indexed as ``name:<alias>`` (see _index_keys), so a
    direct key lookup covers both the canonical name and any alias.
    """
    key = f"name:{name.lower()}"
    return [key] if key in cur_index else []


def _register_verification(ver, target, cas, ec, name) -> None:
    if cas:
        ver.by_cas[cas] = target
    if ec:
        ver.by_ec[ec] = target
    ver.by_name[name.lower()] = target


class _VerificationIndex:
    def __init__(self) -> None:
        self.by_cas: Dict[str, Dict] = {}
        self.by_ec: Dict[str, Dict] = {}
        self.by_name: Dict[str, Dict] = {}
        self.raw: List[Dict[str, Any]] = []
        # Expected source-validation failures (e.g. an accepted staged export
        # that declares a conflicting annex) are recorded, not raised; the
        # pipeline falls back to the active snapshot.
        self.source_validation_failures: List[Dict[str, Any]] = []

    def add_all(self, rows: List[Dict[str, Any]]) -> None:
        self.raw.extend(rows)


def _rule(row: Dict[str, Any]) -> Dict[str, Any]:
    # ``conditions`` is the backward-compatible summary string; the full
    # structured Annex III fragments are preserved in ``raw.conditions_list``
    # (and ``raw.source_row``) so nothing is lost.
    cond_value = row.get("conditions")
    return {
        "status": row["status"],
        "annex": row.get("annex"),
        "regulation": row.get("regulation"),
        "source_url": row.get("source_url"),
        "source_updated_at": row.get("source_updated_at"),
        "conditions": cond_value,
        "resolution": row.get("resolution"),
        "effective_date": row.get("date_included") or row.get("effective_date"),
        "raw": {
            "source_kind": row.get("source_kind"),
            "source_file": row.get("source_file"),
            "source_sha256": row.get("source_sha256"),
            "row_reference": row.get("row_reference"),
            "entry_number": row.get("entry_number"),
            "reference_number": row.get("reference_number"),
            "official_reference": row.get("reference_number"),
            "fetched": row.get("fetched"),
            "accepted": row.get("accepted"),
            "retrieved_at": row.get("retrieved_at"),
            "conditions_list": row.get("conditions_list"),
            # The COMPLETE source row (all columns, including unrecognised ones)
            # so nothing is lost as the export schema evolves.
            "source_row": row.get("source_row"),
        },
    }


_STRONGEST = {"unknown": 0, "allowed_with_conditions": 1, "restricted": 2, "banned": 3}


def _glossary_alias(row: Dict[str, Any]) -> Optional[str]:
    """Return the export's explicit 'Name of Common Ingredients Glossary' alias.

    The source export itself declares this exact name for the substance; we add
    it as a narrowly-sourced alias so a formula may resolve the substance by its
    glossary INCI name. Returns None for a placeholder/empty value.
    """
    glossary = ((row.get("source_row") or {}).get("Name of Common Ingredients Glossary") or "")
    glossary = (glossary or "").strip()
    if not glossary or _is_placeholder_text(glossary):
        return None
    return glossary


def _attach_rule(target: Dict[str, Any], row: Dict[str, Any]) -> None:
    target["rules"].append(_rule(row))
    # Preserve a Colour Index identifier the export declares (Annex IV colorants)
    # as substance identity evidence, if known and not already present/conflicting.
    ci = _norm_id(row.get("ci"))
    if ci and not target.get("ci"):
        target["ci"] = ci
    # determination must reflect the STRONGEST rule across ALL of a substance's
    # rules (banned > restricted > allowed_with_conditions > unknown), never the
    # last-attached rule. This keeps an Annex III restriction from downgrading an
    # Annex II banned headline, and makes the result order-independent.
    current = target.get("determination", "unknown")
    incoming = row["status"]
    if _STRONGEST.get(incoming, 0) > _STRONGEST.get(current, 0):
        target["determination"] = incoming
    src = row.get("source_kind") or "export"
    if src not in target.setdefault("sources", []):
        target["sources"].append(src)
    # preserve aliases from curated identity (already present)
    if row.get("name") and row["name"].strip().lower() not in [
            a.lower() for a in target.get("aliases", [])]:
        target["aliases"].append(row["name"])
    # Add the export's explicit 'Name of Common Ingredients Glossary' as a
    # narrowly-sourced alias (the export itself declares this exact name for the
    # substance). Only when it is a real value (not a placeholder).
    glossary = _glossary_alias(row)
    if glossary and glossary.lower() not in [str(a).lower() for a in target.get("aliases", [])]:
        target["aliases"].append(glossary)


# ---------------------------------------------------------------------------
# Build the processed outputs.
# ---------------------------------------------------------------------------
def build_processed(output_dir: Optional[Path] = None,
                    inputs_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Assemble registry + provenance, writing JSONL/SQLite/meta.

    ``inputs_dir`` defaults to ``data/raw``; ``output_dir`` defaults to
    ``data/processed``. Both may be overridden so tests never touch the
    workspace artifacts.
    """
    out_dir = Path(output_dir) if output_dir else PROCESSED
    raw_dir = Path(inputs_dir) if inputs_dir else RAW

    curated = _load_curated_identities()
    cur_index: Dict[str, Any] = {}
    by_id: Dict[str, Any] = {}
    for sub in curated:
        by_id[sub["id"]] = sub
        for key in _index_keys(sub):
            cur_index.setdefault(key, set()).add(sub["id"])

    # Gather verified rules from every raw export.
    ver = _VerificationIndex()
    # Discover sources from BOTH active source dirs and the staging area
    # (``.incoming``), so a first-time fetch into an empty raw tree is imported.
    for key, src_dir in _iter_sources(raw_dir):
        kind = _kind_from_dir(key)
        if kind is None:
            continue
        source_url = _source_url_for(key)
        manifest_path = raw_dir / "fetch_manifest.json"
        active_dir = raw_dir / key
        # 1) Prefer a freshly-downloaded VALIDATED staged file; promote it only
        #    AFTER its import yields rules.
        # 2) If no staged file yields rules (e.g. a header-only export that
        #    passes validation but parses to nothing), FALL BACK to the preserved
        #    active snapshot so a bad refresh never wipes coverage.
        bits = _import_staged_or_active(
            src_dir, active_dir, key, kind, source_url,
            raw_dir, manifest_path, ver)
        if bits is None:
            # no usable rules from either staging or active -> skip this source.
            continue

    substances = list(curated)
    report = _reconcile(substances, cur_index, ver, by_id=by_id)
    # Link explicit source-backed group memberships (Part A) after all rows are
    # reconciled so both group and member identities exist.
    _link_group_memberships(substances, cur_index, by_id, report)

    counts = _counts(substances)
    sources = _source_provenance(raw_dir)
    content_hash = compute_content_hash(substances, sources)

    # Committed snapshot provenance: the raw file SHA-256s + acceptance state.
    # ``metadata.json`` is committed (the raw files themselves are gitignored),
    # so this is the durable source-of-record pointer used by CI to detect a
    # failed refresh wiping coverage.
    snapshot = sources

    metadata = {
        "title": "EU regulatory substance registry -- Phase 1 normalized",
        "version": "0.2.0",
        "data_updated_at": _now_iso(),
        "content_hash": content_hash,
        "content_hash_version": CONTENT_HASH_VERSION,
        "count_substances": len(substances),
        "count_verified_rules": counts["verified"],
        "count_identity_only": counts["identity_only"],
        "count_quarantined": len(report.quarantined),
        "source_validation_failures": getattr(ver, "source_validation_failures", []),
        "reconcile": {
            "matched_by_identifier": report.matched_by_identifier,
            "matched_by_name": report.matched_by_name,
            "new_created": report.new_created,
            "quarantined": report.quarantined,
            "skipped_admin_rows": report.skipped_admin,
            "group_links": report.group_links,
            "member_links": report.member_links,
            "unresolved_memberships": report.unresolved_memberships,
            "count_skipped_admin": len(report.skipped_admin),
            "count_member_links": len(report.member_links),
            "count_unresolved_memberships": len(report.unresolved_memberships),
        },
        "verified_sources": sources,
        "snapshot": snapshot,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_out = out_dir / "eu_reg_lists.jsonl"
    sqlite_out = out_dir / "eu_reg_lists.sqlite"
    meta_out = out_dir / "metadata.json"
    _write_jsonl(substances, jsonl_out)
    _write_sqlite(substances, metadata, sqlite_out)
    meta_out.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    return metadata


def _kind_from_dir(key: str) -> Optional[str]:
    if key.startswith("cosing-annex"):
        return "cosing-annex"
    if key in ("echa-svhc",):
        return "echa-svhc"
    if key in ("echa-annex-xiv",):
        return "echa-annex-xiv"
    if key in ("echa-annex-xvii",):
        return "echa-annex-xvii"
    if key in ("eurlex-1223", "eurlex-1333"):
        # legal-text XML; recorded for provenance but not parsed into rules.
        return "legal-text"
    return None


def _manifest_entry(key: str, manifest_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    mpath = manifest_path or FETCH_MANIFEST
    if not mpath.exists():
        return None
    try:
        doc = json.loads(mpath.read_text(encoding="utf-8"))
        return doc.get(key)
    except (json.JSONDecodeError, OSError):
        return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _counts(substances: List[Dict[str, Any]]) -> Dict[str, int]:
    verified = sum(1 for s in substances
                   if s.get("determination", "unknown") != "unknown")
    return {"verified": verified, "identity_only": len(substances) - verified}


CONTENT_HASH_VERSION = "3"


# versioned content-hash contracts:
#   "0" — earliest contract (single-annex Phase-1 snapshots, e.g. annex-ii.zip):
#         no member_of AND rules facts omit source_row/conditions_list.
#   "1" — Annex II-III: source_row/conditions_list included, no member_of (e.g.
#         annex-ii-iii-before-iv-vi.zip).
#   "2" — group-membership contract: member_of included, NO version marker (the
#         "before-correct" and first corrected snapshots from the prior milestone).
#   "3" — current contract: member_of included + an explicit "hash_version" marker.
_CONTENT_HASH_VERSIONS = ("0", "1", "2", "3")


def compute_content_hash(substances: List[Dict[str, Any]],
                         sources: Dict[str, Any],
                         hash_version: str = CONTENT_HASH_VERSION) -> str:
    """Deterministic hash of the normalized content (timestamp-independent).

    ``hash_version`` selects the schema contract. Only the known versions are
    accepted; an unknown version raises (we never silently reinterpret a hash).
    """
    if hash_version not in _CONTENT_HASH_VERSIONS:
        raise ValueError(f"unsupported content-hash version: {hash_version!r}")
    import hashlib as _h
    facts = []
    for s in sorted(substances, key=lambda x: (x.get("name") or "", x.get("cas") or "")):
        # exclude only transient timestamps (data_updated_at/retrieved_at).
        fact = {
            "id": s.get("id"),
            "name": s.get("name"),
            "aliases": sorted(s.get("aliases") or []),
            "cas": s.get("cas"),
            "ec": s.get("ec"),
            "e_number": s.get("e_number"),
            "ci": s.get("ci"),
            "determination": s.get("determination"),
            "sources": sorted(s.get("sources") or []),
            "rules": [
                {"status": r.get("status"), "annex": r.get("annex"),
                 "regulation": r.get("regulation"),
                 "source_url": r.get("source_url"),
                 "conditions": r.get("conditions"),
                 "resolution": r.get("resolution"),
                 "effective_date": r.get("effective_date"),
                 "source_sha256": (r.get("raw") or {}).get("source_sha256"),
                 "row_reference": (r.get("raw") or {}).get("row_reference"),
                 "reference_number": (r.get("raw") or {}).get("reference_number"),
                 "accepted": (r.get("raw") or {}).get("accepted"),
                 "source_row": (r.get("raw") or {}).get("source_row"),
                 "conditions_list": (r.get("raw") or {}).get("conditions_list")}
                for r in sorted(s.get("rules", []),
                                key=lambda r: (r.get("annex") or "", r.get("status") or ""))
            ],
        }
        # Version 0 (earliest) rules facts omitted source_row + conditions_list.
        if hash_version == "0":
            for fr in fact["rules"]:
                fr.pop("source_row", None)
                fr.pop("conditions_list", None)
        # Versions 2 and 3 include the group-membership links.
        if hash_version in ("2", "3"):
            fact["member_of"] = sorted(s.get("member_of") or [],
                                       key=lambda m: (str(m.get("group_id") or ""),
                                                      str(m.get("official_reference") or ""),
                                                      str(m.get("row_reference") or "")))
        facts.append(fact)
    src = {k: {f: (v["sha256"] if isinstance(v, dict) else v)
                for f, v in (info.get("files") or {}).items()}
           for k, info in sorted(sources.items())}
    # Version 1 and 2 match their original (unversioned) byte-for-byte contracts
    # so legacy snapshots verify; only version 3 adds the hash-version marker.
    payload = {"substances": facts, "sources": src}
    if hash_version >= "3":
        payload["hash_version"] = hash_version
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return _h.sha256(blob.encode("utf-8")).hexdigest()


def _source_provenance(raw_dir: Path) -> Dict[str, Any]:
    manifest_path = raw_dir / "fetch_manifest.json"
    report = {}
    for d in sorted(raw_dir.iterdir()) if raw_dir.exists() else []:
        if not d.is_dir():
            continue
        kind = _kind_from_dir(d.name)
        if kind is None:
            continue  # not a recognized source export dir
        files = {}
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".csv", ".tsv", ".txt", ".xml"):
                continue
            files[f.name] = {
                "sha256": _sha256(f.read_bytes()),
                "byte_len": f.stat().st_size,
            }
        manifest_entry = _manifest_entry(d.name, manifest_path)
        report[d.name] = {
            "kind": kind,
            "files": files,
            "present": bool(files),
            "fetched": bool(manifest_entry and manifest_entry.get("fetched")),
            "accepted": bool(manifest_entry and manifest_entry.get("accepted")),
            "retrieval_error": manifest_entry.get("error") if manifest_entry else None,
        }
    return report


def _write_jsonl(substances: List[Dict[str, Any]], out: Optional[Path] = None) -> None:
    out = out or PROCESSED_JSONL
    with out.open("w", encoding="utf-8") as fh:
        for s in substances:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")


def _write_sqlite(substances: List[Dict[str, Any]], metadata: Dict[str, Any],
                  out: Optional[Path] = None) -> None:
    out = out or PROCESSED_SQLITE
    if out.exists():
        out.unlink()
    con = sqlite3.connect(str(out))
    con.executescript("""
        CREATE TABLE substances (
            id TEXT PRIMARY KEY, name TEXT, aliases TEXT, cas TEXT, ec TEXT,
            e_number TEXT, ci TEXT, determination TEXT, sources TEXT, member_of TEXT
        );
        CREATE TABLE rules (
            substance_id TEXT, status TEXT, annex TEXT, regulation TEXT,
            source_url TEXT, source_updated_at TEXT, conditions TEXT,
            resolution TEXT, effective_date TEXT, raw TEXT
        );
        CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT);
    """)
    for s in substances:
        con.execute(
            "INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s["id"], s["name"], json.dumps(s.get("aliases") or []), s.get("cas"),
             s.get("ec"), s.get("e_number"), s.get("ci"),
             s.get("determination", "unknown"), json.dumps(s.get("sources") or []),
             json.dumps(s.get("member_of") or [])))
        for r in s.get("rules", []):
            con.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                (s["id"], r.get("status"), r.get("annex"), r.get("regulation"),
                 r.get("source_url"), r.get("source_updated_at"), r.get("conditions"),
                 r.get("resolution"), r.get("effective_date"),
                 json.dumps(r.get("raw") or {})))
    for k, v in metadata.items():
        con.execute("INSERT INTO metadata VALUES (?,?)", (k, json.dumps(v)))
    con.commit()
    con.close()


if __name__ == "__main__":
    import sys
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    meta = build_processed(output_dir=out_dir)
    print(json.dumps(meta, indent=2))
