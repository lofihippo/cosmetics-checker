"""Core data model for eu-reg-lists.

A **substance** is a stable chemical identity. It may carry zero or more
**rules** (regulatory determinations from a specific source annex). Rules are
separate from identity: the same substance can be subject to both a cosmetic
rule (e.g. Annex V) and a food rule (e.g. 1333/2008), and both are retained.

Honesty semantics:
  * ``determination == "unknown"`` means the substance is a *recognised identity*
    but carried NO verified rule from an official source export. This is NOT a
    "no restriction" claim.
  * ``determination in {"banned","restricted","allowed_with_conditions"}`` means
    at least one rule was parsed from a source export and assigned a status.
  * Every rule records ``regulation``, ``source_url``, ``source_updated_at`` and
    ``resolution`` (how the status was derived).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Status ranks used to consolidate multiple rules (highest wins for headline).
# ``listed`` (SVHC Candidate List listing info) sits between ``allowed_with_conditions``
# and ``restricted``: it is a stronger evidence signal than an unrestricted
# conditional entry but is NOT a numerical restriction or a prohibition.
RANK = {"unknown": 0, "allowed_with_conditions": 1, "listed": 2, "restricted": 3, "banned": 4}

DETERMINATIONS = ("unknown", "allowed_with_conditions", "listed", "restricted", "banned")


@dataclass
class Rule:
    """A single regulatory determination from a source export."""

    status: Optional[str]  # banned | restricted | listed | allowed_with_conditions
    annex: Optional[str]           # Annex II..VI | SVHC | Annex XIV | Annex XVII | Food
    regulation: Optional[str]      # human-readable regulation citation
    source_url: Optional[str]
    source_updated_at: Optional[str]  # upstream pub/update date (None if unknown)
    conditions: Optional[str] = None
    resolution: Optional[str] = None   # how the status was derived (explicit)
    effective_date: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "annex": self.annex,
            "regulation": self.regulation, "source_url": self.source_url,
            "source_updated_at": self.source_updated_at,
            "conditions": self.conditions, "resolution": self.resolution,
            "effective_date": self.effective_date, "raw": self.raw,
        }


@dataclass
class Substance:
    """A stable chemical identity with zero or more rules.

    ``member_of`` records explicit source-backed group *membership*: this
    identity is named (via the source's ``Name of Common Ingredients Glossary``
    / ``Identified INGREDIENTS`` columns) in the source entry of a group
    substance. The group's own rules are surfaced by the checker as *linked*
    evidence — never merged into this identity's identifiers, and never a claim
    that every group condition applies or is satisfied.
    """

    id: Optional[str]
    name: Optional[str]
    aliases: List[str] = field(default_factory=list)
    cas: Optional[str] = None
    ec: Optional[str] = None
    e_number: Optional[str] = None
    ci: Optional[str] = None
    determination: str = "unknown"
    rules: List[Rule] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)  # source ids that populated it
    member_of: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "aliases": self.aliases,
            "cas": self.cas, "ec": self.ec, "e_number": self.e_number,
            "ci": self.ci, "determination": self.determination,
            "rules": [r.to_dict() for r in self.rules],
            "sources": self.sources,
            "member_of": list(self.member_of),
        }

    def __json__(self) -> Dict[str, Any]:
        return self.to_dict()

    @property
    def has_verified_rule(self) -> bool:
        return self.determination != "unknown" and bool(self.rules)

    @property
    def strongest_rule(self) -> Optional[Rule]:
        if not self.rules:
            return None
        return max(self.rules, key=lambda r: RANK.get(r.status or "unknown", 0))

    def keys(self) -> List[str]:
        out = [self.name.strip().lower()] if self.name else []
        out += [str(a).strip().lower() for a in self.aliases if str(a).strip()]
        return [k for k in out if k]


@dataclass
class CheckResult:
    """Result of checking a single ingredient token against the registry.

    ``is_in_dataset`` is True when the token resolves to a known substance
    identity. ``determination`` is "unknown" for a recognised-but-unverified
    substance (never an affirmative OK). For a not-found token the whole record
    is NULL (``is_in_dataset=False``, ``determination="unknown"``).
    """

    ingredient: str
    is_in_dataset: bool
    determination: str
    status: Optional[str] = None
    annex: Optional[str] = None
    regulation: Optional[str] = None
    source_url: Optional[str] = None
    data_updated_at: Optional[str] = None
    conditions: Optional[str] = None
    rules: List[Rule] = field(default_factory=list)
    cas: Optional[str] = None
    ec: Optional[str] = None
    e_number: Optional[str] = None
    ci: Optional[str] = None
    resolution: Optional[str] = None
    cased_name: Optional[str] = None
    # Machine-readable evaluation state for the ingredient's rules. Always
    # present for a resolved substance. For an Annex III restricted rule the
    # distinction between "restriction identified" and "violation evaluated"
    # is preserved: without concrete product concentration/context the rule is
    # unevaluated (never a "compliant" claim).
    evaluation: Optional[str] = None
    evaluation_reason: Optional[str] = None
    # Classification of a retained Annex II prohibition. ``unambiguous`` means
    # an unconditional (definitive) prohibition; ``scope_unresolved`` means the
    # source scope/exception applicability is unresolved (never presented as a
    # definitive prohibition). Mirrors the formula evaluator's field.
    prohibition_finding: Optional[str] = None
    # Explicit source-backed group membership: this ingredient is a named
    # member of one or more group source entries. ``linked_rules`` carries the
    # group's own rules (surface as evidence, never merged), and ``membership``
    # records the group source identity/reference and the member-name evidence.
    membership: List[Dict[str, Any]] = field(default_factory=list)
    linked_rules: List[Rule] = field(default_factory=list)
    # Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence. ``entry70`` is
    # None unless the resolved identity is a D4/D5/D6 substance; when present it
    # is the raw entry-70 verdict dict from :mod:`.annex17_entry70`. It NEVER
    # replaces a CosIng prohibition and is never a category violation on its
    # own: a label-only check has no concentration/product-type context, so it
    # surfaces the entry-70 finding as identified-but-unevaluated.
    entry70: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ingredient": self.ingredient,
            "is_in_dataset": self.is_in_dataset,
            "determination": self.determination,
            "status": self.status,
            "annex": self.annex,
            "regulation": self.regulation,
            "source_url": self.source_url,
            "data_updated_at": self.data_updated_at,
            "conditions": self.conditions,
            "rules": [r.__dict__ for r in self.rules],
            "cas": self.cas,
            "ec": self.ec,
            "e_number": self.e_number,
            "ci": self.ci,
            "resolution": self.resolution,
            "cased_name": self.cased_name,
            "evaluation": self.evaluation,
            "evaluation_reason": self.evaluation_reason,
            "prohibition_finding": self.prohibition_finding,
            "membership": self.membership,
            "linked_rules": [r.__dict__ for r in self.linked_rules],
            "entry70": self.entry70,
        }
