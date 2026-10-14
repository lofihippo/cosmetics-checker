"""Parse official CosIng Annex export files into verified rules.

The real CosIng export format (as downloaded from the site's Annex list page
"CSV" button) is a CSV/TXT file with this structure::

    "File creation date: 08/10/2026"
    "ANNEX II","Last update: 06/10/2026"
    "LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"
    "Substance identification"
    "Reference Number","Chemical name / INN","CAS Number","EC Number",Regulation,...
    1,N-(5-Chlorobenzoxazol-2-yl)acetamide,35783-57-4,-,"(EC) 2009/1223",...

Key facts about the real schema:
  * The annex is NOT a per-row column. It is declared in the metadata preamble
    (line 2: ``"ANNEX II","Last update: ..."``). The annex number determines the
    regime (Annex II = banned, III = restricted, IV/V/VI = positive lists).
  * The header row is the first row whose column-0 value is "Reference Number".
  * Data columns are: Reference Number, Chemical name / INN, CAS Number,
    EC Number, Regulation, Other Directives/Regulations, SCCS opinions,
    Chemical/IUPAC Name, Identified INGREDIENTS or substances e.g., CMR,
    Update Date. The status/prohibition is implied by the annex context.
  * The file may have a ``.txt`` extension (the browser download is
    ``COSING_Annex_II_v2.txt``).

This parser is schema-driven and conservative: it only emits a verified rule
when the annex is declared in the preamble AND at least a name is present. It
does not infer statuses from name substrings. The ``resolution`` encodes the
annex so the provenance is traceable to the source entry.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class AnnexMismatchError(ValueError):
    """Raised when an export's self-declared annex contradicts the configured
    source annex. This is an *expected* source-validation failure (a candidate
    export staged under the wrong annex key), distinct from a programming error;
    the normalize pipeline records it and falls back to the active snapshot."""


# Column names (lowercased, stripped) we recognise in the real CosIng export.
COL_REF = "reference number"
COL_NAME = "chemical name / inn"
# Annex IV uses a slightly different header for the substance name ("Chemical
# name" rather than "Chemical name / INN"); we recognise both.
COL_NAME_ANNEX_IV = "chemical name"
COL_CAS = "cas number"
COL_EC = "ec number"
COL_REGULATION = "regulation"
# Annex IV identifies colorants by a combined colour-index / glossary column.
COL_CI = "colour index number / name of common ingredients glossary"

# Annex III adds restriction/condition columns (present in the real export).
COL_PRODUCT_TYPES = "product type, body parts"
COL_MAX_CONC = "maximum concentration in ready for use preparation"
COL_OTHER = "other"
COL_WARNINGS = "wording of conditions of use and warnings"

# Map an annex token (from the preamble) to a determination.
STATUS_BY_ANNEX = {
    "ii": "banned",
    "iii": "restricted",
    "iv": "allowed_with_conditions",  # positive list (conditional)
    "v": "allowed_with_conditions",
    "vi": "allowed_with_conditions",
}


def _preamble_annex(lines: List[str]) -> Optional[str]:
    """Return the roman annex token declared in the preamble, if any.

    The annex is in a line like ``"ANNEX II","Last update: 06/10/2026"``.
    Returns the low-ercased roman numeral (e.g. ``"ii"``) or None.
    """
    for line in lines[:20]:
        m = re.search(r'"ANNEX\s+([IVX]+)"', line, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return None


def _find_header_idx(lines: List[str]) -> Optional[int]:
    """Locate the data header row (first row whose col-0 is 'Reference Number')."""
    for i, line in enumerate(lines):
        strip = line.strip().strip('"').lstrip()
        if strip.lower().startswith(COL_REF):
            return i
    return None


def _header_columns(fieldnames: List[str]) -> Dict[str, Optional[str]]:
    """Map our recognised column names to the actual CSV column name."""
    low = {c.strip().lstrip('"').lower(): c for c in fieldnames}
    return {
        "reference_number": low.get(COL_REF),
        # Annex III/V/VI use "Chemical name / INN"; Annex IV uses "Chemical name".
        "name": low.get(COL_NAME) or low.get(COL_NAME_ANNEX_IV),
        "ci": low.get(COL_CI),
        "cas": low.get(COL_CAS),
        "ec": low.get(COL_EC),
        "regulation": low.get(COL_REGULATION),
        "product_types": low.get(COL_PRODUCT_TYPES),
        "max_concentration": low.get(COL_MAX_CONC),
        "other": low.get(COL_OTHER),
        "warnings": low.get(COL_WARNINGS),
    }


# Optional condition columns whose raw values we preserve verbatim. Recognition
# is name-based (the header order differs between annexes), so a value is kept
# only when its column is present.
_OPTIONAL_TEXT_COLS = ("product_types", "max_concentration", "other", "warnings")


def _combine_conditions(row: Dict[str, Any], cols: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Build the ordered list of condition fragments for an Annex III rule.

    Conditions are kept as a structured, ordered list so the UI can present the
    product-type context, the concentration limit, other restrictions, and the
    required warnings as separate, traceable fields. Each fragment is preserved
    verbatim from the source; we never invent or parse concentration values.
    """
    out: List[Dict[str, Any]] = []
    product_types = cols.get("product_types")
    if product_types:
        val = (row.get(product_types) or "").strip()
        if val:
            out.append({"field": "product_types", "label": "Product type, body parts",
                        "value": val})
    max_concentration = cols.get("max_concentration")
    if max_concentration:
        val = (row.get(max_concentration) or "").strip()
        if val:
            out.append({"field": "max_concentration",
                        "label": "Maximum concentration in ready for use preparation",
                        "value": val})
    other = cols.get("other")
    if other:
        val = (row.get(other) or "").strip()
        if val:
            out.append({"field": "other", "label": "Other", "value": val})
    warnings = cols.get("warnings")
    if warnings:
        val = (row.get(warnings) or "").strip()
        if val:
            out.append({"field": "warnings",
                        "label": "Wording of conditions of use and warnings",
                        "value": val})
    return out


