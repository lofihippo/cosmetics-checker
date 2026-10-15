"""Structured formula input + conservative Annex III evaluator.

This is a *bounded* evaluator. It supports only a small documented set of real
Annex III restrictions that have unambiguous numeric limits with explicit,
matching units/basis/context. It does NOT attempt general legal-text
interpretation: complex rules (multi-branch product types, "as NH3" free-base
equivalents, after-mixing limits, aggregate limits, pH/professional-use
conditions) are conservatively returned as ``requires_manual_review`` /
``requires_context`` rather than evaluated.

Honesty semantics (project policy):
  * A numeric "within limit" is ONLY a numeric comparison against the rule's cap.
    It is NOT a statement that the ingredient is clean/compliant/unrestricted,
    and NOT a claim that warnings, pH, professional-use, or other conditions are
    satisfied. Those remaining conditions stay ``unresolved_requirements``.
  * ``unknown ingredient`` is an unresolved identity (never "no restriction").
  * Every supported rule is mapped explicitly to its source reference and a
    conditions fingerprint; if the fingerprint no longer matches the snapshot's
    stored conditions, the mapping is INVALIDATED (not silently reused).

Applicability gating
--------------------
A numeric comparison against a rule's cap is performed ONLY when the supplied
unit, concentration basis and necessary product context are each present and
match the rule's canonical value (via an explicit, documented alias map). Exact
canonical matching is used — never substring/prefix/fuzzy matching. Missing,
unsupported or contradictory unit/basis/product context leaves
``numeric_comparison`` null and is returned as ``requires_context`` /
``requires_manual_review``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from eu_reg_lists import CheckResult, Registry, check_token
from eu_reg_lists.release_policy import coverage_dict
from eu_reg_lists.checker import annex2_prohibition_finding

DISCLAIMER = "Regulatory information, not legal advice."

# ---------------------------------------------------------------------------
# Canonical value maps (explicit, documented). Matching uses EXACT canonical
# equality after mapping through these aliases; there is no fuzzy/substring
# inference.
# ---------------------------------------------------------------------------

# Supported concentration units and their documented aliases. Only a percent by
# weight is supported by the bounded evaluator. Unsupported units are NEVER
# converted or assumed.
_UNIT_ALIASES: Dict[str, str] = {
    "%": "%",
    "percent": "%",
    "pct": "%",
    "w/w": "%",   # percent by weight
    "w/w %": "%",
}
_SUPPORTED_UNIT_CANONICAL = "%"

# Accepted concentration-basis aliases. The canonical is underscored. Variants
# that are NOT in this map (e.g. 'ready', a prefix) are unsupported and are not
# assumed.
_BASIS_ALIASES: Dict[str, str] = {
    "ready_for_use": "ready_for_use",
    "ready_for_use_preparation": "ready_for_use",
    "ready for use": "ready_for_use",
    "after_mixing": "after_mixing",
    "after_mixing_for_use": "after_mixing",
    "after mixing for use": "after_mixing",
}

# Explicit product-type enum we accept for the bounded rules. Each supported
# rule declares the canonical product it binds to below; a supplied
# product_type must map (exactly) to that same canonical value.
_PRODUCT_ALIASES: Dict[str, str] = {
    "rust_inhibitor": "rust_inhibitor",
    "hair_product": "hair_product",
    "hair_products": "hair_product",
    "nail_product": "nail_product",
    "nail_products": "nail_product",
    "antidandruff_shampoo": "antidandruff_shampoo",
    "antidandruff_shampoos": "antidandruff_shampoo",
    "lash_brow_colour": "lash_brow_colour",
    "colouring_eyelashes_eyebrows": "lash_brow_colour",
    "oral_product": "oral_product",
    "oral_products": "oral_product",
    "artificial_nail_system": "artificial_nail_system",
    "artificial_nail_systems": "artificial_nail_system",
}


def _canonical_unit(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _UNIT_ALIASES.get(value.strip().lower())


def _canonical_basis(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _BASIS_ALIASES.get(value.strip().lower())


def _canonical_product(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _PRODUCT_ALIASES.get(value.strip().lower())


# The condition fingerprint is an explicit, reviewable record of the EXACT
# structured conditions we bind to. It is derived from the snapshot's
# ``conditions_list`` at mapping time and must match for the rule to be
# evaluated. A source change to the conditions invalidates the mapping.
def _conditions_fingerprint(conditions_list: Any) -> str:
    """Return a stable fingerprint over a rule's structured conditions.

    The fingerprint covers the field + value of each ordered fragment, so a
    change to any product-type/limit/other/warning text invalidates the mapping
    (we never silently reuse an old interpretation).
    """
    if not conditions_list:
        return ""
    blob = json.dumps([[c.get("field"), c.get("value")]
                       for c in conditions_list], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Supported rule mappings (explicit, reviewable, source-bound).
# ---------------------------------------------------------------------------
# Each limit is a real Annex III restriction with an unambiguous single
# percentage. ``product`` is the canonical product-type context the rule binds
# to (when the rule restricts to a product category), so a formula with a
# missing/mismatched product type is ``requires_context`` rather than evaluated
# against an inapplicable limit. ``conditions_template`` is the exact
# conditions-list the mapping was built from; a fingerprint mismatch
# invalidates the mapping.
SUPPORTED_RULES: Dict[str, Dict[str, Any]] = {
    # Reference Number 17 — Sodium nitrite, Rust inhibitor cap 0.2%.
    "17": {
        "reference_number": "17",
        "name": "Sodium nitrite",
        "cas": "7632-00-0",
        "limit_type": "max",
        "limit_decimal": "0.2",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "rust_inhibitor",
        "product_label": "Rust inhibitor",
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Rust inhibitor"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "0.2%"},
            {"field": "other", "label": "Other",
             "value": "Not to be used with secondary and/or tertiary amines or other substances forming nitrosamines"},
        ],
    },
    # Reference Number 49 — Selenium sulfide, Antidandruff shampoos cap 1%.
    "49": {
        "reference_number": "49",
        "name": "Selenium sulfide",
        "cas": "7488-56-4",
        "limit_type": "max",
        "limit_decimal": "1",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "antidandruff_shampoo",
        "product_label": "Antidandruff shampoos",
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Antidandruff shampoos"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "1%"},
            {"field": "warnings",
             "label": "Wording of conditions of use and warnings",
             "value": "Contains selenium disulphide\nAvoid contact with eyes or damaged skin"},
        ],
    },
    # Reference Number 185 — Toluene, Nail products cap 25%.
    "185": {
        "reference_number": "185",
        "name": "Toluene",
        "cas": "108-88-3",
        "limit_type": "max",
        "limit_decimal": "25",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "nail_product",
        "product_label": "Nail products",
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Nail products"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "25%"},
            {"field": "warnings",
             "label": "Wording of conditions of use and warnings",
             "value": "Keep out of reach of children. To be used by adults only"},
        ],
    },
    # Reference Number 48 — Silver nitrate, colouring eyelashes/brows cap 4%.
    "48": {
        "reference_number": "48",
        "name": "Silver nitrate",
        "cas": "7761-88-8",
        "limit_type": "max",
        "limit_decimal": "4",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "lash_brow_colour",
        "product_label": "Only for colouring eyelashes and eyebrows",
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Only for colouring eyelashes and eyebrows"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "4%"},
            {"field": "warnings",
             "label": "Wording of conditions of use and warnings",
             "value": "Contains silver nitrate \nRinse eyes immediately if product comes into contact with them"},
        ],
    },
    # Reference Number 3 — Oxalic acid (esters/alkal. salts), Hair products cap
    # 5% AND requires 'Professional use' (an unevaluated context condition).
    "3": {
        "reference_number": "3",
        "name": "Oxalic acid, its esters and alkaline salts",
        "cas": None,  # multiple salts; no single CAS to key on
        "limit_type": "max",
        "limit_decimal": "5",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "hair_product",
        "product_label": "Hair products",
        "requires_context": ["professional_use"],
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Hair products"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "5%"},
            {"field": "other", "label": "Other", "value": "Professional use"},
            {"field": "warnings",
             "label": "Wording of conditions of use and warnings",
             "value": "For professional use only"},
        ],
    },
    # Reference Number 14 — Hydroquinone (Annex III), Artificial nail systems
    # cap 0.02% (after mixing for use). Kept OUT of numeric evaluation because
    # it is an after-mixing limit and carries a cross-annex Annex II exception;
    # it is returned as manual review unless the full exception logic is
    # supported (it is not).
    "14": {
        "reference_number": "14",
        "name": "Hydroquinone",
        "cas": "123-31-9",
        "limit_type": "max",
        "limit_decimal": "0.02",
        "unit": "%",
        "basis": "after_mixing",
        "product": "artificial_nail_system",
        "product_label": "Artificial nail systems",
        "cross_annex_exception": True,
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Artificial nail systems"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "0.02% (after mixing for use)"},
            {"field": "other", "label": "Other", "value": "Professional use"},
            {"field": "warnings",
             "label": "Wording of conditions of use and warnings",
             "value": "- For professional use only \n- Avoid skin contact \n- Read directions for use carefully"},
        ],
    },
    # Reference Number 46 — 6-Methylcoumarin, Oral products cap 0.003%.
    "46": {
        "reference_number": "46",
        "name": "6-Methylcoumarin",
        "cas": "92-48-8",
        "limit_type": "max",
        "limit_decimal": "0.003",
        "unit": "%",
        "basis": "ready_for_use",
        "product": "oral_product",
        "product_label": "Oral products",
        "conditions_template": [
            {"field": "product_types", "label": "Product type, body parts",
             "value": "Oral products"},
            {"field": "max_concentration",
             "label": "Maximum concentration in ready for use preparation",
             "value": "0.003%"},
            {"field": "other", "label": "Other",
             "value": "The presence of the substance shall be indicated in the list of ingredients referred to in Article 19(1), point (g), when its concentration exceeds:\n— 0,001 % in leave-on products\n— 0,01 % in rinse-off products"},
        ],
    },
}

# Ingredients with a complex Annex III rule that we deliberately do NOT evaluate.
# Listed explicitly so the evaluator can return an honest manual-review verdict
# instead of attempting general legal-text interpretation.
COMPLEX_RULES_MANUAL_REVIEW: Dict[str, str] = {
    "2a": "Thioglycolic acid and its salts (multi-branch product types, "
          "'as thioglycolic acid' basis, pH + professional-use conditions) — "
          "complex; manual review.",
}


# ---------------------------------------------------------------------------
# Input model + validation.
# ---------------------------------------------------------------------------
@dataclass
class FormulaIngredient:
    name_or_id: str
    percent: Optional[Decimal] = None
    unit: Optional[str] = None
    basis: Optional[str] = None
    product_type: Optional[str] = None
    body_area: Optional[str] = None
    professional_use: Optional[bool] = None
    ready_for_use: Optional[bool] = None
    assessment_date: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Any) -> "FormulaIngredient":
        if not isinstance(d, dict):
            raise ValueError("each formula ingredient must be a JSON object")
        # Validate the object shape before reading individual fields.
        name_or_id = d.get("ingredient", d.get("name"))
        if not isinstance(name_or_id, str) or not name_or_id.strip():
            raise ValueError("ingredient must be a non-empty name or identifier")
        percent = _parse_percent(d.get("percent", d.get("percentage")))
        unit = _optional_str(d.get("unit"))
        basis = _optional_str(d.get("basis"))
        product_type = _optional_str(d.get("product_type", d.get("product")))
        body_area = _optional_str(d.get("body_area", d.get("body")))
        professional_use = _optional_bool(d.get("professional_use", d.get("professional")))
        ready_for_use = _optional_bool(d.get("ready_for_use"))
        assessment_date = _optional_date(d.get("assessment_date"))
        if assessment_date is not None:
            # A supplied assessment date must be syntactically valid ISO and is
            # never silently ignored (Part A: validated assessment-date contract).
            _parse_iso_date(assessment_date)
        return cls(name_or_id=name_or_id.strip(), percent=percent, unit=unit,
                   basis=basis, product_type=product_type, body_area=body_area,
                   professional_use=professional_use, ready_for_use=ready_for_use,
                   assessment_date=assessment_date)


def _optional_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"expected a string, got {type(v).__name__}")
    return v.strip() or None


def _optional_date(v: Any) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"expected a string for assessment_date, got {type(v).__name__}")
    return v.strip() or None


def _parse_iso_date(value: str) -> None:
    """Validate an ISO-8601 date string (``YYYY-MM-DD`` semantics via ``date``).

    Raises ValueError for a malformed date so a bad assessment_date is rejected
    consistently rather than ignored.
    """
    try:
        from datetime import date
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"assessment_date is not a valid ISO date: {value!r}")


def _optional_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    # Booleans are accepted only as JSON booleans; accept a small documented
    # set of strings as well (never a bare 0/1 that could mask an error).
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "yes"):
            return True
        if low in ("false", "no"):
            return False
        raise ValueError(f"expected a boolean, got {v!r}")
    raise ValueError(f"expected a boolean, got {type(v).__name__}")


def _parse_percent(v: Any) -> Optional[Decimal]:
    """Parse a percentage with decimal-safe validation (no float round-trip).

    Rejects booleans masquerading as numbers, non-finite values, malformed
    numeric strings, and values outside 0-100. Returns None when absent.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("percentage must be a number, not a boolean")
    if isinstance(v, Decimal):
        val = v
    elif isinstance(v, int):
        # int is exact; no float round-trip.
        val = Decimal(v)
    elif isinstance(v, float):
        if not _is_finite(v):
            raise ValueError("percentage must be finite")
        # Convert the float's shortest decimal repr (not str(v), which may
        # already be exact for the value), avoiding binary round-trip drift.
        val = Decimal(repr(v))
    elif isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # Reject malformed numeric strings such as "20%%%".
        if not _is_clean_decimal_string(s):
            raise ValueError(f"percentage is not a valid decimal: {v!r}")
        try:
            val = Decimal(s)
        except InvalidOperation:
            raise ValueError(f"percentage is not a valid decimal: {v!r}")
    else:
        raise ValueError("percentage must be a number or numeric string")
    if not val.is_finite():
        raise ValueError("percentage must be finite")
    if val < 0 or val > 100:
        raise ValueError("percentage must be between 0 and 100")
    return val


