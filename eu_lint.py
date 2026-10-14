#!/usr/bin/env python3
"""eu_lint.py -- Phase 0 prototype EU ingredient linter.

Single-file prototype that takes a raw ingredient string (or a file) and
checks each token against a small curated EU-restriction dataset.

Scope & honesty:
  * A "clean" result means NO EU ban/restriction/conditional-rule was
    triggered AND every ingredient was resolved to a verified determination.
    It is NOT a safety certificate and NOT legal advice.
  * "Not in dataset" and identity-only substances are NEVER "clean".
    They are reported with explicit NULL / "unknown" semantics.
  * A substance is a stable identity that may carry one or more *rules*
    (e.g. a food rule AND a cosmetic rule). All rules are retained and
    reported; we never drop one to pick whichever row appears last.
  * Conditional rules (allowed_with_conditions) prevent a clean result and
    produce the flag exit code, because an Annex V/IV/VI condition must be
    evaluated against the actual concentration/use.
  * This uses a *curated* dataset (data/eu_flags.json); it is not the
    authoritative corpus. Phase 1 (eu-reg-lists) replaces it with official
    export files.

Usage:
    python eu_lint.py "water, red 40, titanium dioxide, bht" --market eu --format json
    python eu_lint.py label.txt --market eu --format csv
"""

from __future__ import annotations

import argparse
import csv
import errno
import html
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__version__ = "0.2.0"

DEFAULT_DATASET = Path(__file__).resolve().parent / "data" / "eu_flags.json"
DEFAULT_DISCLAIMER = "Regulatory information, not legal advice."

# Severity ordering used for output and exit codes.
SEVERITY_ORDER = ("info", "warning", "error")

# Status ranks used to consolidate multiple rules (highest wins for display,
# but ALL rules are retained in `rules`).
RANK = {"unknown": 0, "allowed_with_conditions": 1, "restricted": 2, "banned": 3}


# ---------------------------------------------------------------------------
# Tokenizer -- explicit, deterministic, no fuzzy matching.
# ---------------------------------------------------------------------------
_PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_PAREN_RE = re.compile(r"\s*\(.*?\)")
_SLASH_RE = re.compile(r"\s*/\s*")
_WS_RE = re.compile(r"[ \t]+")  # collapse only spaces/tabs; keep newlines
# Label headers / seams that must not become ingredient tokens.
_HEADER_RE = re.compile(
    r"(?:active\s+|inactive\s+)?ingredients?\s*[:.]\s*"
    r"|\b(?:nutrition facts|more information|directions|storage)\b\s*[:.]\s*"
    r"|\benriched with vitamins and minerals\b",
    re.IGNORECASE,
)
# HTML "&amp;" and a bare "&" act as an ingredient separator for *unprotected*
# text. Known names containing "&" are protected by the phrase map first.
_AMP_RE = re.compile(r"&\s*(?:amp|#0*38)?;?\s*", re.IGNORECASE)

# Characters that, when inside a *known* ingredient phrase, would be broken by
# naive comma-/"&"-splitting. Such phrases are protected before splitting.
_NEEDS_PROTECT = (",", "&")

# Placeholder sentinel; control char is invisible to normalisation/splitting.
_PLACEHOLDER = "\x00"


def _protect_known_phrases(
    text: str, phrases: Sequence[str]
) -> "tuple[str, Dict[str, str]]":
    """Replace known ingredient phrases that contain `,`/`&` with placeholders.

    A left-to-right, longest-match scan (phrases pre-sorted longest-first)
    protects e.g. "fd&c red 40" and "1,4-dioxane" from being split on the
    comma / ampersand. Callers must apply whitespace collapse + HTML-entity
    decode BEFORE this so variations like "FD&C  Red 40" and "FD&amp;C Red 40"
    already match. Returns (protected_text, placeholder_to_phrase).
    """
    low = text.lower()
    n = len(text)
    out: List[str] = []
    mapping: Dict[str, str] = {}
    i = 0
    while i < n:
        matched = None
        for ph in phrases:
            if low.startswith(ph, i):
                matched = ph
                break
        if matched:
            token = f"{_PLACEHOLDER}{len(mapping)}{_PLACEHOLDER}"
            mapping[token] = matched
            out.append(token)
            i += len(matched)
        else:
            out.append(text[i])
            i += 1
    return "".join(out), mapping


