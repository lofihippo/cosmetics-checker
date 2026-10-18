"""Shared deterministic label tokenizer; exact registry phrases, never fuzzy.

Balanced brackets delimit supplementary candidates. Unmatched brackets are
literal characters. Iterative traversal also supports deeply nested labels.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

_PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_WS_RE = re.compile(r"[ \t]+")
_HEADER_RE = re.compile(
    r"(?:active\s+|inactive\s+)?ingredients?\s*[:.]\s*"
    r"|\b(?:nutrition facts|more information|directions|storage)\b\s*[:.]\s*"
    r"|\benriched with vitamins and minerals\b", re.IGNORECASE,
)
_AMP_RE = re.compile(r"&\s*(?:amp|#0*38)?;?\s*", re.IGNORECASE)
_MAY_CONTAIN_RE = re.compile(
    r"\b(?:may\s+contain|peut\s+contenir)\b\s*:?"
    r"|\[\s*\+\s*/\s*-\s*\]\s*:?"
    r"|\(\s*\+\s*/\s*-\s*\)\s*:?|\+\s*/\s*-\s*:?",
    re.IGNORECASE,
)
_CONTAINS_RE = re.compile(r"^(?:(?:contains|contient)\b|incl\.)\s*:?\s*", re.I)
_NANO_RE = re.compile(r"[\[(]\s*nano\s*[\])]")
_NEEDS_PROTECT = (",", "&", "/", ";", ".", "%", "\n")
_PLACEHOLDER = "\x00"


@dataclass(frozen=True)
class LabelToken:
    text: str
    derived_from: Optional[str] = None
    group_id: int = 0


def _protect_known_phrases(text: str, phrases: Sequence[str]) -> tuple[str, Dict[str, str]]:
    """Longest exact phrase protection via a trie, independent of phrase count.

    Shield separators, periods and percent signs before label cleanup, leaving
    brackets visible for candidate extraction.
    Word boundaries prevent protecting a known phrase inside a different name.
    """
    trie: Dict[str, Any] = {}
    for phrase in phrases:
        node = trie
        for char in phrase:
            node = node.setdefault(char, {})
        node[""] = phrase
    mapping = {f"{_PLACEHOLDER}{ord(c)}{_PLACEHOLDER}": c for c in _NEEDS_PROTECT}
    escapes = {v: k for k, v in mapping.items()}
    out: List[str] = []
    i = 0
    while i < len(text):
        node = trie
        end = i
        if i == 0 or not text[i - 1].isalnum():
            j = i
            while j < len(text) and text[j] in node:
                node = node[text[j]]
                j += 1
                if "" in node and (j == len(text) or not text[j].isalnum()):
                    end = j
        if end > i:
            out.append("".join(escapes.get(c, c) for c in text[i:end]))
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out), mapping


def _pre_normalize(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text).replace("\xa0", " ").lower()).strip()


def _bracket_pairs(text: str) -> Dict[int, int]:
    stack: List[int] = []
    pairs: Dict[int, int] = {}
    for i, char in enumerate(text):
        if char in "([":
            stack.append(i)
        elif char in ")]":
            if stack and text[stack[-1]] == {")": "(", "]": "["}[char]:
                pairs[stack.pop()] = i
            else:
                # A mismatch invalidates open groups, not the following label.
                stack.clear()
    return pairs


def tokenize_candidates(
    text: str, phrases: Optional[Sequence[str]] = None,
    resolves: Optional[Callable[[str], bool]] = None,
) -> List[LabelToken]:
    """Return primary tokens and exactly resolved supplementary candidates.

    Without a registry callback, supplied phrases are the exact lookup set.
    Unknown supplementary text is omitted; unknown primary text stays unknown.
    A bracket-only label has primary content, so it cannot disappear as an
    ignored translation. Known full bracketed names and nano forms stay intact.
    """
    known = {_pre_normalize(p) for p in (phrases or []) if p}
    is_known = resolves if resolves is not None else known.__contains__
    pre = _pre_normalize(text).replace(_PLACEHOLDER, " ")
    protect = [p for p in known if any(c in p for c in _NEEDS_PROTECT)]
    protected, mapping = _protect_known_phrases(pre, protect)
    protected = _PERCENT_RE.sub(" ", protected)
    protected = _MAY_CONTAIN_RE.sub(",", protected)
    norm = _HEADER_RE.sub(" ", _AMP_RE.sub(",", protected))
    pairs = _bracket_pairs(norm)

    def restore(value: str) -> str:
        # Trim label punctuation while exact-name punctuation is still shielded.
        value = _WS_RE.sub(" ", value).strip(" .,:;")
        for key, char in mapping.items():
            value = value.replace(key, char)
        return value

    def split(start: int, end: int) -> List[tuple[int, int]]:
        spans = []
        i = begin = start
        while i < end:
            if i in pairs:
                i = pairs[i] + 1
                continue
            if norm[i] in ",;\n/":
                spans.append((begin, i))
                begin = i + 1
            i += 1
        spans.append((begin, end))
        return spans

    tokens: List[LabelToken] = []
    pending: List[tuple[int, int, Optional[str], bool, int]] = [
        (a, b, None, False, group_id)
        for group_id, (a, b) in reversed(list(enumerate(split(0, len(norm)))))
    ]
    while pending:
        start, end, parent, in_brackets, group_id = pending.pop()
        # Strip leading qualifiers only within bracket content.
        if in_brackets:
            while start < end and norm[start].isspace():
                start += 1
            match = _CONTAINS_RE.match(norm[start:end])
            if match:
                start += match.end()
        pieces: List[str] = []
        groups: List[tuple[int, int]] = []
        i = begin = start
        while i < end:
            if i in pairs:
                pieces.append(norm[begin:i])
                groups.append((i + 1, pairs[i]))
                i = pairs[i] + 1
                begin = i
            else:
                i += 1
        pieces.append(norm[begin:end])
        full = restore(norm[start:end])
        base = restore(" ".join(pieces))
        primary = full if is_known(full) or _NANO_RE.search(full) else base
        if primary and (parent is None or is_known(primary)):
            tokens.append(LabelToken(primary, parent, group_id))
        # An explicit stack avoids Python recursion limits on hostile labels.
        origin = full if primary else parent
        children = [(a, b, origin, True, group_id) for gstart, gend in groups
                    for a, b in split(gstart, gend)]
        pending.extend(reversed(children))
    return tokens


def tokenize(text: str, phrases: Optional[Sequence[str]] = None) -> List[str]:
    """Backward-compatible string tokens from the shared candidate parser."""
    return [token.text for token in tokenize_candidates(text, phrases)]