def _is_clean_decimal_string(s: str) -> bool:
    """Return True for a standalone decimal string (optional sign, one optional
    decimal point, at least one digit). Rejects trailing '%', doubled '%', etc."""
    import re
    return re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s) is not None


def _is_finite(f: float) -> bool:
    return f == f and f not in (float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# Evaluator.
# ---------------------------------------------------------------------------
# Possible verdicts:
#   exceeds_supported_limit | within_supported_numeric_limit | requires_context
#   | requires_manual_review | unknown_ingredient
# Plus a distinct set for source-bound REACH Annex XVII entry 70 (D4/D5/D6)
# so a supported entry 70 trigger is NEVER masked by a generic
# "requires_manual_review" / "not evaluated" headline and the within-threshold,
# missing-context, transition and unsupported states stay distinct and never
# imply overall compliance.
VERDICTS = (
    "exceeds_supported_limit",
    "within_supported_numeric_limit",
    "requires_context",
    "requires_manual_review",
    "unknown_ingredient",
    "entry70_supported_trigger",
    "entry70_within_threshold",
    "entry70_missing_context",
    "entry70_transition",
    "entry70_unsupported",
)


def _entry70_verdict_and_context(e70: Optional[Dict[str, Any]]) -> "tuple[Optional[str], Optional[str]]":
    """Map a source-bound entry 70 verdict dict to a formula verdict headline.

    Centralises the entry 70 presentation semantics so a supported trigger is
    never masked by the generic CosIng "requires_manual_review"/"Not evaluated"
    phrasing. Returns ``(verdict, applicable_context)``; both are None when
    entry 70 is absent or inactive so the caller keeps its CosIng result.
    """
    if not e70:
        return None, None
    state = e70.get("state")
    if state == "applicable_exceeds":
        return ("entry70_supported_trigger",
                "REACH Annex XVII entry 70 supported trigger: the submitted "
                "concentration meets or exceeds 0,1 % by weight for the "
                "supported cosmetic branch. This is a regulatory trigger, not a "
                "personal-harm or compliance-certificate claim.")
    if state == "applicable_within":
        return ("entry70_within_threshold",
                "REACH Annex XVII entry 70: the submitted concentration is below "
                "0,1 % by weight for the supported cosmetic branch. This is only "
                "the numeric comparison, not a compliance certificate.")
    if state == "missing_context":
        return ("entry70_missing_context",
                "REACH Annex XVII entry 70: a restriction applies but the "
                "submitted product context is incomplete/unsupported; it is not "
                "evaluated and not a compliance result.")
    if state == "transition_not_effective":
        return ("entry70_transition",
                "REACH Annex XVII entry 70: the assessment date is before the "
                "branch's effective date; the restriction has not yet applied "
                "for that date.")
    if state == "unsupported_use":
        return ("entry70_unsupported",
                "REACH Annex XVII entry 70: this use is covered by entry 70 but "
                "is not a supported cosmetic branch of this tool; not evaluated.")
    return None, None


@dataclass
class FormulaEvaluation:
    ingredient: str
    resolved_name: Optional[str]
    resolution: Optional[str]
    is_in_dataset: bool
    determination: str
    rule_status: Optional[str]          # strongest rule status (evidence only)
    annex: Optional[str]
    applicable_context: Optional[str]   # applicability/context assessment
    numeric_comparison: Optional[str]   # None when no numeric compare performed
    submitted_value: Optional[str]      # the raw concentration submitted
    unit: Optional[str]
    basis: Optional[str]
    cap: Optional[str]                  # the rule's cap (with unit) if applicable
    unresolved_requirements: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    verdict: str = "requires_manual_review"
    rules: List[Dict[str, Any]] = field(default_factory=list)  # ALL rule evidence
    # Explicit source-backed group membership: the member's own rules are in
    # ``rules``; the group's rules surfaced via a membership record are in
    # ``linked_rules`` (never merged). ``membership`` records the source group
    # identity/reference/row and member-name evidence. A member with linked
    # evidence is NOT "no regulatory evidence".
    membership: List[Dict[str, Any]] = field(default_factory=list)
    linked_rules: List[Dict[str, Any]] = field(default_factory=list)
    source_rule: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    # Whether the retained Annex II prohibition is an UNAMBIGUOUS prohibition
    # finding versus a cross-annex rule whose exception applicability is
    # unresolved. Never inferred from the headline determination alone.
    prohibition_finding: Optional[str] = None
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence (raw dict),
    # surfaced as supplementary evidence and never replacing the CosIng rules.
    entry70: Optional[Dict[str, Any]] = None
    # The raw submitted context, preserved exactly as supplied (distinct from any
    # canonical interpretation in ``applicable_context`` / the numeric comparison,
    # and never reconstructed from the rule's required context). Absent vs false
    # vs true are preserved distinctly.
    submitted_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ingredient": self.ingredient,
            "resolved_name": self.resolved_name,
            "resolution": self.resolution,
            "is_in_dataset": self.is_in_dataset,
            "determination": self.determination,
            "rule_status": self.rule_status,
            "annex": self.annex,
            "applicable_context": self.applicable_context,
            "numeric_comparison": self.numeric_comparison,
            "submitted_value": self.submitted_value,
            "unit": self.unit,
            "basis": self.basis,
            "cap": self.cap,
            "unresolved_requirements": self.unresolved_requirements,
            "reasons": self.reasons,
            "verdict": self.verdict,
            "rules": self.rules,
            "membership": self.membership,
            "linked_rules": self.linked_rules,
            "source_rule": self.source_rule,
            "provenance": self.provenance,
            "prohibition_finding": self.prohibition_finding,
            "entry70": self.entry70,
            "submitted_context": self.submitted_context,
        }


