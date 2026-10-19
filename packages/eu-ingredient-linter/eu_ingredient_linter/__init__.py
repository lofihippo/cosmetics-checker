"""Label and batch reports over eu-reg-lists, with snapshot coverage.

The shared checker resolves exact identities and aliases, retains rule citations
and distinguishes unknown identities from verified findings. Formula evaluation
is exported from .formula; coverage policy lives in eu_reg_lists.release_policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from eu_reg_lists import CheckResult, Registry, check_label, check_token
from eu_reg_lists.release_policy import coverage_dict

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

# ECHA sources are intentionally NOT included in this release (deferred
# acquisition/data). They are surfaced separately from ordinary "missing"
# coverage so a user sees that ECHA is deliberately excluded, not just absent.
# The canonical policy lives in eu_reg_lists.release_policy (single source of
# truth); this alias keeps the module-level value stable for existing callers.
DEFERRED_SOURCE_KINDS = {"echa-svhc", "echa-annex-xiv", "echa-annex-xvii"}

DISCLAIMER = "Regulatory information, not legal advice."


@dataclass
class CoverageReport:
    """Which EU source lists are available in the snapshot vs missing.

    ``available`` lists covered sources. ``missing`` lists sources that are
    configured but not present/accepted in the snapshot (a coverage gap, never a
    clean result). ``not_included`` lists sources deliberately excluded from this
    release (ECHA) so a user can distinguish a coverage gap from an intentional
    scope exclusion.
    """

    available: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    not_included: List[str] = field(default_factory=list)
    annex17_entries: List[str] = field(default_factory=list)
    annex17_branches: List[str] = field(default_factory=list)
    annex17_source: Optional[str] = None

    @property
    def has_annex_ii(self) -> bool:
        return "cosing-annex-ii" in self.available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "missing": self.missing,
            "not_included": self.not_included,
            "has_annex_ii": self.has_annex_ii,
            "annex17_entries": self.annex17_entries,
            "annex17_branches": self.annex17_branches,
            "annex17_source": self.annex17_source,
            "note": "Only listed sources are covered; missing lists and "
                    "not-included lists are coverage gaps / deliberate scope "
                    "exclusions, NOT a clean result. REACH Annex XVII is covered "
                    "only for entry 70 D4/D5/D6 cosmetic branches (wash-off and "
                    "other cosmetic products); it is not full Annex XVII or REACH "
                    "coverage, and ECHA exports remain excluded.",
        }


def build_coverage(verified_sources: Dict[str, Any]) -> CoverageReport:
    """Derive available / missing / not-included lists from snapshot provenance.

    Uses :func:`eu_reg_lists.release_policy.coverage_dict` as the single source
    of truth for the release boundary. A source is AVAILABLE only when it is
    present AND accepted AND not a deliberately-deferred (ECHA) source. ECHA is
    always reported under ``not_included`` regardless of its physical inventory,
    so effective coverage can never advertise a deferred list as available.
    ``not_included`` carries the deliberately-deferred ECHA sources. The
    remaining configured source kinds not available are listed as MISSING.
    """
    cov = coverage_dict(verified_sources)
    return CoverageReport(available=cov["available"], missing=cov["missing"],
                          not_included=cov["not_included"],
                          annex17_entries=list(cov.get("annex17_entries") or []),
                          annex17_branches=list(cov.get("annex17_branches") or []),
                          annex17_source=cov.get("annex17_source"))


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
                      tokenize_fn: Optional[Callable[[str, "Optional[Sequence[str]]"], List[str]]] = None,
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
    report = check_label(text, registry, tokenize_fn, effective_dt)
    return {
        **report,
        "results": [r.as_dict() for r in report["results"]],
        "coverage": build_coverage(getattr(registry, "verified_sources", {}) or {}).to_dict(),
        "disclaimer": DISCLAIMER,
    }


def batch_report(items: List[str], registry: Registry,
                 tokenize_fn: Optional[Callable[[str, "Optional[Sequence[str]]"], List[str]]] = None,
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
    coverage = per_item[0]["coverage"] if per_item else coverage_dict({})
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
