"""REACH Annex XVII entry 70 — bounded, source-bound applicability evaluator.


Scope
-----
This module implements ONLY the explicitly supported cosmetic branches of
REACH Annex XVII entry 70 from Commission Regulation (EU) 2024/1328 (amending
Annex XVII to Regulation (EC) No 1907/2006 as regards D4, D5 and D6). It is a
bounded, reviewable mapping — NOT a general legal-text parser.

Supported branches (cosmetics):
  * paragraph 3(a): D4 and D5 in **wash-off cosmetic products** — the
    paragraph 1, point (c) restriction (0,1 % by weight or more in mixtures)
    applies from **31 January 2020**.
  * paragraph 3(b): for **all cosmetic products other than 3(a)** — paragraph 1
    applies from **6 June 2027**.

Unsupported branches (explicitly NOT implemented):
  * dry cleaning (paragraph 2 / 3(e) / 7),
  * devices (paragraph 3(c) / 6(c) / 6(d)) and medicinal/in vitro diagnostic
    products (paragraph 3(d)),
  * industrial uses and paragraph 6 residues/silicone-polymer derogations
    (paragraphs 4, 5, 6), and laboratory/solvent/art-restoration uses.

The evaluator only ever answers whether a *cosmetic* product's supplied
context would trigger the paragraph 1 market-placing restriction. A matched
substance is never automatically a violation, and a restriction is never a
personal-harm claim.

Honesty semantics
-----------------
  * Every returned verdict carries the exact source threshold (0,1 % by weight),
    the operator (>=), the applicable effective date, the product/cosmetic
    branch and provenance.
  * Missing concentration, product type, or assessment date → ``missing_context``
    (never assumed, never "clean").
  * A concentration not expressed on the marketed (ready-for-use) basis, or a
    ``ready_for_use=false`` value, → ``missing_context`` (a raw-material
    percentage does not establish the marketed cosmetic concentration).
  * An unsupported use (dry cleaning, devices, industrial, residue derogation)
    → ``unsupported_use``.
  * A date before the branch's effective date → ``transition_not_effective``
    (the restriction has not yet triggered for that date), never a violation.
  * ``within`` is only the numeric comparison against the market trigger; it is
    NOT a compliance certificate, and warnings/other EU obligations stay
    unevaluated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

DISCLAIMER = "Regulatory information, not legal advice."

# --- Source identity -------------------------------------------------------
# Commission Regulation (EU) 2024/1328, OJ L, 17.5.2024, amending Annex XVII to
# Regulation (EC) No 1907/2006 as regards D4, D5 and D6.
# Canonical act identifier: CELEX 32024R1328 / ELI http://data.europa.eu/eli/reg/2024/1328/oj
ENTRY70_SOURCE: Dict[str, Any] = {
    "regulation": "(EU) 2024/1328",
    "annex": "Annex XVII",
    "entry": "70",
    "celex": "32024R1328",
    "eli": "http://data.europa.eu/eli/reg/2024/1328/oj",
    "publisher_act": "Commission Regulation (EU) 2024/1328",
    "source_url": (
        "https://www.moew.government.bg/static/media/ups/articles/attachments/"
        "OJ_L_202401328_EN_TXT3bb31a3161bcab20b2f3051abd65574c.pdf"
    ),
    # The retrieval URL is distinct from the canonical publisher document. The
    # original bytes, SHA-256 and metadata of the retained official copy are
    # recorded beside the PDF in
    # .local-reference/independent-annex-xvii-review-2026-10-13/.
    "document_sha256": (
        "2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966"
    ),
    "publication_date": "2024-05-17",
    # This document is an amending act. The consolidated entry-70 text from the
    # applicable consolidated REACH act must be checked for later amendments
    # before this mapping is used a second time (see VERIFY_CONSOLIDATED below).
    "document_kind": "amending_act",
    # Citation fields surfaced in the report evidence rows (not part of the
    # release-gating contract; see ``_mapping_compatible``).
    "source_kind": "reach-annex-xvii-entry70",
    # Reproducibility contract (versioned; part of the accepted source record).
    # This is the release-gating contract: a snapshot advertises/evaluates
    # entry 70 only when its verified_sources record matches this mapping
    # version AND document hash exactly (see ``source_is_active``).
    "mapping_version": 1,
    "supported_substances": ["D4", "D5", "D6"],
    "supported_branches": ["washoff_cosmetic", "other_cosmetic"],
    # Official-document paragraph/page locators for the operative text as
    # retained in the amending act (the OJ PDF pages 5-6, AN NEX page, titled
    # "Entry 70 of Annex XVII ... is replaced by the following").
    "locators": {
        "paragraph_1": "Annex page 5, paragraph 1",
        "paragraph_3a": "Annex page 5, paragraph 3(a)",
        "paragraph_3b": "Annex page 5, paragraph 3(b)",
        "paragraph_3c": "Annex page 5, paragraph 6 (devices)",
        "paragraph_6": "Annex page 6, paragraph 6 (residues)",
    },
}

# Later-amendment / consolidation check obligation. The mapping below is built
# from the amending act and is only valid for the state it records. Re-verify
# the consolidated entry 70 before a subsequent refresh.
VERIFY_CONSOLIDATED = (
    "Consolidation check required before reuse: the retained source is the "
    "amending act (Regulation (EU) 2024/1328, CELEX 32024R1328). Checked-as-of "
    "2026-10-13 the official-document relationship review records Italian, "
    "German and French corrigenda for this act; the English text and the "
    "retained mapping were not shown to be changed thereby (recorded as a note, "
    "not an assurance). Before a subsequent refresh, re-verify the consolidated "
    "entry 70 for any later amending act/corrigendum and record the consolidated "
    "CELEX revision identifier for the assessment date."
)

# Release-gating source kind for the entry 70 source-backed mapping. This is a
# distinct, non-ECHA source (the official EU legal act / OJ amending act), so it
# is NOT part of the deferred ECHA set. A snapshot advertises/evaluates entry 70
# only when ``verified_sources`` records this key as present AND accepted AND
# carrying a compatible mapping contract (see ``source_is_active``).
ENTRY70_SOURCE_KIND = "reach-annex-xvii-entry70"

# --- Substance identities (paragraph 1 preamble) --------------------------
# D4 = Octamethylcyclotetrasiloxane CAS 556-67-2 EC 209-136-7
# D5 = Decamethylcyclopentasiloxane  CAS 541-02-6 EC 208-764-9
# D6 = Dodecamethylcyclohexasiloxane CAS 540-97-6 EC 208-762-8
SUBSTANCES = {
    "D4": {
        "name": "Octamethylcyclotetrasiloxane",
        "short": "D4",
        "cas": "556-67-2",
        "ec": "209-136-7",
    },
    "D5": {
        "name": "Decamethylcyclopentasiloxane",
        "short": "D5",
        "cas": "541-02-6",
        "ec": "208-764-9",
    },
    "D6": {
        "name": "Dodecamethylcyclohexasiloxane",
        "short": "D6",
        "cas": "540-97-6",
        "ec": "208-762-8",
    },
}

# --- Paragraph 1 market trigger -------------------------------------------
MARKET_THRESHOLD = Decimal("0.1")          # % by weight
MARKET_OPERATOR = ">="                    # "equal to or greater than 0,1 % by weight"
MARKET_OPERATOR_TEXT = "equal to or greater than"
BASE_EFFECTIVE_DATE = date(2026, 6, 6)    # paragraph 1 default (non-cosmetic context)

# --- Concentration-basis contract -------------------------------------------
# The paragraph 1 market trigger is stated for the substance "as ... 0,1 % by
# weight" in the mixture the cosmetic product places on the market. A supported
# comparison therefore requires the concentration to be expressed on the
# MARKETED / finished cosmetic-product ("ready for use") basis, percent by
# weight. A raw-material or other-supplied basis does NOT establish the
# marketed-mixture concentration: it stays unresolved (never silently converted
# or assumed). This mirrors the shared bounded evaluator's strict basis gating.
MARKETED_BASIS = "ready_for_use"
_BASIS_ALIASES: Dict[str, str] = {
    "ready_for_use": MARKETED_BASIS,
    "ready for use": MARKETED_BASIS,
    "ready_for_use_preparation": MARKETED_BASIS,
    "ready_for_use_prepartion": MARKETED_BASIS,
    "marketed_product": MARKETED_BASIS,
    "marketed": MARKETED_BASIS,
    "finished_product": MARKETED_BASIS,
    "finished": MARKETED_BASIS,
    "product": MARKETED_BASIS,
    "mixture": MARKETED_BASIS,
    "cosmetic_product": MARKETED_BASIS,
    "w/w product": MARKETED_BASIS,
}


def _canonical_basis(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _BASIS_ALIASES.get(value.strip().lower())

# --- Cosmetics branches (paragraph 3) --------------------------------------
# 3(a) wash-off cosmetics: D4, D5; effective 2020-01-31; paragraph 1(c) applies.
# 3(b) other cosmetics (all cosmetic products other than 3(a)): effective
#      2027-06-06; paragraph 1 applies.
WASHOFF_EFFECTIVE_DATE = date(2020, 1, 31)
OTHER_COSMETIC_EFFECTIVE_DATE = date(2027, 6, 6)


@dataclass
class Entry70Verdict:
    """The assessment of one ingredient against the supported cosmetic branches."""
    ingredient: str
    substance_short: Optional[str] = None    # D4 / D5 / D6 (or None if unknown)
    supported: bool = False                  # True only if a cosmetic branch applies
    state: Optional[str] = None              # see VERDICT_STATES
    reason: Optional[str] = None
    threshold: Optional[str] = None          # "0.1 % by weight"
    operator: Optional[str] = None           # ">="
    branch: Optional[str] = None             # "washoff_cosmetic" | "other_cosmetic"
    effective_date: Optional[str] = None     # applicable branch effective date (ISO)
    assessment_date: Optional[str] = None    # submitted assessment date (ISO)
    submitted_percent: Optional[str] = None  # submitted concentration (% by weight)
    product_type: Optional[str] = None       # submitted cosmetic-product type
    basis: Optional[str] = None              # submitted concentration basis
    ready_for_use: Optional[bool] = None     # submitted ready-for-use flag
    source: Dict[str, Any] = field(default_factory=lambda: dict(ENTRY70_SOURCE))
    provenance: Dict[str, Any] = field(default_factory=dict)
    unresolved_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "ingredient": self.ingredient,
            "substance_short": self.substance_short,
            "supported": self.supported,
            "state": self.state,
            "reason": self.reason,
            "threshold": self.threshold,
            "operator": self.operator,
            "branch": self.branch,
            "effective_date": self.effective_date,
            "assessment_date": self.assessment_date,
            "submitted_percent": self.submitted_percent,
            "product_type": self.product_type,
            "basis": self.basis,
            "ready_for_use": self.ready_for_use,
            "source": self.source,
            "provenance": self.provenance,
            "unresolved_requirements": self.unresolved_requirements,
        }
        # Citation fields surfaced in report evidence rows (always present).
        out["source_kind"] = self.source.get("source_kind") or ENTRY70_SOURCE["source_kind"]
        out["data_updated_at"] = (
            self.provenance.get("data_updated_at")
            or self.source.get("data_updated_at"))
        out["document_sha256"] = self.source.get("document_sha256")
        return out


# Verdict states:
#   applicable_exceeds | applicable_within | transition_not_effective
#   | missing_context | unsupported_use | not_entry70_substance
VERDICT_STATES = (
    "applicable_exceeds",
    "applicable_within",
    "transition_not_effective",
    "missing_context",
    "unsupported_use",
    "not_entry70_substance",
    "source_not_active",
)


# --- Identity resolution ---------------------------------------------------
def identify_substance(name_or_id: str) -> Optional[str]:
    """Return the D4/D5/D6 short code for a supplied name/identifier, else None.

    Matches (exact, case-insensitive) canonical names, D4/D5/D6 short codes,
    and CAS/EC identifiers. Never fuzzy-matches.
    """
    key = str(name_or_id or "").strip().lower()
    for short, info in SUBSTANCES.items():
        if key in (short.lower(), info["name"].lower(),
                   info["cas"].lower(), info["ec"].lower()):
            return short
        # Accept a bare EC number with an optional 'EC ' prefix.
        for ec_full in (info["ec"].lower(), "ec " + info["ec"].lower()):
            if key == ec_full:
                return short
    return None


# --- Product-type / context normalization -----------------------------------
# Only EXPLICIT cosmetic-branch product types are accepted. The source (entry 70,
# paragraph 3) defines exactly two cosmetic branches: 3(a) wash-off cosmetic
# products (D4, D5) and 3(b) all other cosmetic products (D4, D5, D6). A
# product-type must state which branch applies. Generic/ambiguous text ("cosmetic",
# "other", "leave-on on its own") is NOT enough: it does not establish whether
# the product is washed off, so it stays unresolved (never assumed). "leave-on"
# is an explicit non-wash-off cosmetic product, so it maps to the 3(b) branch.
_COSMETIC_PRODUCT_ALIASES: Dict[str, str] = {
    "washoff": "washoff_cosmetic",
    "wash-off": "washoff_cosmetic",
    "wash_off": "washoff_cosmetic",
    "washoff_cosmetic": "washoff_cosmetic",
    "wash-off-cosmetic": "washoff_cosmetic",
    "wash_off_cosmetic": "washoff_cosmetic",
    "rinse-off": "washoff_cosmetic",
    "rinse_off": "washoff_cosmetic",
    "rinseoff": "washoff_cosmetic",
    "leave-on": "other_cosmetic",
    "leave_on": "other_cosmetic",
    "leaveon": "other_cosmetic",
    "other-cosmetic": "other_cosmetic",
    "other_cosmetic": "other_cosmetic",
    "non-wash-off": "other_cosmetic",
    "non_wash_off": "other_cosmetic",
    "nonwashoff": "other_cosmetic",
    "non-wash-off-cosmetic": "other_cosmetic",
    "non_wash_off_cosmetic": "other_cosmetic",
}

# Explicit uses that ARE covered by entry 70 but are NOT implemented by this
# tool. They are recognised so they return ``unsupported_use`` (a deliberate
# limitation), not ``missing_context`` (which would merely be missing data).
_UNSUPPORTED_USE_ALIASES: Dict[str, str] = {
    "dry-cleaning": "dry_cleaning",
    "dry_cleaning": "dry_cleaning",
    "textile-leather-fur-dry-cleaning": "dry_cleaning",
    "device": "device",
    "devices": "device",
    "artifical_device": "device",
    "medicinal-product": "medicinal_product",
    "medicinal_product": "medicinal_product",
    "veterinary-medicinal": "veterinary_medicinal",
    "veterinary_medicinal": "veterinary_medicinal",
    "industrial-use": "industrial_use",
    "industrial_use": "industrial_use",
    "residue-from-silicone-polymer": "residue_derogation",
    "residue_derogation": "residue_derogation",
    "laboratory-reagent": "laboratory_reagent",
    "laboratory_reagent": "laboratory_reagent",
    "art-restoration": "art_restoration",
    "art_restoration": "art_restoration",
}


def _canonical_product(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _COSMETIC_PRODUCT_ALIASES.get(value.strip().lower())


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


# The mapping contract is versioned and carries the full reproducible identity:
# the official document bytes (SHA-256), the document kind, the supported
# substances/branches and the source locators. A legitimate semantic mapping
# change MUST have a different reproducible identity (a new ``mapping_version``
# and/or a new ``mapping_content_hash``), so a re-built snapshot from the same
# accepted raw inputs retains equivalent activation and results, while a
# changed mapping is never silently accepted as the old identity.
MAPPING_SCHEMA_VERSION = 1

# The retained official document file name inside the entry70 raw source dir.
ENTRY70_DOCUMENT_FILE = "OJ_L_202401328_EN.pdf"
# The retained mapping contract file name inside the entry70 raw source dir.
ENTRY70_MAPPING_FILE = "entry70-mapping.json"


def _canonical_mapping_fields(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Return the deterministic, semantic mapping fields."""
    return {
        "mapping_version": mapping.get("mapping_version"),
        "document_sha256": mapping.get("document_sha256"),
        "supported_substances": sorted(mapping.get("supported_substances") or []),
        "supported_branches": sorted(mapping.get("supported_branches") or []),
        "document_kind": mapping.get("document_kind"),
        "locators": dict(
            sorted((k, str(v)) for k, v in (mapping.get("locators") or {}).items())
            if isinstance(mapping.get("locators"), dict) else [],
        ),
    }