def _validation_error(message: str) -> ValueError:
    return ValueError(message)


def _validate_formula(formula: Any) -> List[Any]:
    """Validate the top-level formula object; return the ingredient list."""
    if not isinstance(formula, dict):
        raise _validation_error("formula must be a JSON object")
    ingredients_raw = formula.get("ingredients")
    if not isinstance(ingredients_raw, list) or not ingredients_raw:
        raise _validation_error("formula must contain a non-empty 'ingredients' list")
    return ingredients_raw


def evaluate_formula(formula: Any, registry: Registry) -> Dict[str, Any]:
    """Evaluate a structured formula against the bounded Annex III evaluator.

    Returns a report with per-ingredient evaluations, snapshot metadata, and
    coverage. Invalid input raises ``ValueError`` (never silently coerced).
    """
    ingredients_raw = _validate_formula(formula)
    ingredients = [FormulaIngredient.from_dict(e) for e in ingredients_raw]

    # Reject duplicate RESOLVED identities (canonical name + its CAS/alias may
    # resolve to the same substance; never silently combine percentages).
    seen_ids: Dict[str, int] = {}
    for ing in ingredients:
        sub = registry.lookup(ing.name_or_id)
        if sub is not None:
            sub_id = sub.id or sub.name or ""
            if sub_id in seen_ids:
                raise _validation_error(
                    f"duplicate resolved identity {sub.name!r} at "
                    f"{seen_ids[sub_id]} and {ingredients.index(ing)}; "
                    f"not silently combined")
            seen_ids[sub_id] = ingredients.index(ing)

    evaluations = [_evaluate_one(ing, registry) for ing in ingredients]
    data_updated_at = registry.data_updated_at
    coverage = _coverage_dict(registry)
    return {
        "evaluations": [e.to_dict() for e in evaluations],
        "data_updated_at": data_updated_at,
        "registry_size": len(registry),
        "coverage": coverage,
        "disclaimer": DISCLAIMER,
    }