def _conditions_summary(conditions: List[Dict[str, Any]]) -> Optional[str]:
    """A compact, human-readable summary of an Annex III condition list.

    This is the backward-compatible ``conditions`` string consumed by existing
    code; the full structured list is preserved separately (``conditions_list``
    in the rule's raw provenance) so no detail is lost.
    """
    if not conditions:
        return None
    parts = []
    for c in conditions:
        label = c.get("label") or c.get("field") or "condition"
        val = (c.get("value") or "").strip()
        if not val:
            continue
        # Collapse internal newlines to a single space for the summary string.
        flat = " ".join(val.split())
        parts.append(f"{label}: {flat}")
    return "; ".join(parts) if parts else None


def parse_cosing_csv(text: str, source_url: str,
                     source_updated_at: Optional[str] = None,
                     annex: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse a real CosIng annex export (with its metadata preamble) into rules.

    ``annex`` may be supplied explicitly (e.g. the source dir key
    ``cosing-annex-ii``); otherwise it is read from the preamble.
    """
    lines = text.split("\n")
    preamble_annex = _preamble_annex(lines)
    if annex is None:
        annex = preamble_annex
    if annex is None or annex.lower() not in STATUS_BY_ANNEX:
        return []
    # Reject a disagreement between the export's self-declared annex and the
    # configured source. We never let a directory name override a contradictory
    # declaration (that would risk misclassifying a row's regime).
    if preamble_annex is not None and preamble_annex.lower() != annex.lower():
        raise AnnexMismatchError(
            f"export declares Annex {preamble_annex.upper()} but source is "
            f"configured as Annex {annex.upper()}"
        )

    hdr_idx = _find_header_idx(lines)
    if hdr_idx is None:
        return []
    data_text = "\n".join(lines[hdr_idx:])
    # Use the standard CSV dialect (doublequote=True) for the real CosIng export.
    # The export uses CSV double-quote escaping inside multiline cells; a
    # ``Sniffer``-detected dialect can set doublequote=False and misalign rows
    # containing escaped quotes (""), producing bogus substance names.
    reader = csv.reader(io.StringIO(data_text), dialect="excel")
    try:
        raw_header = next(reader)
    except StopIteration:
        return []
    cols = _header_columns(raw_header)
    name_col = cols["name"]
    if not name_col:
        return []

    status = STATUS_BY_ANNEX[annex.lower()]
    out: List[Dict[str, Any]] = []
    for row in reader:
        if not row:
            continue
        if len(row) < len(raw_header):
            row = row + [""] * (len(raw_header) - len(row))
        record = dict(zip(raw_header, row))
        name = (record.get(name_col) or "").strip()
        if not name:
            continue
        # Reference Number is alphanumeric (e.g. ``2a``), not an int; keep it as
        # a string so nothing is lost/coerced.
        ref_no_col = cols["reference_number"]
        ref_no = None
        if ref_no_col:
            ref_no = (record.get(ref_no_col) or "").strip() or None
        conditions = _combine_conditions(record, cols)
        # The Colour Index identifier (Annex IV colorants); kept as identity
        # evidence, never inferred from a name substring.
        ci = (record.get(cols["ci"]) or "").strip() if cols["ci"] else None
        ci = ci or None
        out.append({
            "name": name,
            "cas": (record.get(cols["cas"]) or "").strip().replace("/", "/")
                   if cols["cas"] else None,
            "ec": (record.get(cols["ec"]) or "").strip()
                  if cols["ec"] else None,
            "ci": ci,
            "annex": f"Annex {annex.upper()}",
            "status": status,
            # For Annex II there are no condition columns, so conditions is None;
            # for Annex III (or a conditional positive list) we keep a compact
            # summary string (backward-compatible) AND the full structured list.
            "conditions": _conditions_summary(conditions),
            "conditions_list": conditions or None,
            "resolution": f"cosing-annex-{annex}",
            "reference_number": ref_no,
            "regulation": (record.get(cols["regulation"]) or "").strip()
                          if cols["regulation"] else None,
            "source_url": source_url,
            "source_updated_at": source_updated_at,
            # Retain the COMPLETE source row (including unrecognised fields) so
            # nothing is lost when the export schema evolves.
            "source_row": {_norm_header(k): v for k, v in record.items()},
        })
    return out


def _norm_header(k: str) -> str:
    """Normalise a source-row header into a stable key for the provenance dict."""
    return str(k).strip().lstrip('"').strip()


def parse_export_file(path: Path, source_url: str,
                      source_updated_at: Optional[str] = None,
                      annex: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse a real CosIng export file (.txt / .csv / .tsv) into verified rules."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".tsv",):
        text = "\n".join(line.replace("\t", ";") for line in text.splitlines())
    return parse_cosing_csv(text, source_url, source_updated_at, annex=annex)