def _restore_placeholders(text: str, mapping: Dict[str, str]) -> List[str]:
    """Restore any placeholders within a single token (kept whole)."""
    if not mapping:
        return [text] if text else []
    hits = re.findall(rf"{_PLACEHOLDER}\d+{_PLACEHOLDER}", text)
    if not hits:
        return [text] if text else []
    restored = text
    for hit in hits:
        restored = restored.replace(hit, mapping[hit])
    return [restored] if restored else []


# Pre-normalisation: decode HTML entities and collapse runs of spaces/tabs so
# that phrase matching sees canonical text. Kept separate from the "&"-to-comma
# conversion (which must only run on UNSHIELDED ampersands).
def _pre_normalize(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = text.lower()
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_text(text: str) -> str:
    """Normalise a raw label string (called on placeholder-protected text).

    Drops percentage specs and converts remaining "&"-separators to commas.
    HTML entity decoding, whitespace collapse and lowercasing are done by
    `_pre_normalize` before phrase protection (in `tokenize`).
    """
    text = text.replace("&nbsp;", " ")
    text = _AMP_RE.sub(",", text)
    text = _PERCENT_RE.sub(" ", text)
    text = text.lower()
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _strip_header(line: str) -> str:
    return _HEADER_RE.sub(" ", line)


def tokenize(text: str, phrases: Optional[Sequence[str]] = None) -> List[str]:
    """Split a normalised label into clean ingredient tokens.

    Order matters for punctuation-safe names:
      1. Decode HTML entities + collapse whitespace + lowercase (so
         "FD&amp;C Red 40" and "FD&C  Red 40" both become "fd&c red 40").
      2. Protect recognised names containing `,`/`&` from being split.
      3. Convert remaining "&" to commas, strip headers/parentheticals, split
         on commas/semicolons/newlines.
    Exact alias matching only; never guesses.
    """
    if not text:
        return []
    pre = _pre_normalize(text)
    protect = [p for p in (phrases or []) if any(c in p for c in _NEEDS_PROTECT)]
    protect.sort(key=len, reverse=True)
    protected, mapping = _protect_known_phrases(pre, protect)

    norm = normalize_text(protected)
    tokens: List[str] = []
    for line in re.split(r"\n", norm):
        line = _strip_header(line)
        for part in re.split(r"[,;]", line):
            part = _PAREN_RE.sub("", part)
            tok = part.strip(" .")
            for sub in _SLASH_RE.split(tok):
                sub = sub.strip(" .,")
                if sub:
                    tokens.extend(_restore_placeholders(sub, mapping))
    return tokens


# ---------------------------------------------------------------------------
# Dataset loading.
# ---------------------------------------------------------------------------
class Substance:
    """A stable substance identity with zero or more regulatory rules."""

    def __init__(self, raw: Dict[str, Any]) -> None:
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.aliases = raw.get("aliases") or []
        self.cas = raw.get("cas")
        self.ec = raw.get("ec")
        self.e_number = raw.get("e_number")
        self.ci = raw.get("ci")
        self.determination = raw.get("determination", "unknown")
        self.rules = raw.get("rules") or []

    @property
    def has_verified_rule(self) -> bool:
        return self.determination != "unknown" and bool(self.rules)

    def keys(self) -> List[str]:
        out = [self.name.lower()] if self.name else []
        out.extend(str(a).lower() for a in self.aliases)
        return [k for k in out if k]


class Dataset:
    """In-memory index mapping names+aliases to Substance identities.

    Because every alias/name resolves to a single Substance (not a rule), and a
    Substance retains ALL of its rules, no record silently overwrites another.
    """

    def __init__(self, path: Path = DEFAULT_DATASET) -> None:
        self.path = Path(path)
        self.meta: Dict[str, Any] = {}
        self.substances: List[Substance] = []
        self._name_lookup: Dict[str, Substance] = {}
        self._alias_lookup: Dict[str, Substance] = {}
        self._phrases: List[str] = []
        self.load()

    def load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.meta = raw.get("meta", {})
        self.substances = [Substance(s) for s in raw.get("substances", [])]
        self._name_lookup = {}
        self._alias_lookup = {}
        self._ambiguous_aliases: set = set()

        # Detect canonical-name collisions BEFORE building lookups. Two distinct
        # substances sharing a canonical name cannot be distinguished by that
        # name, so it must be treated as ambiguous (unresolved), never silently
        # mapped to whichever substance loads first.
        name_owners: Dict[str, set] = {}
        for sub in self.substances:
            if sub.name:
                name_owners.setdefault(sub.name.lower(), set()).add(sub.id)
        ambiguous_names = {k for k, v in name_owners.items() if len(v) > 1}

        owners = {s.id: s for s in self.substances}
        name_of = {s.id: (s.name.lower() if s.name else "") for s in self.substances}

        # Alias -> owners. Reject aliases that map to >1 substance OR collide
        # with a different substance's canonical name.
        alias_owners: Dict[str, set] = {}
        for sub in self.substances:
            self._ambiguous_aliases |= ambiguous_names
            canon = sub.name.lower() if sub.name else ""
            # do not index collapsed canonical names; if unambiguous, map once
            if sub.name and canon not in ambiguous_names:
                self._name_lookup[canon] = sub
            for key in sub.keys():
                if key == canon:
                    continue
                alias_owners.setdefault(key, set()).add(sub.id)

        for key, owner_ids in alias_owners.items():
            if len(owner_ids) > 1:
                self._ambiguous_aliases.add(key)
                continue
            if key in name_of and name_of[key] not in owner_ids:
                self._ambiguous_aliases.add(key)
                continue
            self._alias_lookup[key] = owners[next(iter(owner_ids))]

        # all names + aliases (for protected-phrase tokenization). Ambiguous
        # aliases/names are NOT used for phrase protection.
        seen = set()
        self._phrases = []
        for sub in self.substances:
            for k in sub.keys():
                if k and k not in seen and k not in self._ambiguous_aliases:
                    seen.add(k)
                    self._phrases.append(k)

    def lookup(self, token: str) -> Optional[Substance]:
        key = token.strip().lower()
        if key in self._ambiguous_aliases:
            # Ambiguous alias -> cannot resolve to a single substance. Return
            # None (treated as unresolved/unknown), not a silent last-row pick.
            return None
        return self._name_lookup.get(key) or self._alias_lookup.get(key)

    @property
    def ambiguous_aliases(self) -> set:
        return self._ambiguous_aliases

    @property
    def phrases(self) -> List[str]:
        return self._phrases

    def __len__(self) -> int:
        return len(self.substances)


# ---------------------------------------------------------------------------
# Check logic.
# ---------------------------------------------------------------------------
# Any rule that requires attention. A conditional rule (positive-list with
# conditions) still must be evaluated, so it is a flag and prevents "clean".
FLAG_STATUSES = ("banned", "restricted", "allowed_with_conditions")


def _strongest(rule_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rule_list:
        return None
    return max(rule_list, key=lambda r: RANK.get(r.get("status") or "unknown", 0))


def status_to_severity(status: Optional[str]) -> str:
    if status == "banned":
        return "error"
    if status == "restricted":
        return "warning"
    if status == "allowed_with_conditions":
        return "warning"  # a condition must be evaluated; treat as warning
    return "info"  # unknown / unresolved


def check_token(token: str, dataset: Dataset) -> Dict[str, Any]:
    """Check a single token, returning ALL applicable rules if resolved.

    ``is_in_dataset`` is True whenever the token resolves to a substance
    identity. ``determination`` is "unknown" for identity-only substances
    (recognised name, no verified EU rule) and for not-found tokens the whole
    result is NULL. "unknown" is NEVER treated as clean.
    """
    sub = dataset.lookup(token)
    if sub is None:
        return {
            "ingredient": token,
            "is_in_dataset": False,
            "determination": "unknown",
            "status": None,
            "annex": None,
            "regulation": None,
            "source_url": None,
            "data_updated_at": dataset.meta.get("data_updated_at"),
            "conditions": None,
            "notes": None,
            "rules": [],
            "cas": None,
            "ec": None,
            "e_number": None,
            "ci": None,
            "cased_name": None,
        }

    strongest = _strongest(sub.rules)
    determination = sub.determination if sub.has_verified_rule else "unknown"
    status = strongest.get("status") if strongest else None
    return {
        "ingredient": token,
        "is_in_dataset": True,
        "determination": determination,
        "status": status,
        "annex": strongest.get("annex") if strongest else None,
        "regulation": strongest.get("regulation") if strongest else None,
        "source_url": strongest.get("source_url") if strongest else None,
        # The artifact build date is surfaced here but is NOT claimed to be an
        # upstream publication date (see source_updated_at per rule).
        "data_updated_at": dataset.meta.get("data_updated_at"),
        "conditions": strongest.get("conditions") if strongest else None,
        "notes": strongest.get("conditions") if strongest else None,
        "rules": sub.rules,
        "cas": sub.cas,
        "ec": sub.ec,
        "e_number": sub.e_number,
        "ci": sub.ci,
        "cased_name": sub.name,
    }


def check_label(text: str, dataset: Dataset) -> Dict[str, Any]:
    """Check a whole label and return a summary result dict."""
    tokens = tokenize(text, dataset.phrases)
    results = [check_token(t, dataset) for t in tokens]

    flagged = [r for r in results if r["is_in_dataset"] and r["status"] in FLAG_STATUSES]
    violated = [r for r in flagged if r["status"] == "banned"]
    restricted = [r for r in flagged if r["status"] == "restricted"]
    conditional = [r for r in flagged if r["status"] == "allowed_with_conditions"]
    # Unverified = not found, OR resolved to an identity-only substance whose
    # EU determination is unknown. Neither may ever be asserted clean.
    n_not_in_dataset = sum(1 for r in results if not r["is_in_dataset"])
    n_unverified_identity = sum(
        1 for r in results if r["is_in_dataset"] and r["determination"] == "unknown"
    )
    n_unverified = n_not_in_dataset + n_unverified_identity

    # Honest semantics: a label is clean ONLY if every ingredient resolved to a
    # verified determination AND no ban/restriction/conditional rule was
    # triggered. A conditional rule (Annex V/IV/VI positive-list) still needs
    # the concentration/use evaluated, so it prevents a clean result.
    # Empty input (no tokens produced) must NEVER be clean: there is nothing
    # evaluated, so it is reported as UNVERIFIED / invalid, not clean.
    no_ingredients = len(results) == 0
    clean = not flagged and n_unverified == 0 and not no_ingredients
    has_error = bool(violated)

    if flagged:
        verdict = "FLAGGED"
    elif no_ingredients:
        verdict = "NO-INGREDIENTS"
    else:
        verdict = "NOT-FULLY-VERIFIED"

    return {
        "input": text,
        "tokens": tokens,
        "results": results,
        "n_ingredients": len(results),
        "n_flagged": len(flagged),
        "n_banned": len(violated),
        "n_restricted": len(restricted),
        "n_conditional": len(conditional),
        "n_not_in_dataset": n_not_in_dataset,
        "n_unverified_identity": n_unverified_identity,
        "n_unverified": n_unverified,
        "clean": clean,
        "has_error": has_error,
        "no_ingredients": no_ingredients,
        "verdict": verdict,
        "data_updated_at": dataset.meta.get("data_updated_at"),
        "dataset_count": len(dataset),
        "disclaimer": DEFAULT_DISCLAIMER,
        "sources": dataset.meta.get("sources"),
    }


# ---------------------------------------------------------------------------
# Reporters.
# ---------------------------------------------------------------------------
def fmt_status(status: Optional[str]) -> str:
    if status == "banned":
        return "PROHIBITED"
    if status == "restricted":
        return "RESTRICTED"
    if status == "allowed_with_conditions":
        return "CONDITIONAL"
    if status == "unknown":
        return "UNVERIFIED"
    return "NOT-IN-DATASET"


def status_label(r: Dict[str, Any]) -> str:
    """Human-friendly status for a result row.

    Handles the NULL/unverified case (recognised substance with no status)
    so UI code never crashes calling .replace() on None.
    """
    if not r.get("is_in_dataset"):
        return "Not in dataset"
    if r.get("status"):
        return str(r["status"]).replace("_", " ").title()
    return "Unverified"


def render_text(result: Dict[str, Any]) -> str:
    rows = []
    for r in result["results"]:
        status = fmt_status(r["status"]) if r["is_in_dataset"] else "NOT-IN-DATASET"
        annex = r.get("annex") or "-"
        reg = r.get("regulation") or "-"
        src = r.get("source_url") or "-"
        notes = r.get("conditions") or ""
        rows.append(
            f"  {r['ingredient']:<26} {status:<17} {annex:<9} {reg:<40}"
            f" {src}\n      {notes}"
        )
    header = (
        "  INGREDIENT                 STATUS            ANNEX     "
        "REGULATION                          SOURCE URL"
    )
    body = "\n".join(rows)
    return (
        f"EU ingredient check\n"
        f"  dataset substances : {result['dataset_count']}\n"
        f"  ingredients        : {result['n_ingredients']}\n"
        f"  flagged            : {result['n_flagged']}\n"
        f"  not-in-dataset     : {result['n_not_in_dataset']}\n"
        f"  unverified         : {result['n_unverified']}\n"
        f"  data_updated_at    : {result['data_updated_at']}\n\n"
        + header + "\n" + body + "\n\n"
        f"Result: {result['verdict']}\n"
        f"Note: a clean result means every ingredient resolved to a verified "
        f"determination and no EU ban/restriction/condition was triggered. "
        f"{DEFAULT_DISCLAIMER}"
    )


def render_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False)


def render_csv(result: Dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "ingredient",
            "status",
            "annex",
            "regulation",
            "source_url",
            "is_in_dataset",
            "determination",
            "conditions",
            "data_updated_at",
            "disclaimer",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    for r in result["results"]:
        writer.writerow(
            {
                "ingredient": r["ingredient"],
                "status": r.get("status") or ("UNKNOWN" if r["is_in_dataset"] else "NOT_IN_DATASET"),
                "annex": r.get("annex") or "",
                "regulation": r.get("regulation") or "",
                "source_url": r.get("source_url") or "",
                "is_in_dataset": r["is_in_dataset"],
                "determination": r.get("determination") or "unknown",
                "conditions": r.get("conditions") or "",
                "data_updated_at": r.get("data_updated_at") or "",
                "disclaimer": result["disclaimer"],
            }
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def _read_input(value: str) -> str:
    p = Path(value)
    try:
        is_file = p.is_file()
    except OSError as exc:
        # On Python 3.12, Path(value).is_file() raises ENAMETOOLONG when `value`
        # is a long pasted ingredient label. That is a valid raw string, not a
        # file reference (no file can exist at a path that exceeds the OS path
        # limit), so treat it as a raw label rather than a filename.
        if exc.errno == errno.ENAMETOOLONG:
            return value
        raise
    if is_file:
        return p.read_text(encoding="utf-8")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eu_lint",
        description="EU cosmetic ingredient linter (Phase 0 prototype).",
        epilog=DEFAULT_DISCLAIMER,
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Raw ingredient string, or path to a file containing labels.",
    )
    parser.add_argument(
        "--market", default="eu", choices=["eu"],
        help="Market to check against (Phase 0 supports only 'eu').",
    )
    parser.add_argument(
        "--format", dest="fmt", default="table",
        choices=["table", "json", "csv"], help="Output format.",
    )
    parser.add_argument(
        "--dataset", default=str(DEFAULT_DATASET),
        help="Path to a eu_flags.json dataset file.",
    )
    parser.add_argument("--version", action="version", version=f"eu_lint {__version__}")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input:
        print("error: no input given (paste a label or pass a file path)",
              file=sys.stderr)
        return 2

    dataset = Dataset(Path(args.dataset))
    text = _read_input(args.input)
    result = check_label(text, dataset)

    if args.fmt == "json":
        print(render_json(result))
    elif args.fmt == "csv":
        print(render_csv(result), end="")
    else:
        print(render_text(result))

    # Exit 1 when a definitive EU ban/restriction OR a conditional rule was
    # triggered (a flag). "Not in dataset" / unverified identities produce
    # NO flag (they are not a determination) and return 0, but are never clean.
    if result["n_flagged"]:
        return 1
    # Empty / whitespace-only input yields NO evaluated ingredient -> invalid.
    if result["no_ingredients"]:
        print("error: no ingredients parsed from input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
