"""In-memory registry that loads substances from SQLite/JSONL and searches them.

Collision semantics (matching the eu_lint prototype):
  * A canonical name or alias must map to exactly one substance. If a name or
    alias would ambiguously map to more than one substance (including a
    canonical name shared by two substances), it is rejected as ambiguous and
    resolves to ``None`` (unresolved) rather than silently picking one.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import Rule, Substance


class Registry:
    """Searchable in-memory index over substances and their rules."""

    def __init__(self, substances: List[Substance], data_updated_at: Optional[str] = None,
                 verified_sources: Optional[Dict[str, Any]] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        self.substances = substances
        self.data_updated_at = data_updated_at
        self.verified_sources = verified_sources or {}
        self.metadata = metadata or {}
        self._name_lookup: Dict[str, Substance] = {}
        self._alias_lookup: Dict[str, Substance] = {}
        self._id_lookup: Dict[str, Substance] = {}
        self._ambiguous: Set[str] = set()
        self._by_id: Dict[str, Substance] = {}
        self._build_lookup()

    # -- construction -----------------------------------------------------
    @classmethod
    def from_sqlite(cls, path: str | Path) -> "Registry":
        return _load_from_sqlite(path)

    @classmethod
    def from_jsonl_metadata(cls, jsonl: str | Path,
                            metadata: Optional[Dict[str, Any]] = None) -> "Registry":
        return _load_from_jsonl(jsonl, metadata)

    def _build_lookup(self) -> None:
        # Canonical name -> set of substance ids (collision if >1).
        self._by_id = {s.id or s.name: s for s in self.substances}
        name_owners: Dict[str, Set[str]] = {}
        for sub in self.substances:
            if sub.name:
                name_owners.setdefault(sub.name.lower(), set()).add(sub.id or sub.name)
        ambiguous_names = {k for k, v in name_owners.items() if len(v) > 1}

        owners = {s.id or s.name: s for s in self.substances}
        alias_owners: Dict[str, Set[str]] = {}

        for sub in self.substances:
            self._ambiguous |= ambiguous_names
            canon = (sub.name or "").lower()
            if sub.name and canon not in ambiguous_names:
                self._name_lookup[canon] = sub
            for key in sub.keys():
                if key == canon:
                    continue
                alias_owners.setdefault(key, set()).add(sub.id or sub.name)

        # Build a map of canonical name -> its owning substance id (to detect
        # an alias colliding with a DIFFERENT substance's canonical name).
        name_owner_of: Dict[str, str] = {}
        for sub in self.substances:
            if sub.name:
                c = sub.name.lower()
                if c not in ambiguous_names:
                    name_owner_of[c] = sub.id or sub.name

        for key, owner_ids in alias_owners.items():
            if len(owner_ids) > 1:
                self._ambiguous.add(key)
                continue
            # An alias may not collide with a different substance's canonical name.
            if key in name_owner_of and name_owner_of[key] not in owner_ids:
                self._ambiguous.add(key)
                continue
            self._alias_lookup[key] = owners[next(iter(owner_ids))]

        # Index exact identifiers (CAS/EC/E-number/CI) so a token that *is* an
        # identifier resolves directly. A shared identifier (e.g. one CAS used by
        # several substances) is ambiguous -> quarantined (never first-pick).
        self._id_lookup: Dict[str, Substance] = {}
        id_owners: Dict[str, Set[str]] = {}
        for sub in self.substances:
            ids = [sub.cas, sub.ec, sub.e_number, sub.ci]
            for i in ids:
                if not i:
                    continue
                id_owners.setdefault(str(i).strip().lower(), set()).add(sub.id or sub.name)
        for key, id_set in id_owners.items():
            if len(id_set) > 1:
                self._ambiguous.add(key)
                continue
            self._id_lookup[key] = owners[next(iter(id_set))]

    # -- query ------------------------------------------------------------
    def lookup(self, token: str) -> Optional[Substance]:
        key = token.strip().lower()
        if key in self._ambiguous:
            return None
        return (self._name_lookup.get(key)
                or self._alias_lookup.get(key)
                or self._id_lookup.get(key))

    def resolve_memberships(self, sub: Substance) -> List[Dict[str, Any]]:
        """Return the explicit group-membership records for a substance.

        Each record is the stored ``member_of`` entry linking this identity to a
        group source entry via the member's exact source-name evidence. Only
        source-declared memberships are returned; ambiguous/unresolved
        membership is recorded separately and never silently dropped.
        """
        return list(getattr(sub, "member_of", None) or [])

    def group_rules_for(self, sub: Substance) -> List[Tuple[Substance, Rule, Dict[str, Any]]]:
        """Return ``(group, rule, membership)`` for every group rule linked to a
        member substance via an explicit source-backed membership record.

        The group's rules are surfaced as *linked* evidence keyed by the group's
        source identity (``member_of.group_id``) AND bound to the **specific
        source rule** the membership comes from (``member_of.source_key`` =
        source_kind + official_reference + row_reference). A membership does NOT
        inherit an unrelated rule stored on the same record. Rules are
        deduplicated by stable source identity. A group record that no longer
        resolves to a live group substance is preserved in ``membership`` but
        yields no rule (reported as unresolved).
        """
        out: List[Tuple[Substance, Rule, Dict[str, Any]]] = []
        seen: Set[Tuple[str, str, str]] = set()
        for m in getattr(sub, "member_of", None) or []:
            gid = m.get("group_id")
            group = self._by_id.get(gid) if gid else None
            if group is None:
                continue
            # Bind to the specific source key recorded by the membership.
            source_key = m.get("source_key")
            if not source_key or not isinstance(source_key, (list, tuple)):
                continue
            skind, sref, srow = (str(source_key[0] or ""), str(source_key[1] or ""),
                                 str(source_key[2] or ""))
            for rule in group.rules:
                raw = rule.raw or {}
                key = (str(raw.get("source_kind") or ""),
                       str(raw.get("official_reference") or raw.get("reference_number") or ""),
                       str(raw.get("row_reference") or ""))
                # Only the rule(s) actually backed by this membership are linked;
                # an unrelated rule stored on the same record is NOT inherited.
                if key != (skind, sref, srow):
                    continue
                if key in seen:
                    continue
                seen.add(key)
                out.append((group, rule, m))
        return out

    def search(self, query: str, limit: int = 25) -> List[Substance]:
        """Search by CAS, EC, E-number, CI, INCI name or alias (case-insensitive).

        Uses exact-id lookup first, then a substring scan across keys. Returns
        substances that match; ambiguous lookup keys are skipped.
        """
        q = query.strip()
        if not q:
            return []
        # exact identifier / name hit
        exact = self.lookup(q)
        if exact is not None:
            return [exact]
        low = q.lower()
        seen: Dict[str, Substance] = {}
        for sub in self.substances:
            ids = [sub.cas, sub.ec, sub.e_number, sub.ci]
            keys = list(sub.keys()) + [str(i) for i in ids if i]
            for k in keys:
                if low in k.lower():
                    seen[sub.id or sub.name] = sub
                    break
        return list(seen.values())[:limit]

    @property
    def ambiguous_keys(self) -> Set[str]:
        return set(self._ambiguous)

    @property
    def phrases(self) -> List[str]:
        """All unambiguous names+aliases, for punctuation-safe tokenization."""
        out: List[str] = []
        seen: Set[str] = set()
        for sub in self.substances:
            for k in sub.keys():
                if k and k not in seen and k not in self._ambiguous:
                    seen.add(k)
                    out.append(k)
        return out

    def __len__(self) -> int:
        return len(self.substances)


# -- loaders --------------------------------------------------------------
def _decode_metadata(value: Any) -> Any:
    """Decode a JSON-encoded metadata value stored in SQLite.

    Values were written via json.dumps, so a string comes back with literal
    quotes ('"2026-...Z"'). We decode to the actual string/dict/list.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _substance_from_dict(d: Dict[str, Any]) -> Substance:
    rules = [Rule(**{k: v for k, v in r.items() if k in Rule.__dataclass_fields__})
             for r in d.get("rules", [])]
    return Substance(
        id=d.get("id"),
        name=d.get("name"),
        aliases=d.get("aliases") or [],
        cas=d.get("cas"),
        ec=d.get("ec"),
        e_number=d.get("e_number"),
        ci=d.get("ci"),
        determination=d.get("determination", "unknown"),
        rules=rules,
        sources=d.get("sources") or [],
        member_of=d.get("member_of") or [],
    )