def _coverage_dict(registry: Registry) -> Dict[str, Any]:
    """Derive available/missing coverage from the registry provenance.

    Delegates to :func:`eu_reg_lists.release_policy.coverage_dict` so the
    formula report uses the SAME release-boundary coverage as the label/batch
    reports (ECHA is always ``not_included``, never available).
    """
    verified = getattr(registry, "verified_sources", {}) or {}
    return coverage_dict(verified)


def evaluate_formula_file(path: str, registry: Registry) -> Dict[str, Any]:
    """Load a formula from a JSON file and evaluate it.

    Malformed JSON, a missing/wrong shape, or invalid field values raise
    ``ValueError`` (never silently coerced).
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        # parse_float=Decimal preserves JSON number precision so a value like
        # 25.0000000000000001 does not collapse to a lossy binary float; it
        # compares identically to its exact string equivalent.
        formula = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise _validation_error(f"invalid JSON: {exc}") from exc
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        # e.g. a JSON number like 1e99999999999999999999 that Decimal cannot
        # hold -> a clear validation error (the CLI turns this into exit 2),
        # never a traceback.
        raise _validation_error("invalid numeric value in formula") from exc
    return evaluate_formula(formula, registry)


def _evaluate_one(ing: FormulaIngredient, registry: Registry) -> FormulaEvaluation:
    # Use the SHARED checker to obtain full regulatory evidence independently of
    # any numeric support: is_in_dataset, determination, all rules, resolution
    # method and snapshot timestamp.
    evidence = check_token(ing.name_or_id, registry, registry.data_updated_at)
    reasons: List[str] = []
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence from the shared
    # checker (label-only: no context). When the identity is a D4/D5/D6
    # substance, we RE-EVALUATE it with the submitted formula context so a
    # supported D5 formula produces an actual entry 70 comparison instead of
    # copying the missing-context label-only result. This is the same shared
    # evaluator, never a second engine.
    e70 = getattr(evidence, "entry70", None)
    from eu_reg_lists.annex17_entry70 import evaluate_entry70 as _evaluate70
    if e70 is not None:
        # Use the identity the shared checker ALREADY resolved for entry 70
        # (e.g. the D5 CAS 541-02-6 for the INCI alias 'Cyclopentasiloxane'),
        # rather than requiring the original user token to independently match
        # the entry 70 alias table. This keeps CAS and verified INCI aliases
        # equivalent. ``e70['ingredient']`` is the resolved D4/D5/D6 identifier
        # (may fall back to the supplied token when the checker resolved it
        # directly). This is the same shared evaluator, never a second engine.
        _resolved_ident = e70.get("ingredient") or ing.name_or_id
        _v = _evaluate70(
            _resolved_ident,
            percent=ing.percent,
            unit=ing.unit,
            product_type=ing.product_type,
            basis=ing.basis,
            ready_for_use=ing.ready_for_use,
            assessment_date=ing.assessment_date,
            verified_sources=getattr(registry, "verified_sources", None),
            snapshot_data_updated_at=registry.data_updated_at,
        )
        e70 = _v.to_dict()
        # Record which resolved identity was assessed so the report never
        # depends on the original token matching the entry 70 alias table.
        e70["resolved_name"] = (
            e70.get("ingredient") or getattr(evidence, "cased_name", None))
        if e70.get("state") != "missing_context" \
                and e70.get("state") != "source_not_active":
            reasons.append("REACH Annex XVII entry 70 evaluated with the "
                           "submitted formula context.")
        if e70.get("state") == "source_not_active":
            reasons.append("REACH Annex XVII entry 70 is not source-backed in "
                           "this snapshot; not evaluated.")

    # Derive the source-bound entry 70 headline (verdict + applicable_context)
    # once, so EVERY evaluation branch below can show an accurate prominent
    # trigger for a supported entry 70 branch instead of masking it behind the
    # generic CosIng "requires_manual_review" / "Not evaluated" phrasing. This
    # keeps the within-threshold, missing-context, transition and unsupported
    # states distinct and never implies overall compliance.
    e70_verdict, e70_context = _entry70_verdict_and_context(e70)

    if not evidence.is_in_dataset:
        reasons.append("Exact identifier/name not found; unresolved.")
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=None, resolution="unresolved",
            is_in_dataset=False, determination="unknown",
            rule_status=None, annex=None, applicable_context=None,
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=list(reasons),
            reasons=list(reasons), verdict="unknown_ingredient", rules=[],
        )

    sub = registry.lookup(ing.name_or_id)
    assert sub is not None, "is_in_dataset but registry lookup returned None"
    resolution = _resolution_method(ing.name_or_id, sub)
    reasons.append(f"Resolved {ing.name_or_id!r} to {sub.name!r} by {resolution}.")
    all_rules = [r.__dict__ for r in evidence.rules]
    determination = evidence.determination
    # Preserve explicit source-backed group membership + linked evidence. A
    # member identity with linked rules still has regulatory evidence (the
    # group's rule), reported as linked, never merged into its own rules.
    membership = list(getattr(evidence, "membership", None) or [])
    linked_rules = [r.__dict__ for r in getattr(evidence, "linked_rules", None) or []]
    if membership:
        reasons.append(
            "Member of explicit source group(s); linked group evidence is "
            "preserved (not merged into this identity).")
    # Centralise collection of linked (group) unevaluated conditions so EVERY
    # formula-evaluation branch preserves them consistently (no direct rules,
    # one/multiple Annex III rules, unsupported/complex rules, supported
    # numeric comparisons, scoped prohibition/exception paths). Linked rules
    # are surfaced as evidence only; they are never numerically evaluated and
    # their applicability is never inferred.
    _surface_conditions(reasons, linked_rules)

    # A recognised but UNVERIFIED identity (determination unknown, no rules).
    if not evidence.rules and determination == "unknown":
        if linked_rules:
            reasons.append(
                "Identity has no own rule but carries linked group evidence; "
                "not 'no regulatory evidence'.")
        # A source-bound entry 70 finding is the prominent headline when it is
        # present (e.g. a D5/D6 identity with no CosIng rule). Its supported
        # trigger / within-threshold / missing-context / transition / unsupported
        # states are surfaced instead of a generic manual-review label, so a
        # supported trigger is never hidden. When entry 70 is absent/inactive the
        # identity stays an unresolved unknown (never a clean result).
        verdict = e70_verdict if e70_verdict else "requires_manual_review"
        applicable = e70_context or "No CosIng rule; not evaluated."
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination="unknown",
            rule_status=None, annex=None, applicable_context=applicable,
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=list(reasons),
            entry70=e70,
            reasons=list(reasons), verdict=verdict,
            rules=all_rules, membership=membership, linked_rules=linked_rules,
        )

    # Build provenance/citation for the shared evidence.
    provenance = _evidence_provenance(evidence, registry)

    # Select the applicable Annex III restricted rule for this substance.
    annex3_rules = [r for r in evidence.rules
                    if (r.annex or "").lower() == "annex iii" and r.status == "restricted"]

    # If there is an Annex II prohibition, retained as evidence (Benzene etc).
    has_annex2 = any((r.annex or "").lower() == "annex ii" for r in evidence.rules)

    # Determine whether the retained Annex II prohibition is an unconditional
    # ban or carries an unresolved scope/exception qualification (e.g. "
    # ... when used as a substance in hair dye products", or an exception to an
    # Annex III entry). We never infer so from the headline determination alone;
    # the source name is the only evidence of scope used (bounded, documented).
    annex2_rules = [r for r in evidence.rules
                    if (r.annex or "").lower() == "annex ii"]

    if not annex3_rules:
        # Known substance but no Annex III restricted rule. If it is prohibited
        # (Annex II) that is retained as evidence; no numeric evaluation.
        if has_annex2:
            scope_finding, scope_reason = _annex2_scope_finding(annex2_rules)
            reasons.append("Annex II prohibition retained; no Annex III numeric "
                           "mapping.")
            if scope_finding != "unambiguous" and scope_reason:
                reasons.append(scope_reason)
            # Surface the unevaluated conditions of every retained rule
            # (including positive-list conditions), so a multi-list result
            # never only says "no Annex III mapping".
            for r in evidence.rules:
                reasons.extend(_rule_conditions_unevaluated(r))
            return FormulaEvaluation(
                ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
                is_in_dataset=True, determination=determination,
                rule_status=evidence.status, annex=evidence.annex,
                applicable_context=("Annex II prohibition with unresolved "
                                    "scope/exception." if scope_finding != "unambiguous"
                                    else "Annex II prohibition (no Annex III numeric mapping)."),
                numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
                basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=list(reasons),
            entry70=e70,
                reasons=list(reasons), verdict="requires_manual_review",
                rules=all_rules, membership=membership, linked_rules=linked_rules,
                source_rule=_headline(_source_rule_for(evidence)),
                provenance=provenance,
                prohibition_finding=scope_finding,
            )
        # Recognised identity with rules but no Annex III restricted mapping.
        # Surface the unevaluated conditions of every retained rule so a
        # positive-list / multi-list result preserves the complete evidence.
        if evidence.rules:
            for r in evidence.rules:
                reasons.extend(_rule_conditions_unevaluated(r))
        reasons.append("No Annex III restricted rule matched for this substance.")
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=evidence.status, annex=evidence.annex,
            applicable_context="No Annex III restricted rule; not evaluated.",
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=list(reasons),
            entry70=e70,
            reasons=list(reasons), verdict="requires_manual_review",
            rules=all_rules, membership=membership, linked_rules=linked_rules,
            source_rule=_headline(_source_rule_for(evidence)),
            provenance=provenance,
        )

    # Multiple Annex III restricted rules -> conservative manual review.
    if len(annex3_rules) > 1:
        reasons.append(f"{len(annex3_rules)} Annex III rules; multiple rules require "
                       f"manual review.")
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=evidence.status, annex=evidence.annex,
            applicable_context="Multiple Annex III rules; not evaluated.",
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=list(reasons),
            entry70=e70,
            reasons=list(reasons), verdict="requires_manual_review",
            rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule=_headline(_source_rule_for(evidence)),
            provenance=provenance,
        )

    # Exactly one Annex III restricted rule.
    rule = annex3_rules[0]
    raw = rule.raw or {}
    ref = str(raw.get("official_reference") or raw.get("reference_number") or "")
    conditions = raw.get("conditions_list") or []
    fp = _conditions_fingerprint(conditions)
    provenance = _rule_provenance(rule, registry, conditions, fp)
    source_rule = _source_rule_dict(rule, sub, registry)

    # Derive ALL unevaluated conditions from the rule (warnings, other,
    # professional-use) — surfaced regardless of numeric outcome.
    unresolved = _unresolved_conditions(rule, mapping_constraints=SUPPORTED_RULES.get(ref))

    mapping, map_reasons = _find_mapping(rule, fp)

    # ---- complex rule (never interpreted) ----
    if ref in COMPLEX_RULES_MANUAL_REVIEW:
        reasons.append(COMPLEX_RULES_MANUAL_REVIEW[ref])
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=rule.status, annex=rule.annex,
            applicable_context="Complex rule; not evaluated.",
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=unresolved + reasons,
            entry70=e70,
            reasons=unresolved + reasons, verdict="requires_manual_review",
            rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule=source_rule, provenance=provenance,
        )

    # ---- cross-annex exception (e.g. Hydroquinone) -> manual review ----
    if mapping and mapping.get("cross_annex_exception"):
        reasons.append("Cross-annex exception (Annex II/III); exception logic not "
                       "supported.")
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=rule.status, annex=rule.annex,
            applicable_context="Cross-annex exception; does not evaluate the Annex "
                               "II exception.",
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=unresolved + reasons,
            entry70=e70,
            reasons=unresolved + reasons, verdict="requires_manual_review",
            rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule=source_rule, provenance=provenance,
            prohibition_finding="exception_unresolved",
        )

    # ---- unsupported / unmapped rule -> manual review ----
    if mapping is None:
        reasons.append(map_reasons[0] if map_reasons else
                       f"No supported-rule mapping for reference {ref or 'unknown'}; "
                       f"not interpreted.")
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=rule.status, annex=rule.annex,
            applicable_context="Unsupported rule; not evaluated.",
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=None, unresolved_requirements=unresolved + reasons,
            entry70=e70,
            reasons=unresolved + reasons, verdict="requires_manual_review",
            rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule=source_rule, provenance=provenance,
        )

    # ---- supported mapping: strict applicability gating before compare ----
    applicability, ctx_reasons = _applicability(ing, mapping)
    cap_str = f"{mapping['limit_decimal']}{mapping['unit']}"
    reasons += ctx_reasons

    # Numeric comparison is performed ONLY when applicability is fully resolved.
    numeric = None
    if applicability == "applicable":
        numeric = _numeric_compare(ing.percent, Decimal(mapping["limit_decimal"]))
    elif ing.percent is None:
        reasons.append("No concentration supplied; restriction identified but not "
                       "evaluated.")

    unresolved_all = _dedup(unresolved + reasons)

    if numeric is None:
        verdict = "requires_context" if applicability != "applicable" else "requires_context"
        applicable_context = _context_note(mapping, ing)
        return FormulaEvaluation(
            ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
            is_in_dataset=True, determination=determination,
            rule_status=rule.status, annex=rule.annex,
            applicable_context=applicable_context,
            numeric_comparison=None, submitted_value=_submitted(ing), unit=ing.unit,
            basis=ing.basis, submitted_context=_submitted_context(ing), cap=cap_str, unresolved_requirements=unresolved_all,
            entry70=e70,
            reasons=unresolved_all, verdict=verdict,
            rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule={**source_rule, "limit": mapping["limit_decimal"],
                                          "limit_unit": mapping["unit"], "basis": mapping["basis"]},
            provenance=provenance,
        )

    verdict = numeric
    return FormulaEvaluation(
        ingredient=ing.name_or_id, resolved_name=sub.name, resolution=resolution,
        is_in_dataset=True, determination=determination,
        rule_status=rule.status, annex=rule.annex,
        applicable_context=_context_note(mapping, ing),
        numeric_comparison=numeric, submitted_value=_submitted(ing), unit=ing.unit,
        basis=ing.basis, submitted_context=_submitted_context(ing), cap=cap_str, unresolved_requirements=unresolved_all,
            entry70=e70,
        reasons=unresolved_all, verdict=verdict,
        rules=all_rules, membership=membership, linked_rules=linked_rules, source_rule={**source_rule, "limit": mapping["limit_decimal"],
                                      "limit_unit": mapping["unit"], "basis": mapping["basis"]},
        provenance=provenance,
    )


def _applicability(ing: FormulaIngredient, mapping: Dict[str, Any]) -> "tuple[str, List[str]]":
    """Determine applicability of a supported rule to the supplied context.

    Returns (status, reasons) where status is one of ``applicable`` (all
    required unit/basis/product context present and matching), ``missing`` or
    ``mismatch`` (some required context missing/contradictory). Exact canonical
    matching only — never substring/prefix/fuzzy.
    """
    reasons: List[str] = []
    # Unit must be present and map to '%'.
    canonical_unit = _canonical_unit(ing.unit)
    if ing.unit is None:
        reasons.append("Concentration unit not supplied; required before numeric "
                       "comparison.")
        return "mismatch", reasons
    if canonical_unit is None:
        reasons.append(f"Unsupported concentration unit {ing.unit!r}; no numeric "
                       f"comparison.")
        return "mismatch", reasons
    if canonical_unit != _SUPPORTED_UNIT_CANONICAL or canonical_unit != mapping["unit"]:
        reasons.append(f"Concentration unit {ing.unit!r} does not match the rule's "
                       f"required unit; no numeric comparison.")
        return "mismatch", reasons

    # Basis must be present and map to the rule's required basis.
    mapping_basis = mapping.get("basis")
    canonical_basis = _canonical_basis(ing.basis)
    if ing.basis is None:
        reasons.append("Concentration basis not supplied; required before numeric "
                       "comparison.")
        return "mismatch", reasons
    if canonical_basis is None:
        reasons.append(f"Unsupported concentration basis {ing.basis!r}; no numeric "
                       f"comparison.")
        return "mismatch", reasons
    if canonical_basis != mapping_basis:
        reasons.append(f"Concentration basis {ing.basis!r} does not match the rule's "
                       f"required basis {mapping_basis!r}; no numeric comparison.")
        return "mismatch", reasons

    # Product context must be present and map to the rule's required product.
    mapping_product = mapping.get("product")
    if mapping_product:
        canonical_product = _canonical_product(ing.product_type)
        if ing.product_type is None:
            reasons.append("Product type context not supplied; required before numeric "
                           "comparison.")
            return "mismatch", reasons
        if canonical_product is None:
            reasons.append(f"Unsupported product type {ing.product_type!r}; no numeric "
                           f"comparison.")
            return "mismatch", reasons
        if canonical_product != mapping_product:
            reasons.append(f"Product type {ing.product_type!r} does not match the rule's "
                           f"required context; no numeric comparison.")
            return "mismatch", reasons

    # ready_for_use flag, when supplied, must be consistent. A rule with
    # basis ready_for_use requires ready_for_use to be true (or absent-but-
    # implied) — a false value is contradictory.
    if ing.ready_for_use is False and mapping_basis == "ready_for_use":
        reasons.append("ready_for_use is false but the rule requires ready-for-use "
                       "basis; contradictory context.")
        return "mismatch", reasons

    # professional_use, when supplied for a rule that requires it, is recorded
    # as an unevaluated context requirement (never a satisfied condition).
    return "applicable", reasons


def _unresolved_conditions(rule: Any, mapping_constraints: Optional[Dict[str, Any]]) -> List[str]:
    """Return every unevaluated condition/requirement from a rule's conditions.

    Surfaced regardless of whether a numeric comparison is performed, so a
    ``within_supported_numeric_limit`` result never presents an empty unresolved
    list while known requirements remain unchecked.
    """
    out: List[str] = []
    raw = rule.raw or {}
    conditions = raw.get("conditions_list") or []
    for c in conditions:
        field = c.get("field")
        label = c.get("label") or field or "Condition"
        value = (c.get("value") or "").replace("\n", " ").strip()
        # The product-type and max-concentration dimensions are applicability
        # / numeric dimensions assessed (or deferred) separately. Only the
        # remaining sources of conditions are surfaced as unresolved so a
        # "within limit" never presents an empty list when these remain.
        if field in ("product_types", "max_concentration"):
            continue
        if value:
            out.append(f"{label}: {value}")
    if mapping_constraints and "professional_use" in (mapping_constraints.get("requires_context") or []):
        out.append("Professional-use requirement not evaluated.")
    return _dedup(out)


def _submitted(ing: FormulaIngredient) -> Optional[str]:
    if ing.percent is None:
        return None
    return str(ing.percent)


def _submitted_context(ing: FormulaIngredient) -> Dict[str, Any]:
    """Return the raw submitted context exactly as supplied.

    Only the keys that were actually supplied are present; absent context stays
    absent (never zero, never inferred). False and true are preserved distinctly
    from absent. This is separate from any canonical interpretation.
    """
    ctx: Dict[str, Any] = {}
    if ing.unit is not None:
        ctx["unit"] = ing.unit
    if ing.basis is not None:
        ctx["basis"] = ing.basis
    if ing.product_type is not None:
        ctx["product_type"] = ing.product_type
    if ing.body_area is not None:
        ctx["body_area"] = ing.body_area
    if ing.professional_use is not None:
        ctx["professional_use"] = ing.professional_use
    if ing.ready_for_use is not None:
        ctx["ready_for_use"] = ing.ready_for_use
    if ing.assessment_date is not None:
        ctx["assessment_date"] = ing.assessment_date
    return ctx


def _resolution_method(token: str, sub) -> str:
    key = token.strip().lower()
    ids = [sub.cas, sub.ec, sub.e_number, sub.ci]
    if key in {str(i).strip().lower() for i in ids if i}:
        return "exact_identifier"
    return "exact_name_or_alias"


def _rule_provenance(rule, registry, conditions, fp) -> Dict[str, Any]:
    raw = rule.raw or {}
    return {
        "annex": rule.annex,
        "official_reference": raw.get("official_reference"),
        "reference_number": raw.get("reference_number"),
        "row_reference": raw.get("row_reference"),
        "source_file": raw.get("source_file"),
        "source_sha256": raw.get("source_sha256"),
        "retrieved_at": raw.get("retrieved_at"),
        "source_url": rule.source_url,
        "source_updated_at": rule.source_updated_at,
        "regulation": rule.regulation,
        "data_updated_at": registry.data_updated_at,
        "conditions_fingerprint": fp,
        "conditions_list": conditions,
    }


def _evidence_provenance(evidence: CheckResult, registry) -> Dict[str, Any]:
    raw0 = (evidence.rules[0].raw if evidence.rules else {}) or {}
    return {
        "regulation": evidence.regulation,
        "source_url": evidence.source_url,
        "source_updated_at": getattr(evidence.rules[0], "source_updated_at", None)
        if evidence.rules else None,
        "data_updated_at": evidence.data_updated_at,
        "official_reference": raw0.get("official_reference"),
        "row_reference": raw0.get("row_reference"),
        "source_file": raw0.get("source_file"),
        "source_sha256": raw0.get("source_sha256"),
        "retrieved_at": raw0.get("retrieved_at"),
    }


def _source_rule_dict(rule, sub, registry) -> Dict[str, Any]:
    raw = rule.raw or {}
    return {
        "reference_number": raw.get("official_reference"),
        "name": sub.name,
        "cas": sub.cas,
        "status": rule.status,
        "annex": rule.annex,
        "regulation": rule.regulation,
        "source_url": rule.source_url,
        "data_updated_at": registry.data_updated_at,
        "scope_text": _scope_text(rule),
    }


def _source_rule_for(evidence: CheckResult) -> dict:
    return {
        "reference_number": (evidence.rules[0].raw or {}).get("official_reference")
        if evidence.rules else None,
        "name": evidence.cased_name,
        "status": evidence.status,
        "annex": evidence.annex,
        "regulation": evidence.regulation,
        "source_url": evidence.source_url,
    }


def _headline(d: dict) -> dict:
    return d


def _scope_text(rule) -> Optional[str]:
    """Return the source-row scope/exception text (e.g. Hydroquinone's Annex II
    qualification referencing Annex III entry 14), or None when redundant."""
    src_row = (rule.raw or {}).get("source_row") or {}
    src_name = (src_row.get("Chemical name / INN") or "").strip()
    if not src_name:
        return None
    return src_name


# Bounded, documented scope/qualification phrases live in the shared checker
# (``eu_reg_lists.checker.ANNEX2_SCOPE_PHRASES``); the formula evaluator DELEGATES
# to that single classification so the label path and the formula path never
# drift into two independent interpretation engines.
_ANNEX2_SCOPE_PHRASES = None  # kept as a stable anchor symbol; not a separate engine


def _annex2_scope_finding(annex2_rules: List[Any]) -> "tuple[str, Optional[str]]":
    """Return ``(prohibition_finding, reason)`` for the retained Annex II rules.

    Delegates to the shared ``eu_reg_lists.checker.annex2_prohibition_finding``
    so the scoped/exception classification is IDENTICAL to the label path. A
    banned rule is "unambiguous" (an unconditional ban) only when its source name
    carries no documented scope/exception qualifier; otherwise the prohibition
    is returned as "scope_unresolved" (applicability unresolved, never a
    definitive prohibition).
    """
    return annex2_prohibition_finding(annex2_rules)


def _find_mapping(rule: Any, fingerprint: str) -> "tuple[Optional[Dict[str, Any]], List[str]]":
    """Find the supported-rule mapping for a rule by reference number.

    Validates the stored conditions fingerprint against the mapping's canonical
    ``conditions_template``; a mismatch invalidates the mapping (no silent reuse
    of an old interpretation when the source conditions changed).
    """
    ref = str((rule.raw or {}).get("official_reference") or rule.raw.get("reference_number") or "")
    mapping = SUPPORTED_RULES.get(ref)
    if mapping is None:
        return None, []
    template = mapping.get("conditions_template")
    expected_fp = _conditions_fingerprint(template) if template else ""
    if expected_fp != fingerprint:
        return None, [f"Rule mapping for reference {ref} is stale: conditions "
                      f"fingerprint changed ({fingerprint[:12]} != "
                      f"{expected_fp[:12]}); not reused."]
    return mapping, []


def _numeric_compare(percent: Optional[Decimal], limit: Decimal) -> Optional[str]:
    if percent is None:
        return None
    if percent > limit:
        return "exceeds_supported_limit"
    return "within_supported_numeric_limit"


def _context_note(mapping: Dict[str, Any], ing: FormulaIngredient) -> str:
    parts = []
    parts.append(mapping.get("product_label") or "context")
    if mapping.get("basis") == "after_mixing":
        parts.append("after mixing for use")
    if "professional_use" in (mapping.get("requires_context") or []):
        parts.append("professional use required (not evaluated)")
    return "; ".join(parts)


def _dedup(items: Sequence[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _rule_conditions_unevaluated(rule: Any) -> List[str]:
    """Return ALL of a rule's conditions as unevaluated requirement strings.

    Used when NO numeric/Annex III evaluation applies (e.g. an Annex IV/V/VI
    positive-list rule, or a retained Annex II rule with no Annex III mapping):
    the full ordered condition fragments (product type, concentration limit,
    other, warnings) are surfaced verbatim as unresolved requirements, so the
    result never claims the conditions are satisfied or merely that "no Annex
    III mapping exists". For a positive-list or multi-list result this preserves
    the complete per-rule evidence.
    """
    out: List[str] = []
    raw = rule.raw or {}
    for c in raw.get("conditions_list") or []:
        label = c.get("label") or c.get("field") or "Condition"
        value = (c.get("value") or "").replace("\n", " ").strip()
        if value:
            out.append(f"{label}: {value}")
    return _dedup(out)


def _rule_conditions_unevaluated_dict(rule: Dict[str, Any]) -> List[str]:
    """``_rule_conditions_unevaluated`` for a rule already serialised to a dict
    (as in ``linked_rules``). Surfaces ALL of a linked rule's conditions as
    unevaluated requirement strings."""
    out: List[str] = []
    raw = rule.get("raw") or {}
    for c in raw.get("conditions_list") or []:
        label = c.get("label") or c.get("field") or "Condition"
        value = (c.get("value") or "").replace("\n", " ").strip()
        if value:
            out.append(f"{label}: {value}")
    return _dedup(out)


def _surface_conditions(reasons: List[str], rules: List[Dict[str, Any]]) -> None:
    """Append every linked rule's unevaluated conditions to ``reasons``."""
    for r in rules:
        reasons.extend(_rule_conditions_unevaluated_dict(r))
