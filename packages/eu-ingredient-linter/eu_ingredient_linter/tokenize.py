"""Punctuation-aware label tokenizer for eu-ingredient-linter.

This ports the deterministic, punctuation-safe normalisation from the Phase-0
prototype (``eu_lint.tokenize``) so names containing ``,``/``&`` (e.g.
``1,4-dioxane``, ``FD&C Red 40``) are kept whole instead of split on the comma.
Exact alias matching only; never guesses.

Order matters:
  1. Decode HTML entities + collapse whitespace + lowercase (so ``FD&amp;C Red 40``
     and ``FD&C  Red 40`` both become ``fd&c red 40``).
  2. Protect recognised names containing ``,``/``&`` from being split.
  3. Convert remaining ``&`` to commas, strip headers/parentheticals, split on
     commas/semicolons/newlines.
"""

from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Sequence

_PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_PAREN_RE = re.compile(r"\s*\(.*?\)")
_SLASH_RE = re.compile(r"\s*/\s*")
_WS_RE = re.compile(r"[ \t]+")  # collapse only spaces/tabs; keep newlines
_HEADER_RE = re.compile(
    r"(?:active\s+|inactive\s+)?ingredients?\s*[:.]\s*"
    r"|\b(?:nutrition facts|more information|directions|storage)\b\s*[:.]\s*"
    r"|\benriched with vitamins and minerals\b",
    re.IGNORECASE,
)
_AMP_RE = re.compile(r"&\s*(?:amp|#0*38)?;?\s*", re.IGNORECASE)

# Characters that, when inside a *known* ingredient phrase, would be broken by
# naive comma-/"&"-splitting. Such phrases are protected before splitting.
_NEEDS_PROTECT = (",", "&")
_PLACEHOLDER = "\x00"


def _protect_known_phrases(text: str, phrases: Sequence[str]) -> "tuple[str, Dict[str, str]]":
    """Replace known ingredient phrases containing `,`/`&` with placeholders."""
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
    if not mapping:
        return [text] if text else []
    hits = re.findall(rf"{_PLACEHOLDER}\d+{_PLACEHOLDER}", text)
    if not hits:
        return [text] if text else []
    restored = text
    for hit in hits:
        restored = restored.replace(hit, mapping[hit])
    return [restored] if restored else []


def _pre_normalize(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = text.lower()
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _normalize_text(text: str) -> str:
    text = text.replace("&nbsp;", " ")
    text = _AMP_RE.sub(",", text)
    text = _PERCENT_RE.sub(" ", text)
    text = text.lower()
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _strip_header(line: str) -> str:
    return _HEADER_RE.sub(" ", line)


def tokenize(text: str, phrases: Optional[Sequence[str]] = None) -> List[str]:
    """Split a normalised label into clean ingredient tokens (no fuzzy match)."""
    if not text:
        return []
    pre = _pre_normalize(text)
    protect = [p for p in (phrases or []) if any(c in p for c in _NEEDS_PROTECT)]
    protect.sort(key=len, reverse=True)
    protected, mapping = _protect_known_phrases(pre, protect)

    norm = _normalize_text(protected)
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