def _load_from_jsonl(jsonl: str | Path,
                     metadata: Optional[Dict[str, Any]] = None) -> Registry:
    path = Path(jsonl)
    substances = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        substances.append(_substance_from_dict(json.loads(line)))
    meta = metadata or {}
    return Registry(
        substances,
        data_updated_at=meta.get("data_updated_at"),
        verified_sources=meta.get("verified_sources") or meta.get("snapshot") or {},
        metadata=meta,
    )


def _load_from_sqlite(path: str | Path) -> Registry:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    meta = {}
    try:
        rows = con.execute("SELECT k,v FROM metadata").fetchall()
        # Metadata values are stored as JSON-encoded strings; decode each so a
        # timestamp like '"2026-...Z"' becomes '2026-...Z' (no literal quotes).
        meta = {r["k"]: _decode_metadata(r["v"]) for r in rows}
    except sqlite3.Error:
        pass

    substances: List[Substance] = []
    # ``member_of`` column was added in the group-membership milestone. Read it
    # when present (fresh snapshot), else default to [] (legacy snapshot).
    try:
        sub_cols = [c[1] for c in con.execute("PRAGMA table_info(substances)").fetchall()]
    except sqlite3.Error:
        sub_cols = []
    has_member_of = "member_of" in sub_cols
    sub_sel = ("SELECT id,name,aliases,cas,ec,e_number,ci,determination,sources"
               + (",member_of" if has_member_of else "") + " FROM substances")
    sub_rows = con.execute(sub_sel).fetchall()
    for r in sub_rows:
        aliases = json.loads(r["aliases"]) if r["aliases"] else []
        substances.append(Substance(
            id=r["id"], name=r["name"], aliases=aliases, cas=r["cas"],
            ec=r["ec"], e_number=r["e_number"], ci=r["ci"],
            determination=r["determination"], sources=json.loads(r["sources"]) if r["sources"] else [],
            member_of=json.loads(r["member_of"]) if has_member_of and r["member_of"] else [],
        ))
    rule_rows = con.execute(
        "SELECT substance_id,status,annex,regulation,source_url,source_updated_at,"
        "conditions,resolution,effective_date,raw FROM rules"
    ).fetchall()
    by_id = {s.id: s for s in substances}
    for r in rule_rows:
        sub = by_id.get(r["substance_id"])
        if sub is None:
            continue
        sub.rules.append(Rule(
            status=r["status"], annex=r["annex"], regulation=r["regulation"],
            source_url=r["source_url"], source_updated_at=r["source_updated_at"],
            conditions=r["conditions"], resolution=r["resolution"],
            effective_date=r["effective_date"],
            raw=json.loads(r["raw"]) if r["raw"] else {},
        ))
    con.close()
    return Registry(
        substances,
        data_updated_at=meta.get("data_updated_at"),
        verified_sources=meta.get("verified_sources") or meta.get("snapshot") or {},
        metadata=meta,
    )
