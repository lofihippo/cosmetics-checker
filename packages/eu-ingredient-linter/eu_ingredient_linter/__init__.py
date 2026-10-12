"""eu-ingredient-linter: check label/batch ingredients against an Annex-II registry.

This is **Phase 2, scoped to Annex II + Annex III** (restricted). It provides:

 * ``ingredient_report`` / ``check_list`` — library API over ``eu_reg_lists``.
 * ``CoverageReport`` — states which EU annex/source lists are AVAILABLE
   (Annex II, Annex III) vs MISSING (Annex IV–VI, ECHA) so a result is never
   mistaken for a full regulatory determination.
 * A CLI (``python -m eu_ingredient_linter``) that reads a single label or a
   batch file and prints a human/JSON report.

Honesty rules (AGENTS.md):
 * Every rule carries a ``regulation``, ``source_url``, ``source_updated_at``
   and an explicit ``resolution``; official Reference Numbers are preserved
   separately from file row positions.
 * An unmatched / unresolved / recognised-but-unverified ingredient has
   ``determination == "unknown"`` and is NEVER reported as an affirmative
   "OK" / "clean".
 * Annex II and Annex III are covered; Annex IV–VI and ECHA are reported as
   MISSING (their absence is a coverage gap, not a clean result).
 * An Annex III restriction carries its conditions (product types,
   concentration limits, other restrictions, warnings) which are surfaced but
   NOT evaluated into a concentration verdict.
 * "Regulatory information, not legal advice."

The three substances reconciled as ambiguous/conflicting during ingestion are
left OUT of verified rules (they remain ``unknown``); they are not presented
here as proven violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from eu_reg_lists import CheckResult, Registry, check_token

from .formula import evaluate_formula as evaluate_formula, FormulaEvaluation as FormulaEvaluation
from .tokenize import tokenize

# List identifiers the registry *should* cover, and whether this snapshot does.
SOURCE_KINDS = {
    "cosing-annex-ii": "Annex II (prohibited)",
    "cosing-annex-iii": "Annex III (restricted)",
    "cosing-annex-iv": "Annex IV (colorants)",
    "cosing-annex-v": "Annex V (preservatives)",
    "cosing-annex-vi": "Annex VI (UV filters)",
    "echa-svhc": "ECHA SVHC Candidate List",
    "echa-annex-xiv": "ECHA Authorisation List (Annex XIV)",
    "echa-annex-xvii": "ECHA Restrictions (Annex XVII)",
}

DISCLAIMER = "Regulatory information, not legal advice."


@dataclass
class CoverageReport:
    """Which EU source lists are available in the snapshot vs missing."""

    available: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    @property
    def has_annex_ii(self) -> bool:
        return "cosing-annex-ii" in self.available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "missing": self.missing,
            "has_annex_ii": self.has_annex_ii,
            "note": "Only listed sources are covered; missing lists are a "
                    "coverage gap, NOT a clean result.",
        }


def build_coverage(verified_sources: Dict[str, Any]) -> CoverageReport:
    """Derive available vs missing lists from the snapshot provenance.

    A source is AVAILABLE when it is present AND accepted in the snapshot.
    All configured source kinds not available are listed as MISSING.
    """
    present = {
        k for k, v in (verified_sources or {}).items()
        if v.get("present") and v.get("accepted")
    }
    available = sorted(present & set(SOURCE_KINDS))
    missing = sorted(set(SOURCE_KINDS) - present)
    return CoverageReport(available=available, missing=missing)


def _tokenize_label(text: str, registry: Optional[Registry] = None) -> List[str]:
    """Split a raw label into ingredient tokens, punctuation-safe.

    Uses the registry's unambiguous names+aliases to protect names that contain
    ``,``/``&`` (e.g. ``1,4-dioxane``, ``FD&C Red 40``) from being split on a
    comma, so a banned ingredient is not lost to naive splitting.
    """
    if not text:
        return []
    phrases = getattr(registry, "phrases", None) if registry is not None else None
    return tokenize(text, phrases)


def _effective_data_updated_at(explicit: Optional[str],
                               registry: Registry) -> Optional[str]:
    """Resolve the report-level snapshot timestamp.

    An explicit override wins; otherwise the snapshot's ``registry.data_updated_at``
    is used. This is the snapshot build/update time, never the request time.
    ``source_updated_at`` and ``retrieved_at`` remain separate fields and are
    never substituted for this value.
    """
    if explicit is not None:
        return explicit
    return getattr(registry, "data_updated_at", None)


def check_list(tokens: List[str], registry: Registry,
               data_updated_at: Optional[str] = None) -> List[CheckResult]:
    """Check an explicit list of ingredient tokens (already split)."""
    return [check_token(t, registry, data_updated_at) for t in tokens]


def ingredient_report(text: str, registry: Registry,
                      tokenize_fn: Optional[Callable[[str], List[str]]] = None,
                      data_updated_at: Optional[str] = None) -> Dict[str, Any]:
    """Run a full label report with honest unknown semantics.

    Returns a dict with ``results`` (per-ingredient ``CheckResult.as_dict()``),
    counts, a headline ``verdict``, and a ``coverage`` block (Annex II present,
    others missing). The 3 ambiguous/conflicting quarantines are excluded from
    verified rules (they stay ``unknown``) and are surfaced only as notes.
    """
    # Resolve the effective snapshot timestamp up-front so the per-result and
    # report-level values are the SAME value (never None, never request time).
    effective_dt = _effective_data_updated_at(data_updated_at, registry)
    tokens = (tokenize_fn(text, registry.phrases) if tokenize_fn
              else _tokenize_label(text, registry))
    results = check_list(tokens, registry, effective_dt)
    # A flag includes the ingredient's OWN rules AND any group *linked* rules
    # (explicit source-backed membership), so a member ingredient whose only
    # evidence is a linked group rule is a conditional/restricted finding.
    flagged = [r for r in results
               if r.is_in_dataset and (
                   r.status in ("banned", "restricted", "allowed_with_conditions")
                   or any(lr.status in ("banned", "restricted",
                                        "allowed_with_conditions")
                          for lr in r.linked_rules))]
    banned = [r for r in flagged if r.status == "banned"
              or any(lr.status == "banned" for lr in r.linked_rules)]
    not_in = [r for r in results if not r.is_in_dataset]
    unverified_id = [r for r in results if r.is_in_dataset and r.determination == "unknown"]
    n_unverified = len(not_in) + len(unverified_id)
    no_ingredients = len(results) == 0
    clean = bool(flagged) is False and n_unverified == 0 and not no_ingredients
    verdict = "FLAGGED" if flagged else ("NO-INGREDIENTS" if no_ingredients
                                         else "NOT-FULLY-VERIFIED")
    if clean and not no_ingredients:
        verdict = "CLEAN"
    return {
        "results": [r.as_dict() for r in results],
        "tokens": tokens,
        "n_ingredients": len(results),
        "n_flagged": len(flagged),
        "n_banned": len(banned),
        "n_not_in_dataset": len(not_in),
        "n_unverified_identity": len(unverified_id),
        "n_unverified": n_unverified,
        "clean": clean,
        "verdict": verdict,
        "no_ingredients": no_ingredients,
        "data_updated_at": effective_dt,
        "registry_size": len(registry),
        "coverage": build_coverage(getattr(registry, "verified_sources", {}) or {}).to_dict(),
        "disclaimer": DISCLAIMER,
    }


def batch_report(items: List[str], registry: Registry,
                 tokenize_fn: Optional[Callable[[str], List[str]]] = None,
                 data_updated_at: Optional[str] = None) -> Dict[str, Any]:
    """Check a batch of labels (e.g. products) and return an aggregate report.

    Each ``items`` entry is a product label; the report carries the per-item
    ``ingredient_report`` plus overall flagged count and coverage summary.

    Batch validation contract:
      * Blank lines are dropped by the CLI before calling this; any remaining
        item that yields zero ingredient tokens is **invalid**.
      * ``is_valid`` is False (and ``n_invalid_items`` > 0) when there are no
        valid items OR any item yields no tokens.
      * ``invalid_item_indices`` is the 0-based list of those invalid items.
    """
    # Resolve the effective snapshot timestamp once, shared by all items.
    effective_dt = _effective_data_updated_at(data_updated_at, registry)
    per_item = [ingredient_report(item, registry, tokenize_fn, effective_dt)
                for item in items]
    total_ingredients = sum(r["n_ingredients"] for r in per_item)
    total_flagged = sum(r["n_flagged"] for r in per_item)
    total_banned = sum(r["n_banned"] for r in per_item)
    total_unverified = sum(r["n_unverified"] for r in per_item)
    # coverage is identical across items; use the first (or empty).
    coverage = per_item[0]["coverage"] if per_item else {
        "available": [], "missing": sorted(SOURCE_KINDS),
        "has_annex_ii": False,
        "note": "No items checked.",
    }
    invalid_item_indices = [i for i, it in enumerate(per_item)
                            if it["no_ingredients"]]
    no_valid_items = len(per_item) == 0
    has_invalid = bool(invalid_item_indices) or no_valid_items
    return {
        "items": per_item,
        "n_items": len(per_item),
        "total_ingredients": total_ingredients,
        "total_flagged": total_flagged,
        "total_banned": total_banned,
        "total_unverified": total_unverified,
        "n_invalid_items": len(invalid_item_indices) or (1 if no_valid_items else 0),
        "invalid_item_indices": invalid_item_indices,
        "has_invalid_input": has_invalid,
        "is_valid": not has_invalid,
        "coverage": coverage,
        "disclaimer": DISCLAIMER,
        "data_updated_at": effective_dt,
    }
