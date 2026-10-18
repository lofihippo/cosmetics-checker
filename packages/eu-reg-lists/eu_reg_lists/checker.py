"""Check a single ingredient against a Registry, producing a CheckResult.

Honesty rules (project policy):
  * A ``determination`` of "unknown" — whether from a not-found token or a
    recognised-but-unverified substance — is NEVER an affirmative "OK".
  * Every verified rule carries a ``regulation``, ``source_url``,
    ``source_updated_at`` and an explicit ``resolution``.
  * Ambiguous names/aliases resolve to ``None`` and are reported unresolved.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .models import RANK, CheckResult, Rule, Substance
from .registry import Registry
from .tokenize import LabelToken, tokenize_candidates

# Status ranks used to consolidate multiple rules into a headline status.
_STRONGEST = RANK

# Bounded, documented scope/qualification phrases that indicate an Annex II
# prohibition is NOT an unconditional ban. These are explicit phrases present in
# the official source names; we never generalise beyond them. This is the SINGLE
# source of truth for the scoped-prohibition classification shared by the label
# path (``checker``), the formula evaluator (``eu_ingredient_linter.formula``)
# and the reports. It is a bounded syntactic check -- it does NOT interpret
# arbitrary legal text.
ANNEX2_SCOPE_PHRASES = (
    "when used as a substance",
    "when used",
    "with the exception of",
    "except",
    "for use only",
    "in hair dye products",
    "in nail products",
    "in shampoo",
    "other than",
)


def _rule_source_name(rule: Any) -> str:
    """Return the source-row ``Chemical name / INN`` for a rule.

    Works on :class:`Rule` objects (``.raw``) and on already-serialised rule
    dicts (``["raw"]``), so the same scope detection is used by the checker,
    the label/batch report builders and the reporters without a second engine.
    """
    if hasattr(rule, "raw"):
        raw = (rule.raw or {})
    else:
        raw = ((rule or {}).get("raw") or {}) if isinstance(rule, dict) else {}
    src = ((raw.get("source_row") or {}).get("Chemical name / INN") or "")
    return str(src).strip()


def banned_rule_is_unambiguous(rule: Any) -> bool:
    """Return True only for a ``banned`` rule carrying NO scope/exception marker.

    A banned rule whose source name contains a documented scope/exception phrase
    (``... when used as a substance in hair dye products``, ``... with the
    exception of entry N in Annex III``, etc.) is a SCOPED prohibition whose
    applicability is unresolved; it must not be presented as an unconditional
    prohibition. A banned rule with no/absent source name is treated as
    unconditional (the conservative default, consistent with the formula path).
    """
    status = getattr(rule, "status", None)
    if status is None and isinstance(rule, dict):
        status = rule.get("status")
    if status != "banned":
        return False
    src = _rule_source_name(rule)
    if not src:
        return True
    low = src.lower()
    return not any(phrase in low for phrase in ANNEX2_SCOPE_PHRASES)


def annex2_prohibition_finding(rules: Any) -> "tuple[str, Optional[str]]":
    """Return ``(prohibition_finding, reason)`` for the retained Annex II rules.

    ``unambiguous`` means an unconditional (definitive) prohibition; otherwise
    ``scope_unresolved`` records the source qualifier. Mirrors (and centralises)
    the formula evaluator's scope/exception classification so the label path and
    the formula path agree. ``rules`` is any iterable of :class:`Rule` or rule
    dicts.
    """
    qualifiers: List[str] = []
    for rule in rules:
        if banned_rule_is_unambiguous(rule):
            continue
        src = _rule_source_name(rule)
        if not src:
            # No scope evidence on a banned rule that is not unconditional
            # cannot occur given the phrase check; but never treat ambiguity
            # as a definitive ban.
            continue
        qualifiers.append(f"source name: {src}")
    if qualifiers:
        return (
            "scope_unresolved",
            "Annex II prohibition is scoped/conditional in the source "
            "name; applicability to this product is unresolved.",
        )
    return "unambiguous", None


def _evaluate_context(sub: Substance, rules: List[Rule]) -> "tuple[Optional[str], Optional[str]]":
    """Compute a conservative evaluation state for a resolved substance.

    This distinguishes *identifying* a rule from *evaluating* whether a product
    violates it. In the label/batch path no product concentration or context is
    supplied, so:

      * Banned (Annex II) / positive-list rules: the restriction is identified;
        no numeric evaluation is possible.
      * Restricted (Annex III) rules: the restriction is identified explicitly
        as *restriction_identified* and marked unevaluated — never as a claim
        that a concentration limit was exceeded or that the product complies.
    """
    if not rules:
        return None, None
    # ``listed`` (SVHC Candidate List listing information) is evidence requiring
    # contextual review, NOT a numeric concentration restriction and NOT a
    # prohibition. It is surfaced as a listing finding but never evaluated as a
    # threshold violation.
    any_listed = any(r.status == "listed" for r in rules)
    any_restricted = any(r.status == "restricted" for r in rules)
    if any_restricted:
        return (
            "restriction_identified",
            "A restriction applies but no product concentration/context was "
            "supplied, so no threshold violation has been established. The "
            "restriction is not evaluated; it is not a compliant result.",
        )
    if any(r.status == "banned" for r in rules):
        # A banned rule that carries a source scope/exception qualifier (e.g.
        # ``... when used as a substance in hair dye products``, or an exception
        # referencing an Annex III entry) is NOT an unconditional prohibition.
        # Its applicability is unresolved; it must not be presented as an
        # unconditional-sounding "prohibition identified".
        if not all(banned_rule_is_unambiguous(r) for r in rules if r.status == "banned"):
            return (
                "scope_unresolved",
                "Annex II prohibition is scoped/conditional in the source "
                "name; applicability to this product is unresolved. The "
                "prohibition entry is preserved as evidence but the exception/"
                "scope has not been evaluated.",
            )
        return (
            "prohibition_identified",
            "The substance is prohibited; no concentration evaluation is "
            "applicable.",
        )
    if any_listed:
        return (
            "listing_identified",
            "This substance is on a listing (e.g. the REACH SVHC Candidate "
            "List) as source-specific information. Membership is not itself a "
            "cosmetic prohibition and is not a supported concentration "
            "violation; contextual regulatory review is required.",
        )
    return (
        "unrestricted_identified",
        "A conditional/positive-list rule applies; no violation was evaluated.",
    )


def check_token(token: str, registry: Registry,
                data_updated_at: Optional[str] = None) -> CheckResult:
    """Check a single ingredient token, returning ALL applicable rules.

    ``data_updated_at`` defaults to ``registry.data_updated_at`` so callers
    that omit it still get the snapshot timestamp (must not be None).
    ``is_in_dataset`` is True when the token resolves to a substance identity
    (even if that substance's determination is "unknown"). An ambiguous name or
    alias resolves to None and is treated as unresolved (not-in-dataset).
    """
    if data_updated_at is None:
        data_updated_at = registry.data_updated_at
    sub = registry.lookup(token)
    if sub is None:
        return CheckResult(
            ingredient=token, is_in_dataset=False, determination="unknown",
            data_updated_at=data_updated_at,
        )

    strongest = sub.strongest_rule
    determination = sub.determination if sub.has_verified_rule else "unknown"
    rules = sub.rules
    evaluation, evaluation_reason = _evaluate_context(sub, rules)
    # Classify the retained Annex II prohibition (unambiguous vs scoped/
    # exception-unresolved) so the label path carries the same distinction as
    # the formula path instead of surfacing an unconditional-sounding ban.
    annex2_rules = [r for r in rules if (r.annex or "").lower() == "annex ii"]
    prohibition_finding = (
        annex2_prohibition_finding(annex2_rules)[0] if annex2_rules else None
    )
    # Explicit source-backed group membership: surface the group's own rules as
    # *linked* evidence (never merged into this identity). Group membership
    # identifies relevant evidence; it does not establish that every group
    # condition applies or is satisfied.
    membership = registry.resolve_memberships(sub)
    linked_rules: List[Rule] = []
    for _group, rule, _m in registry.group_rules_for(sub):
        linked_rules.append(rule)
    # REACH Annex XVII entry 70: surface the source-bound finding for a D4/D5/D6
    # identity. A label-only check has no concentration/product-type/assessment
    # date, so the entry-70 verdict is identified-but-unevaluated (missing
    # context), never a category violation. It is carried as supplementary
    # evidence and never disturbs the existing CosIng determination.
    from .annex17_entry70 import evaluate_entry70, identify_substance
    entry70 = None
    # Recognise a D4/D5/D6 identity by any of its verified identifiers (name
    # alias in the snapshot, CAS or EC), so a substance whose snapshot name
    # differs (e.g. "Cyclopentasiloxane" for D5, "Octamethylcyclotetrasiloxane;
    # D4") is still correctly identified as an entry-70 substance. Feed the
    # evaluator the first identifier it recognises (name, then CAS then EC).
    _id_for_entry70 = next(
        (x for x in (token, sub.name, sub.cas, sub.ec) if x is not None
         and identify_substance(x) is not None),
        None,
    )
    if _id_for_entry70 is not None:
        _v = evaluate_entry70(
            _id_for_entry70,
            percent=None, unit=None, product_type=None, assessment_date=None,
            verified_sources=getattr(registry, "verified_sources", None),
            snapshot_data_updated_at=data_updated_at,
        )
        entry70 = _v.to_dict()
    return CheckResult(
        ingredient=token,
        is_in_dataset=True,
        determination=determination,
        status=strongest.status if strongest else None,
        annex=strongest.annex if strongest else None,
        regulation=strongest.regulation if strongest else None,
        source_url=strongest.source_url if strongest else None,
        data_updated_at=data_updated_at,
        conditions=strongest.conditions if strongest else None,
        rules=rules,
        cas=sub.cas,
        ec=sub.ec,
        e_number=sub.e_number,
        ci=sub.ci,
        resolution=strongest.resolution if strongest else None,
        cased_name=sub.name,
        evaluation=evaluation,
        evaluation_reason=evaluation_reason,
        prohibition_finding=prohibition_finding,
        membership=membership,
        linked_rules=linked_rules,
        entry70=entry70,
    )


def check_label(text: str, registry: Registry,
                tokenize_fn=None,
                data_updated_at: Optional[str] = None) -> dict:
    """Check a whole label (list of tokens) and produce a summary dict.

    The default uses the shared bracket-aware tokenizer. An explicitly supplied
    ``tokenize_fn`` remains supported for compatibility.
    """
    if data_updated_at is None:
        data_updated_at = registry.data_updated_at
    candidates = ([LabelToken(t, group_id=i)
                   for i, t in enumerate(tokenize_fn(text, registry.phrases))]
                  if tokenize_fn else tokenize_candidates(
                      text, registry.phrases, lambda t: registry.lookup(t) is not None))
    tokens = [t.text for t in candidates]
    results: List[CheckResult] = []
    # Only collapse supplementary aliases within one label occurrence, using
    # the exact resolved registry object (never CAS sharing or fuzzy names).
    seen: dict[tuple[int, int], CheckResult] = {}
    for candidate in candidates:
        substance = registry.lookup(candidate.text)
        identity = (candidate.group_id, id(substance))
        if substance is not None and candidate.derived_from is not None and identity in seen:
            result = seen[identity]
            if candidate.text != result.ingredient and candidate.text not in result.matched_aliases:
                result.matched_aliases.append(candidate.text)
            continue
        result = check_token(candidate.text, registry, data_updated_at)
        result.derived_from = candidate.derived_from
        results.append(result)
        if substance is not None:
            seen[identity] = result
    # A flag includes the ingredient's OWN rules AND any group *linked* rules
    # (explicit source-backed membership). A member ingredient whose only
    # evidence is a linked group rule is still a conditional/restricted finding,
    # never a clean result. Group membership identifies relevant evidence.
    # A retained, source-bound REACH Annex XVII entry 70 (D4/D5/D6) finding is
    # ALSO a restriction finding (identified-but-unevaluated on a label-only
    # check) and always counts toward ``flagged``, so a D5/D6 label is never
    # shown as an unverified "no verified rule" identity. It is NEVER counted in
    # ``banned`` (it is not a demonstrated prohibition).
    def _restriction_finding(r: CheckResult) -> bool:
        if not r.is_in_dataset:
            return False
        if r.status in ("banned", "restricted", "listed",
                        "allowed_with_conditions"):
            return True
        if any(lr.status in ("banned", "restricted", "listed",
                             "allowed_with_conditions")
               for lr in r.linked_rules):
            return True
        e70 = r.entry70 or {}
        return bool(e70) and e70.get("state") not in (
            "not_entry70_substance", "source_not_active")
    flagged = [r for r in results if _restriction_finding(r)]
    violated = [r for r in flagged
                if r.prohibition_finding == "unambiguous"
                or any(lr.status == "banned"
                       and banned_rule_is_unambiguous(lr)
                       for lr in r.linked_rules)]
    n_not_in_dataset = sum(1 for r in results if not r.is_in_dataset)
    n_unverified_identity = sum(
        1 for r in results if r.is_in_dataset and r.determination == "unknown")
    n_unverified = n_not_in_dataset + n_unverified_identity
    no_ingredients = len(results) == 0
    clean = not flagged and n_unverified == 0 and not no_ingredients
    verdict = "FLAGGED" if flagged else ("NO-INGREDIENTS" if no_ingredients
                                         else "NOT-FULLY-VERIFIED")
    if clean:
        verdict = "CLEAN"
    return {
        "results": results,
        "tokens": tokens,
        "n_candidates": len(tokens),
        "n_ingredients": len(results),
        "n_flagged": len(flagged),
        "n_banned": len(violated),
        "n_not_in_dataset": n_not_in_dataset,
        "n_unverified_identity": n_unverified_identity,
        "n_unverified": n_unverified,
        "clean": clean,
        "verdict": verdict,
        "no_ingredients": no_ingredients,
        "data_updated_at": data_updated_at,
        "registry_size": len(registry),
    }
