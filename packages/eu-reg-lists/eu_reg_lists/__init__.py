"""eu-reg-lists: normalized EU regulatory substance data + lookup API.

Public API
----------
 * ``Registry`` — load from SQLite/JSONL, search by CAS/EC/E-number/CI/INCI/alias.
 * ``load(sqlite_or_jsonl)`` — convenient loader that auto-detects format.
 * ``check_token`` / ``check_label`` — produce ``CheckResult`` with honest
   unknown semantics and explicit resolution methods.
 * ``Substance``, ``Rule``, ``CheckResult`` — data model dataclasses.

Honesty: a ``determination`` of "unknown" (not-found or recognised-but-
unverified substance) is NEVER an affirmative "OK". Every verified rule
carries a ``regulation``, ``source_url``, ``source_updated_at`` and
``resolution``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .checker import check_label, check_token
from .models import CheckResult, Rule, Substance
from .registry import Registry

__version__ = "0.1.0"
__all__ = [
    "Registry", "Substance", "Rule", "CheckResult",
    "check_token", "check_label", "load",
]


def load(source: Union[str, Path], metadata: Optional[dict] = None) -> Registry:
    """Load a Registry from a SQLite db or a JSONL file (auto-detected)."""
    path = Path(source)
    if path.suffix.lower() == ".db" or path.suffix.lower() == ".sqlite" \
            or path.suffix.lower() == ".sqlite3":
        return Registry.from_sqlite(path)
    if path.suffix.lower() == ".jsonl":
        return Registry.from_jsonl_metadata(path, metadata)
    raise ValueError(
        f"unsupported source type: {path.suffix!r} (use .db/.sqlite or .jsonl)"
    )
