"""Check a single ingredient against a Registry, producing a CheckResult.

Honesty rules (per AGENTS.md):
  * A ``determination`` of "unknown" — whether from a not-found token or a
    recognised-but-unverified substance — is NEVER an affirmative "OK".
  * Every verified rule carries a ``regulation``, ``source_url``,
    ``source_updated_at`` and an explicit ``resolution``.
  * Ambiguous names/aliases resolve to ``None`` and are reported unresolved.
"""

from __future__ import annotations

from typing import List, Optional

from .models import RANK, CheckResult, Rule, Substance
from .registry import Registry

# Status ranks used to consolidate multiple rules into a headline status.
_STRONGEST = RANK


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
    any_restricted = any(r.status == "restricted" for r in rules)
    if any_restricted:
        return (
            "restriction_identified",
            "A restriction applies but no product concentration/context was "
            "supplied, so no threshold violation has been established. The "
            "restriction is not evaluated; it is not a compliant result.",
        )
    if any(r.status == "banned" for r in rules):
        return (
            "prohibition_identified",
            "The substance is prohibited; no concentration evaluation is "
            "applicable.",
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
    # Explicit source-backed group membership: surface the group's own rules as
    # *linked* evidence (never merged into this identity). Group membership
    # identifies relevant evidence; it does not establish that every group
    # condition applies or is satisfied.
    membership = registry.resolve_memberships(sub)
    linked_rules: List[Rule] = []
    for _group, rule, _m in registry.group_rules_for(sub):
        linked_rules.append(rule)
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
        membership=membership,
        linked_rules=linked_rules,
    )


def check_label(text: str, registry: Registry,
                tokenize_fn=None,
                data_updated_at: Optional[str] = None) -> dict:
    """Check a whole label (list of tokens) and produce a summary dict.

    ``tokenize_fn`` splits a raw label into tokens; it is optional so callers
    may pass their own tokenizer. Import-level default lives in the caller.
    """
    tokens = tokenize_fn(text, registry.phrases) if tokenize_fn else (
        _split_raw(text, registry)
    )
    results = [check_token(t, registry, data_updated_at) for t in tokens]
    # A flag includes the ingredient's OWN rules AND any group *linked* rules
    # (explicit source-backed membership). A member ingredient whose only
    # evidence is a linked group rule is still a conditional/restricted finding,
    # never a clean result. Group membership identifies relevant evidence.
    flagged = [r for r in results
               if r.is_in_dataset and (
                   r.status in ("banned", "restricted", "allowed_with_conditions")
                   or any(lr.status in ("banned", "restricted",
                                        "allowed_with_conditions")
                          for lr in r.linked_rules))]
    violated = [r for r in flagged if r.status == "banned"
                or any(lr.status == "banned" for lr in r.linked_rules)]
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


def _split_raw(text: str, registry: Registry) -> List[str]:
    """Minimal comma/semicolon splitter used when no tokenizer_fn is supplied."""
    if not text:
        return []
    parts = [p.strip().lower() for p in text.replace("\n", ",").split(",")]
    return [p for p in parts if p]
