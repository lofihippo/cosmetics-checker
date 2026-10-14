"""Shared release policy: which EU source lists are deliberately excluded.

Regulatory information, not legal advice.

This is a single source of truth for the CosIng-focused release boundary. It is
imported by:

* the runtime coverage builders (:mod:`eu_ingredient_linter` and the formula
  evaluator) so ECHA is never presented as available in a report;
* the data-script distribution boundary (``data/scripts/snapshot.py``) so an
  input tree carrying deferred (ECHA) raw bytes is rejected for release even
  when it verifies as a well-formed snapshot;
* the deployment builder (``deploy/build_deployment.py``) so the release
  manifest derives ``echa_not_included`` from the actual snapshot rather than a
  hard-coded value.

The policy is deliberately small and named; it is not a general permissions
framework. A deferred source is **never** a clean or available result: it is a
deliberate scope exclusion surfaced as ``not_included``.
"""

from __future__ import annotations

from typing import Any, Dict, List

# All source lists the application is aware of. Kept here so the runtime
# coverage builders and the release boundary agree on the vocabulary.
SOURCE_KINDS: Dict[str, str] = {
    "cosing-annex-ii": "Annex II (prohibited)",
    "cosing-annex-iii": "Annex III (restricted)",
    "cosing-annex-iv": "Annex IV (colorants)",
    "cosing-annex-v": "Annex V (preservatives)",
    "cosing-annex-vi": "Annex VI (UV filters)",
    "echa-svhc": "ECHA SVHC Candidate List",
    "echa-annex-xiv": "ECHA Authorisation List (Annex XIV)",
    "echa-annex-xvii": "ECHA Restrictions (Annex XVII)",
}

# ECHA sources are deliberately deferred in this release (AGENTS.md #9).
# Acquisition is disabled; they must never be ingested or distributed, and they
# are surfaced as an explicit coverage exclusion rather than a clean result.
DEFERRED_SOURCE_KINDS = frozenset({"echa-svhc", "echa-annex-xiv", "echa-annex-xvii"})

# CosIng sources that form the covered core of this release.
COSING_SOURCE_KINDS = frozenset(
    k for k in SOURCE_KINDS if k.startswith("cosing-annex-")
)


def is_deferred(key: str) -> bool:
    """Return True if a source kind is deliberately excluded from this release."""
    return key in DEFERRED_SOURCE_KINDS


def _present_and_accepted(verified_sources: Dict[str, Any]) -> set:
    """Return the source keys that are present AND accepted in the snapshot.

    ``present``/``accepted`` are the physical/accepted inventory state; a
    deferred source may be physically present in an over-formed input tree, but
    that does not make it release-eligible (see :func:`effective_available`).
    """
    return {
        k for k, v in (verified_sources or {}).items()
        if v.get("present") and v.get("accepted")
    }


def _release_eligible(verified_sources: Dict[str, Any]) -> set:
    """Return the present-and-accepted sources that are eligible for release.

    Deferred (ECHA) sources are always excluded here regardless of their
    physical/accepted inventory state. A source may be recorded in provenance
    but never reach effective coverage for this release.
    """
    return {k for k in _present_and_accepted(verified_sources) if not is_deferred(k)}


def effective_available(verified_sources: Dict[str, Any]) -> List[str]:
    """Return the source kinds that are actually available for this release.

    ECHA sources are never available, even when a source directory is recorded
    as present and accepted in the input tree's provenance.
    """
    eligible = _release_eligible(verified_sources)
    return sorted(eligible & set(SOURCE_KINDS))


def effective_missing(verified_sources: Dict[str, Any]) -> List[str]:
    """Return the configured-but-unavailable (non-deferred) source kinds.

    ``missing`` is a coverage gap; it is never a clean result. A source that is
    physically present but not accepted (or not ingested) counts as missing
    here because effective coverage is what matters.
    """
    present = _present_and_accepted(verified_sources)
    return sorted((set(SOURCE_KINDS) - present) - DEFERRED_SOURCE_KINDS)


def effective_not_included(verified_sources: Dict[str, Any]) -> List[str]:
    """Return the deliberately-deferred source kinds.

    A deferral is always a scope exclusion — never a clean or missing result —
    regardless of the physical/accepted inventory of a source directory.
    """
    return sorted(DEFERRED_SOURCE_KINDS)


def coverage_dict(verified_sources: Dict[str, Any]) -> Dict[str, Any]:
    """Return the effective available/missing/not_included coverage dict.

    Reused by the label, formula and batch report builders so coverage always
    reflects the release boundary and never re-infers it separately.

    REACH Annex XVII entry 70 (D4/D5/D6) is a source-backed mapping from the
    official EU legal act (Regulation 2024/1328), NOT a snapshot source list and
    NOT an ECHA export. It is advertised as covered ONLY when the snapshot
    carries an accepted, compatible entry 70 source/mapping record (see
    ``__import__('eu_reg_lists.annex17_entry70', ...).source_is_active``). An
    empty or historical CosIng-only snapshot must NOT advertise entry 70.
    """
    from .annex17_entry70 import source_is_active, accepted_source_mapping
    active_mapping = None
    if source_is_active(verified_sources):
        active_mapping = accepted_source_mapping(verified_sources) or {}
    else:
        active_mapping = None
    annex17_entries = ["70"] if active_mapping else []
    annex17_branches = list(active_mapping.get("supported_branches") or []) \
        if active_mapping else []
    # A deferred (ECHA) source present+accepted is still a release blocker.
    return {
        "available": effective_available(verified_sources),
        "missing": effective_missing(verified_sources),
        "not_included": effective_not_included(verified_sources),
        "has_annex_ii": "cosing-annex-ii" in effective_available(verified_sources),
        "annex17_entries": annex17_entries,
        "annex17_branches": annex17_branches,
        "annex17_source": (active_mapping or {}).get("source_url"),
        "note": ("Only listed sources are covered; missing lists and "
                 "not-included lists are coverage gaps / deliberate scope "
                 "exclusions, NOT a clean result. " +
                 (("REACH Annex XVII is covered only for entry 70 D4/D5/D6 "
                   "cosmetic branches (wash-off and other cosmetic products), "
                   "bound to the accepted source mapping; it is not full "
                   "Annex XVII or REACH coverage, and ECHA exports remain "
                   "excluded.") if active_mapping else
                  "REACH Annex XVII is not covered by this snapshot (no "
                  "accepted entry 70 source mapping); ECHA exports remain "
                  "excluded.")),
    }


def release_blockers(verified_sources: Dict[str, Any]) -> List[str]:
    """Return deferred sources recorded as present AND accepted in the snapshot.

    Integrity verification does **not** make a deferred source distributeable.
    A source here blocks release export/deployment even though it verifies.
    """
    present = _present_and_accepted(verified_sources)
    return sorted(present & DEFERRED_SOURCE_KINDS)


def deferred_with_raw_data_in_tree(root: Any) -> List[str]:
    """Return deferred source keys whose source dir physically holds data files.

    This is a tree-level eligibility probe: a formerly accepted ECHA directory
    present in an input tree is release-ineligible even if its provenance is not
    (or is no longer) marked accepted. ``root`` is a Path to a snapshot tree.
    """
    out: List[str] = []
    for key in sorted(DEFERRED_SOURCE_KINDS):
        d = root / "data" / "raw" / key
        if d.is_dir() and any(p.is_file() for p in d.rglob("*")):
            out.append(key)
    return out
