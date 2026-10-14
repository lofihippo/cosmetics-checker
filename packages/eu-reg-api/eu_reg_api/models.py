"""Pydantic response/request models for eu-reg-api.

These mirror the fields produced by ``eu-reg-lists`` and ``eu-ingredient-linter``
so the API does not re-derive or invent any classification, date, or coverage.
Every response carries the honesty disclaimer; ``data_updated_at`` is the
snapshot build/update time (never request time), kept distinct from
``source_updated_at`` and ``retrieved_at``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class RuleModel(BaseModel):
    status: Optional[str]
    annex: Optional[str]
    regulation: Optional[str]
    source_url: Optional[str]
    source_updated_at: Optional[str] = None      # upstream pub/update (None if unknown)
    conditions: Optional[str] = None             # compact summary string (backward-compatible)
    resolution: Optional[str] = None
    effective_date: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)  # provenance (source hash, refs)

    @property
    def conditions_list(self) -> List[Dict[str, Any]]:
        """The structured, ordered condition fragments (product types,
        concentration limits, other restrictions, warnings)."""
        return (self.raw or {}).get("conditions_list") or []


class SubstanceModel(BaseModel):
    id: Optional[str]
    name: Optional[str]
    aliases: List[str] = Field(default_factory=list)
    cas: Optional[str]
    ec: Optional[str]
    e_number: Optional[str]
    ci: Optional[str]
    determination: str
    rules: List[RuleModel] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    member_of: List[Dict[str, Any]] = Field(default_factory=list)


class SubstanceResponse(BaseModel):
    """Typed envelope for a CAS lookup.

    Carries the substance (with its rules/provenance), an explicit
    ``is_in_dataset`` membership flag, and the shared snapshot metadata
    (``data_updated_at``, ``coverage``, ``disclaimer``).
    """

    substance: SubstanceModel
    is_in_dataset: bool = True
    metadata: MetadataEnvelope


class CoverageModel(BaseModel):
    available: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    not_included: List[str] = Field(default_factory=list)
    has_annex_ii: bool
    note: str
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) coverage, named
    # explicitly so the frontend never presents it as full Annex XVII or ECHA.
    annex17_entries: List[str] = Field(default_factory=list)
    annex17_branches: List[str] = Field(default_factory=list)
    annex17_source: Optional[str] = None


class MetadataEnvelope(BaseModel):
    data_updated_at: Optional[str]
    coverage: Optional[CoverageModel] = None
    disclaimer: str = "Regulatory information, not legal advice."
    request_hint: Optional[str] = None


class SearchResult(BaseModel):
    """Bounded search over the registry (names, aliases, exact identifiers)."""

    query: str
    count: int
    limit: int
    results: List[SubstanceModel]
    metadata: MetadataEnvelope


class ListEntry(BaseModel):
    row: int
    substance: SubstanceModel


class ListResponse(BaseModel):
    list_name: str
    total: int
    limit: int
    offset: int
    entries: List[ListEntry]
    metadata: MetadataEnvelope


class CheckResultModel(BaseModel):
    """Per-ingredient result (mirrors linter's ``CheckResult.as_dict``)."""

    ingredient: str
    is_in_dataset: bool
    determination: str
    status: Optional[str] = None
    annex: Optional[str] = None
    regulation: Optional[str] = None
    source_url: Optional[str] = None
    data_updated_at: Optional[str] = None
    conditions: Optional[str] = None
    rules: List[RuleModel] = Field(default_factory=list)
    cas: Optional[str] = None
    ec: Optional[str] = None
    e_number: Optional[str] = None
    ci: Optional[str] = None
    resolution: Optional[str] = None
    cased_name: Optional[str] = None
    evaluation: Optional[str] = None
    evaluation_reason: Optional[str] = None
    # Classification of a retained Annex II prohibition. ``unambiguous`` means
    # an unconditional (definitive) prohibition; ``scope_unresolved`` means the
    # source scope/exception applicability is unresolved (never presented as a
    # definitive prohibition). Mirrors the shared evaluator.
    prohibition_finding: Optional[str] = None
    # Explicit source-backed group membership: records the group source entry
    # the ingredient belongs to, plus the group's own rules surfaced as linked
    # evidence. Group membership identifies relevant evidence; it never merges
    # identities or claims every group condition applies.
    membership: List[Dict[str, Any]] = Field(default_factory=list)
    linked_rules: List[RuleModel] = Field(default_factory=list)
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence. None unless
    # the identity is a D4/D5/D6 substance; when present it is the raw
    # entry-70 verdict dict. It never replaces a CosIng prohibition and is never
    # a category violation on its own (a label-only check lacks the
    # concentration/product-type/assessment-date context).
    entry70: Optional[Dict[str, Any]] = None


class CheckResponse(BaseModel):
    """A full ingredient check, delegating to ``ingredient_report``."""

    results: List[CheckResultModel]
    tokens: List[str]
    n_ingredients: int
    n_flagged: int
    n_banned: int
    n_not_in_dataset: int
    n_unverified_identity: int
    n_unverified: int
    clean: bool
    verdict: str
    no_ingredients: bool
    data_updated_at: Optional[str]
    registry_size: int
    coverage: CoverageModel
    disclaimer: str = "Regulatory information, not legal advice."
    metadata: MetadataEnvelope


class FormulaIngredientModel(BaseModel):
    """A single ingredient in a structured formula.

    ``percent`` is a JSON number or a decimal string (e.g. ``25.0000000000000001``).
    The endpoint parses the raw JSON body with ``parse_float=Decimal`` so a JSON
    number keeps exact decimal precision (no binary-float round-trip) and
    compares identically to its exact string equivalent. ``None`` (or absent)
    means the concentration is not supplied — it is left unevaluated, never
    silently assumed ready-for-use.
    """

    ingredient: str = Field(
        ..., min_length=1, description="Ingredient name or exact identifier.")
    # A number or numeric string; both are parsed exactly by the endpoint.
    percent: Optional[Union[float, str]] = Field(
        None, description="Concentration, as a JSON number or decimal string. "
                          "Absent/null = not supplied (unevaluated).")
    unit: Optional[str] = Field(
        None, description="Explicit concentration unit (e.g. '%'). Required for "
                          "a numeric comparison; an unsupported unit is never "
                          "converted or assumed.")
    basis: Optional[str] = Field(
        None, description="Concentration basis (e.g. 'ready_for_use'). Required "
                          "for a numeric comparison; a missing basis is not "
                          "assumed ready-for-use.")
    product_type: Optional[str] = Field(
        None, description="Product-type context (exact canonical or documented "
                          "alias). Required for a product-scoped rule.")
    body_area: Optional[str] = Field(None, description="Optional body-area context.")
    professional_use: Optional[bool] = Field(
        None, description="Optional boolean: professional use. A rule that "
                          "requires it stays an unresolved requirement.")
    ready_for_use: Optional[bool] = Field(
        None, description="Optional boolean: ready-for-use. A False value is "
                          "contradictory for a ready-for-use rule and leaves "
                          "the numeric comparison unresolved.")
    assessment_date: Optional[str] = Field(
        None, description="Optional ISO-8601 assessment date (YYYY-MM-DD). "
                          "Required for a transition/effective-date boundary; "
                          "a supplied but invalid date is rejected (never "
                          "silently ignored). Uses the source-version basis for "
                          "the assessment date.")


class FormulaRequest(BaseModel):
    """Request contract for ``POST /v1/check-formula``.

    ``ingredients`` is required and non-empty, with a maximum of 200 entries.
    The route parses the raw JSON body with ``parse_float=Decimal`` to preserve
    decimal precision; validation is delegated to the shared library
    ``eu_ingredient_linter.evaluate_formula`` (never a second evaluator).
    """

    ingredients: List[FormulaIngredientModel] = Field(
        ..., min_length=1, max_length=200,
        description="Structured-formula ingredients (1-200).")


class FormulaEvaluationModel(BaseModel):
    """Per-ingredient formula evaluation (separate rule/context/numeric fields)."""

    ingredient: str
    resolved_name: Optional[str] = None
    resolution: Optional[str] = None
    is_in_dataset: bool
    determination: str
    rule_status: Optional[str] = None
    annex: Optional[str] = None
    applicable_context: Optional[str] = None
    numeric_comparison: Optional[str] = None
    submitted_value: Optional[str] = None
    unit: Optional[str] = None
    basis: Optional[str] = None
    cap: Optional[str] = None
    unresolved_requirements: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    verdict: str
    rules: List[RuleModel] = Field(default_factory=list)
    # Explicit source-backed group membership: the member's own rules are in
    # ``rules``; the group's rules surfaced via a membership are in
    # ``linked_rules`` (never merged). ``membership`` records the source group
    # identity/reference/row. A member with linked evidence is NOT "no evidence".
    membership: List[Dict[str, Any]] = Field(default_factory=list)
    linked_rules: List[RuleModel] = Field(default_factory=list)
    source_rule: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    # Classification of a retained Annex II prohibition. "unambiguous" means a
    # definitive ban (triggers exit 1); "exception_unresolved" /
    # "scope_unresolved" mean the scope/exception applicability is unresolved
    # (never treated as a definitive prohibition). Mirrors the shared evaluator.
    prohibition_finding: Optional[str] = None
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence, surfaced as
    # supplementary evidence and never replacing the CosIng rules.
    entry70: Optional[Dict[str, Any]] = None
    # The raw submitted context (product_type/body_area/professional_use/
    # ready_for_use/unit/basis/assessment_date), preserved exactly as supplied.
    # Absent vs false vs true are distinct, and it is never reconstructed from
    # the rule's required context.
    submitted_context: Dict[str, Any] = Field(default_factory=dict)


class FormulaCheckResponse(BaseModel):
    """Full structured-formula check, delegating to ``evaluate_formula``."""

    evaluations: List[FormulaEvaluationModel]
    data_updated_at: Optional[str] = None
    registry_size: int
    coverage: CoverageModel
    disclaimer: str = "Regulatory information, not legal advice."
    metadata: MetadataEnvelope


class HealthResponse(BaseModel):
    status: str
    snapshot: str
    version: Optional[str]
    content_hash: Optional[str]
    data_updated_at: Optional[str]
    coverage: CoverageModel
    count_substances: int
    count_verified_rules: int
    disclaimer: str = "Regulatory information, not legal advice."
    note: str = "Service health is not an ingredient compliance determination."


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    disclaimer: str = "Regulatory information, not legal advice."
    metadata: Optional[MetadataEnvelope] = None