def mapping_content_hash(mapping: Dict[str, Any]) -> str:
    """Return a deterministic SHA-256 identity over the semantic mapping fields.

    A change to any supported substance/branch, locator, document hash or
    mapping version changes this identity, so a rebuilt snapshot from the same
    accepted raw inputs reproduces an equivalent activation while a mapping
    change is never silently accepted as the old identity.
    """
    import hashlib
    blob = json.dumps(_canonical_mapping_fields(mapping),
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _mapping_compatible(record: Optional[Dict[str, Any]]) -> bool:
    """Return True only when an accepted source record carries a COMPATIBLE
    mapping contract (mapping version + supported substances/branches + a
    present document hash).

    This is the release-gating check: evaluation and coverage of entry 70 must
    derive from an accepted, validated, internally-consistent source/mapping
    contract, never from the module's existence alone. An empty or historical
    CosIng-only snapshot has no such record and therefore neither advertises nor
    evaluates entry 70. Document-authenticity is bound to the RETAINED official
    document bytes by the snapshot integrity verifier (hash matching the raw
    mapping file and the recorded mapping), so a metadata mapping that disagrees
    with the retained bytes is never active. The supported-branch/substance set
    and mapping version must match the module's canonical contract; only a
    document that is retained and bound reproduce a different (legitimate)
    identity.
    """
    if not record:
        return False
    if not (record.get("accepted") and record.get("present")):
        return False
    mapping = record.get("mapping") or {}
    if not isinstance(mapping, dict):
        return False
    if not mapping.get("document_sha256"):
        return False
    if mapping.get("mapping_version") != ENTRY70_SOURCE["mapping_version"]:
        return False
    subs = mapping.get("supported_substances") or []
    if not isinstance(subs, list):
        return False
    if set(subs) != set(ENTRY70_SOURCE["supported_substances"]):
        return False
    branches = mapping.get("supported_branches") or []
    if not isinstance(branches, list):
        return False
    if set(branches) != set(ENTRY70_SOURCE["supported_branches"]):
        return False
    return True

def source_is_active(verified_sources: Optional[Dict[str, Any]]) -> bool:
    """Return True when the loaded snapshot carries an accepted, compatible
    entry 70 source/mapping contract (see ``_mapping_compatible``)."""
    src = verified_sources or {}
    record = src.get(ENTRY70_SOURCE_KIND)
    if isinstance(record, dict):
        return _mapping_compatible(record)
    return False


def source_record(verified_sources: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the accepted entry 70 source record, or None when not active."""
    if not source_is_active(verified_sources):
        return None
    return (verified_sources or {}).get(ENTRY70_SOURCE_KIND)


def accepted_source_mapping(verified_sources: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the active entry 70 source/mapping contract for the report, or
    None when entry 70 is not source-backed in this snapshot."""
    record = source_record(verified_sources)
    if record is None:
        return None
    return {
        "source_kind": ENTRY70_SOURCE_KIND,
        "document_sha256": ENTRY70_SOURCE["document_sha256"],
        "mapping_version": ENTRY70_SOURCE["mapping_version"],
        "supported_substances": list(ENTRY70_SOURCE["supported_substances"]),
        "supported_branches": list(ENTRY70_SOURCE["supported_branches"]),
        "locators": dict(ENTRY70_SOURCE["locators"]),
        "source_url": ENTRY70_SOURCE["source_url"],
        "regulation": ENTRY70_SOURCE["regulation"],
        "celex": ENTRY70_SOURCE["celex"],
        "eli": ENTRY70_SOURCE["eli"],
        "data_updated_at": record.get("data_updated_at"),
        "retrieved_at": record.get("retrieved_at"),
    }


# --- Evaluator --------------------------------------------------------------
def evaluate_entry70(
    name_or_id: str,
    *,
    percent: Optional[Decimal] = None,
    unit: Optional[str] = None,
    product_type: Optional[str] = None,
    basis: Optional[str] = None,
    ready_for_use: Optional[bool] = None,
    assessment_date: Optional[str] = None,
    verified_sources: Optional[Dict[str, Any]] = None,
    snapshot_data_updated_at: Optional[str] = None,
) -> Entry70Verdict:
    """Evaluate one ingredient against the supported cosmetic branches of entry 70.

    ``percent`` is a concentration in percent by weight (only "%" / "w/w" is
    accepted; other units are unsupported and stay unresolved). ``product_type``
    is the cosmetic-product type ("wash-off" vs "other"). ``basis`` is the
    concentration basis; only a marketed / finished (ready-for-use) basis is a
    supported comparison (a raw-material or incompatible basis stays unresolved).
    ``ready_for_use``, when supplied as False, is contradictory to the marketed
    basis and leaves the comparison unresolved. ``assessment_date`` is
    the date of the assessment (used to decide transition boundaries).

    ``verified_sources`` is the snapshot's ``verified_sources`` dict. When entry
    70 is NOT source-backed by an accepted, compatible mapping contract in that
    snapshot, evaluation is refused (``state="source_not_active"``) so an empty
    or historical CosIng-only snapshot never advertises or evaluates entry 70.

    ``snapshot_data_updated_at`` is the effective LOADED snapshot timestamp. The
    promoted source record may lack a per-source ``data_updated_at`` (the value
    is then null on the source record), so the effective snapshot time is passed
    explicitly and threaded into the evidence provenance. This keeps snapshot
    time distinct from ``retrieved_at`` (download time) and from the publication
    date on ``source``. Callers that omit it fall back to the source record's
    own timestamp (a degraded, legacy path used by direct evaluator tests).
    """
    short = identify_substance(name_or_id)
    _src_record = ((verified_sources or {}).get(ENTRY70_SOURCE_KIND) or {})
    provenance: Dict[str, Any] = {
        "document": ENTRY70_SOURCE["document_sha256"],
        "source_url": ENTRY70_SOURCE["source_url"],
        "publisher_act": ENTRY70_SOURCE["publisher_act"],
        "paragraph_branch_verified": True,
        "consolidation_check": VERIFY_CONSOLIDATED,
        # Effective snapshot time (the loaded snapshot's data_updated_at),
        # threaded explicitly so a promoted source record without a per-source
        # timestamp still carries the correct snapshot freshness. ``retrieved_at``
        # (download time) and the source ``publication_date`` stay separate.
        "data_updated_at": (
            snapshot_data_updated_at or _src_record.get("data_updated_at")),
        "retrieved_at": _src_record.get("retrieved_at"),
        "row_reference": ENTRY70_SOURCE["locators"].get("paragraph_1"),
    }

    if short is None:
        return Entry70Verdict(
            ingredient=name_or_id, supported=False,
            state="not_entry70_substance",
            reason=f"{name_or_id!r} is not D4, D5 or D6; entry 70 does not "
                   "apply as a substance match.",
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # Release-gating: only evaluate entry 70 when the snapshot carries an
    # accepted, compatible source/mapping contract for it. This prevents an
    # empty or historical CosIng-only snapshot from producing an entry 70
    # finding merely because the evaluator module exists.
    if not source_is_active(verified_sources):
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="source_not_active",
            reason="Entry 70 is not source-backed in this snapshot: the "
                   "accepted REACH Annex XVII entry 70 source/mapping contract "
                   "is absent, so entry 70 is not evaluated here.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR,
            submitted_percent=_format_percent(percent), product_type=product_type,
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    submitted_percent = _format_percent(percent)
    unit_canon = _canonical_unit(unit)

    # A recognised but non-cosmetic entry-70 use (dry cleaning, devices,
    # industrial, residue derogation) is an explicit unsupported use for this
    # tool — never silently evaluated as if it were a cosmetic branch.
    unsupported_use = (
        _UNSUPPORTED_USE_ALIASES.get(product_type.strip().lower())
        if product_type else None
    )
    if unsupported_use:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="unsupported_use",
            reason=f"Product use {product_type!r} ({unsupported_use.replace('_', ' ')}) "
                   "is covered by entry 70 but is not a supported cosmetic branch "
                   "of this tool; not evaluated.",
            submitted_percent=submitted_percent, product_type=product_type,
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # Cosmetic product-type is mandatory for the supported cosmetic branches.
    product = _canonical_product(product_type)
    if product is None:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason="Cosmetic product type (wash-off vs other) not supplied; "
                   "which paragraph-3 cosmetic branch applies is unresolved. "
                   "Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR,
            submitted_percent=submitted_percent, product_type=product_type,
            unresolved_requirements=[
                "Cosmetic product type (wash-off vs other cosmetic product)"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # Only wash-off and other-cosmetic branches are implemented as cosmetic.
    # Everything else (dry cleaning / devices / industrial / residue derogation)
    # is an unsupported use for this tool — never silently evaluated.
    if product not in ("washoff_cosmetic", "other_cosmetic"):
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="unsupported_use",
            reason=f"Product use {product_type!r} is not a supported cosmetic "
                   "branch of entry 70; not evaluated.",
            submitted_percent=submitted_percent, product_type=product_type,
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    branch = product
    # Paragraph 3(a) applies only to D4 and D5 in wash-off cosmetic products.
    # Paragraph 3(b) covers "all cosmetic products other than the ones mentioned
    # in paragraph 3(a)": D6 is NOT mentioned in 3(a), so a cosmetic product
    # containing D6 falls under paragraph 3(b), effective 6 June 2027. This
    # evaluator only evaluates the 3(b) branch when the caller supplies an
    # explicit "other / non-wash-off" product type; a "wash-off" product type
    # for D6 cannot be mapped to 3(a) and is recorded here as an explicit,
    # documented exclusion rather than silently re-interpreted.
    if branch == "washoff_cosmetic" and short not in ("D4", "D5"):
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="unsupported_use",
            reason=(
                f"Paragraph 3(a) covers D4 and D5 in wash-off cosmetic products "
                f"only, not {short}. {short} in a cosmetic product (including a "
                "wash-off cosmetic product) falls under paragraph 3(b), which "
                "this evaluator supports only when the product type explicitly "
                "states non-wash-off / other cosmetic; the supplied "
                f"{product_type!r} cannot be mapped. Not evaluated (documented "
                "exclusion)."
            ),
            threshold="0.1 % by weight", operator=MARKET_OPERATOR,
            branch=branch, submitted_percent=submitted_percent,
            product_type=product_type, source=dict(ENTRY70_SOURCE),
            provenance=provenance,
        )

    effective = (WASHOFF_EFFECTIVE_DATE if branch == "washoff_cosmetic"
                 else OTHER_COSMETIC_EFFECTIVE_DATE)

    # Concentration is mandatory for the market trigger comparison.
    if percent is None:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason="No concentration (% by weight) supplied; the paragraph 1 "
                   "market trigger cannot be compared. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=None,
            product_type=product_type,
            unresolved_requirements=["Concentration (% by weight)"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    if unit_canon != "%":
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason=f"Unit {unit or 'unspecified'!r} is not { '%'!r} by weight; "
                   "the concentration basis is unsupported. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=submitted_percent,
            product_type=product_type,
            unresolved_requirements=["Supported concentration unit (% by weight)"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # The market trigger only compares a MARKETED / finished cosmetic-product
    # concentration. A missing or incompatible basis (e.g. raw_material, or
    # ready_for_use=false) does not establish the marketed mixture concentration
    # and stays unresolved (never silently converted or assumed).
    basis_canon = _canonical_basis(basis)
    if basis is None:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason="Concentration basis not supplied; the paragraph 1 market "
                   "trigger only compares a marketed (ready-for-use) "
                   "concentration. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=submitted_percent,
            product_type=product_type,
            unresolved_requirements=["Marketed (ready-for-use) concentration basis"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )
    if basis_canon != MARKETED_BASIS:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason=f"Concentration basis {basis!r} is not the marketed "
                   "(ready-for-use) basis the paragraph 1 market trigger "
                   "compares; the submitted value does not establish the "
                   "marketed cosmetic-product concentration. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=submitted_percent,
            product_type=product_type,
            unresolved_requirements=["Marketed (ready-for-use) concentration basis"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )
    if ready_for_use is False:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason="ready_for_use is false but the paragraph 1 market trigger "
                   "compares a marketed (ready-for-use) concentration; "
                   "contradictory context. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=submitted_percent,
            product_type=product_type,
            unresolved_requirements=["Marketed (ready-for-use) concentration basis"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # Assessment-date transition boundary.
    assessed = _parse_date(assessment_date)
    if assessed is None:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=False,
            state="missing_context",
            reason="Assessment date not supplied; the paragraph 3 transition "
                   "boundary cannot be decided. Not evaluated.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(), submitted_percent=submitted_percent,
            product_type=product_type,
            unresolved_requirements=["Assessment date"],
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    if assessed < effective:
        return Entry70Verdict(
            ingredient=name_or_id, substance_short=short, supported=True,
            state="transition_not_effective",
            reason=f"For {branch.replace('_', ' ')} cosmetics, paragraph 1 "
                   f"applies from {effective.isoformat()}; assessment date "
                   f"{assessed.isoformat()} is before that, so the restriction "
                   "has not yet applied.",
            threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
            effective_date=effective.isoformat(),
            assessment_date=assessed.isoformat(),
            submitted_percent=submitted_percent, product_type=product_type,
            source=dict(ENTRY70_SOURCE), provenance=provenance,
        )

    # Numeric comparison with exact Decimal (>=).
    exceeds = percent >= MARKET_THRESHOLD
    state = "applicable_exceeds" if exceeds else "applicable_within"
    reason = (
        f"Concentration {_format_percent(percent)} {MARKET_OPERATOR_TEXT} "
        f"0,1 % by weight in a {branch.replace('_', ' ')} cosmetic product "
        f"triggers the paragraph 1 market-placing restriction "
        f"(from {effective.isoformat()}). This is a regulatory trigger, not a "
        "personal-harm or safety assessment."
        if exceeds else
        f"Concentration {_format_percent(percent)} is below 0,1 % by weight in "
        f"a {branch.replace('_', ' ')} cosmetic product; the market trigger is "
        "not met. This is only the numeric comparison, not a compliance "
        "certificate."
    )
    return Entry70Verdict(
        ingredient=name_or_id, substance_short=short, supported=True,
        state=state, reason=reason,
        threshold="0.1 % by weight", operator=MARKET_OPERATOR, branch=branch,
        effective_date=effective.isoformat(), assessment_date=assessed.isoformat(),
        submitted_percent=submitted_percent, product_type=product_type,
        basis=basis_canon, ready_for_use=ready_for_use,
        source=dict(ENTRY70_SOURCE), provenance=provenance,
    )


def _format_percent(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return f"{value.normalize()}"


def _canonical_unit(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    u = unit.strip().lower()
    if u in ("%", "percent", "pct", "w/w", "w/w %", "% w/w", "by weight"):
        return "%"
    return u
