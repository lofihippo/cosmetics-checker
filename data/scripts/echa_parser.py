"""Parse official ECHA export files into verified rules.

ECHA publishes three downloadable exports with well-defined schemas:

  * **SVHC Candidate List** — CSV/XLSX: columns include `Substance name` (or
    `Substance Name`), `CAS number` (or `CAS No`), `EC number`, `Date of
    inclusion`, `Reason for inclusion`.
  * **Annex XIV (Authorisation List)** — columns: `Substance Name`, `CAS number`,
    `EC number`, `Annex XIV` / `Sunset date`, `Latest application date`.
  * **Annex XVII (Restrictions)** — columns: `Name of substance`, `CAS number`,
    `EC number`, `Restriction`.

This parser is schema-driven and conservative: it only emits a verified rule
for a row whose annex is recognised and whose name is present. It does not
infer statuses from substring matches. Columns are matched case-insensitively
with flexible aliases. Values are kept verbatim (we do NOT reformat/validate
CAS beyond keeping it, to avoid inventing corrections).

Honesty: we record the exact rows and the source file/hash so each rule is
traceable to an official export.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# Determine the annex/status from the source kind.
#
# Only Annex XIV (authorisation) and Annex XVII (restrictions) carry a
# ``restricted`` determination. The SVHC Candidate List is listing INFORMATION
# under REACH Article 59: it identifies a substance of very high concern for
# eventual authorisation, but membership is NOT itself a cosmetic prohibition
# and NOT a supported concentration/violation limit. It is therefore assigned
# the distinct ``listed`` status, never ``restricted`` or ``banned``. A
# ``listed`` rule is surfaced as evidence requiring contextual review.
ANNEX_BY_KIND = {
    "svhc": ("SVHC", "listed"),
    "annex-xiv": ("Annex XIV", "restricted"),
    "annex-xvii": ("Annex XVII", "restricted"),
}

# Column-name aliases (lowercased, spaces collapsed).
_COL_ALIASES = {
    "name": ["substance name", "substance", "name of substance", "name"],
    "cas": ["cas number", "cas no", "cas", "cas no."],
    "ec": ["ec number", "ec no", "ec", "ec no."],
    "reason": ["reason for inclusion", "reason", "restriction",
               "restrictions", "notes"],
    "date_included": ["date of inclusion", "inclusion date", "sunset date",
                      "date of sunset"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _find_column(header: List[str], key: str) -> Optional[int]:
    for aliases in [_COL_ALIASES[key]]:
        for idx, col in enumerate(header):
            if _norm(col) in (_norm(a) for a in aliases):
                return idx
    # fallback: substring match
    match = None
    for idx, col in enumerate(header):
        cn = _norm(col)
        for a in _COL_ALIASES[key]:
            if a in cn:
                match = idx
                break
    return match


def parse_echa_csv(text: str, kind: str, source_url: str,
                   source_updated_at: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse an ECHA export CSV into verified-rule records.

    ``kind`` must be one of ``svhc``/``annex-xiv``/``annex-xvii``, which selects
    the annex/status for each parsed row.
    """
    if kind not in ANNEX_BY_KIND:
        raise ValueError(f"unknown ECHA kind: {kind!r}")
    annex, status = ANNEX_BY_KIND[kind]

    dialect: Union[str, type[csv.Dialect]]
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = "excel"
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    header = rows[0]
    c = {
        "name": _find_column(header, "name"),
        "cas": _find_column(header, "cas"),
        "ec": _find_column(header, "ec"),
        "reason": _find_column(header, "reason"),
        "date_included": _find_column(header, "date_included"),
    }
    name_i = c["name"]
    if name_i is None:
        return []

    out: List[Dict[str, Any]] = []
    raw_seen: set = set()
    for row in rows[1:]:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        name = (row[name_i] or "").strip()
        if not name:
            continue
        # Deduplicate only IDENTICAL rows (same substance + same restriction text
        # + same date). Two rows for the same substance with DIFFERENT
        # restriction text are distinct rules and must both be retained.
        cond = (row[c["reason"]] or "").strip() if c["reason"] is not None else ""
        date = (row[c["date_included"]] or "").strip() if c["date_included"] is not None else ""
        key = (name.lower(),
               (row[c["cas"]] if c["cas"] is not None else "").strip(),
               (row[c["ec"]] if c["ec"] is not None else "").strip(),
               cond, date)
        if key in raw_seen:
            continue  # exact duplicate row
        raw_seen.add(key)
        rec = {
            "name": name,
            "cas": (row[c["cas"]] or "").strip() if c["cas"] is not None else None,
            "ec": (row[c["ec"]] or "").strip() if c["ec"] is not None else None,
            "annex": annex,
            "status": status,
            "conditions": cond or None,
            "date_included": date or None,
            "resolution": f"echa-{kind}",
            "regulation": ("ECHA SVHC Candidate List" if kind == "svhc"
                           else "Regulation (EC) No 1907/2006 (REACH)"),
            "source_url": source_url,
            "source_updated_at": source_updated_at,
            "source_kind": kind,
        }
        out.append(rec)
    return out


def parse_echa_file(path: Path, kind: str, source_url: str,
                    source_updated_at: Optional[str] = None) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".tsv":
        text = "\n".join(line.replace("\t", ";") for line in text.splitlines())
    return parse_echa_csv(text, kind, source_url, source_updated_at)
