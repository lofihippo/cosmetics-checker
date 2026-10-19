"""Read one formula from CSV/TSV, preserving decimal strings and absent context.

The ingredient column is required. Blank records are ignored; partial unnamed
rows, duplicate headers, unsupported columns and malformed values raise
ValueError with a record location. UTF-8 BOM and standard CSV quoting are
supported. The resulting records use the shared formula validator.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

# Documented accepted schema (canonical column keys -> the FormulaIngredient
# field they populate).
_ALLOWED_COLUMNS = {
    "ingredient": "ingredient",
    "percent": "percent",
    "unit": "unit",
    "basis": "basis",
    "product_type": "product_type",
    "body_area": "body_area",
    "professional_use": "professional_use",
    "ready_for_use": "ready_for_use",
    "assessment_date": "assessment_date",
}
_REQUIRED_COLUMN = "ingredient"

# Documented boolean spellings (blank stays absent; anything else is rejected).
_TRUE_STRINGS = frozenset({"true", "yes"})
_FALSE_STRINGS = frozenset({"false", "no"})

_DELIMITERS = {"csv": ",", "tsv": "\t"}


class StructuredInputError(ValueError):
    """Raised for invalid structured file content (maps to CLI exit 2)."""


def _parse_boolean(cell: Optional[str], location: str) -> Optional[bool]:
    """Parse a documented true/false boolean cell; blank -> None (absent).

    Rejects a nonempty value that is not an explicit true/false spelling rather
    than treating it as a truthy value, and never defaults to a value.
    """
    if cell is None:
        return None
    low = cell.strip().lower()
    if not low:
        return None
    if low in _TRUE_STRINGS:
        return True
    if low in _FALSE_STRINGS:
        return False
    raise StructuredInputError(
        f"{location}: invalid boolean {cell!r} for a context column; expected "
        f"true/false (or blank).")


def _parse_header(header_row: List[str]) -> List[Dict[str, Any]]:
    """Validate the header row and return a list of column specs.

    Each spec is ``{"key": canonical_key, "optional": bool}`` aligned by column
    index. Duplicate headers and unsupported columns are rejected.
    """
    seen: Dict[str, int] = {}
    columns: List[Dict[str, Any]] = []
    location = "header (row 1)"
    for idx, raw in enumerate(header_row):
        name = (raw or "").strip()
        if not name:
            # A blank header cell is an unsupported/incomplete column.
            raise StructuredInputError(
                f"{location}: unsupported column at position {idx + 1} (blank "
                f"header cell)")
        key = _ALLOWED_COLUMNS.get(name)
        if key is None:
            raise StructuredInputError(
                f"{location}: unsupported column {name!r} (position {idx + 1}); "
                f"allowed columns: {', '.join(_ALLOWED_COLUMNS)}")
        if key in seen:
            raise StructuredInputError(
                f"{location}: duplicate column {name!r} (positions "
                f"{seen[key] + 1} and {idx + 1})")
        seen[key] = idx
        columns.append({"key": key, "optional": key != _REQUIRED_COLUMN})
    if _REQUIRED_COLUMN not in seen:
        raise StructuredInputError(
            f"{location}: missing required column {_REQUIRED_COLUMN!r}; "
            f"allowed columns: {', '.join(_ALLOWED_COLUMNS)}")
    return columns


def read_structured(path: str, file_format: str = "csv") -> Dict[str, Any]:
    """Read a structured CSV/TSV file into a formula dict ``{"ingredients": [...]}``.

    ``file_format`` is ``"csv"`` or ``"tsv"``. Standard CSV/TSV parsing is used
    (quoting, embedded commas/newlines, UTF-8/BOM) with **strict** quoting: a
    malformed or unterminated quoted field is rejected (exit 2) rather than
    silently producing a wrong ingredient string. Invalid content raises
    :class:`ValueError` with the record/line location; it never returns a
    silently-partial formula.

    Error messages report the *record* (logical row) index AND, where a
    malformed field spans a physical line, the physical file line number — these
    differ after a multiline quoted field. ``record N`` counts data records
    (the header is record 0); ``line M`` is the physical 1-based file line.
    """
    fmt = (file_format or "").strip().lower()
    if fmt not in _DELIMITERS:
        raise StructuredInputError(
            f"unsupported input format {file_format!r}; expected 'csv' or 'tsv'")
    delimiter = _DELIMITERS[fmt]

    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        raise StructuredInputError(f"input file not found: {path}") from None
    except UnicodeDecodeError as exc:
        raise StructuredInputError(f"{path}: file is not valid UTF-8") from exc

    # Parse STRICTLY so malformed quoting is rejected. We feed the csv.reader a
    # generator of physical lines so we can track the physical line where each
    # record begins (a multiline quoted field occupies several physical lines, so
    # the *record* number != the *physical line* number).
    lines = text.splitlines(keepends=True)
    line_no = 0

    def _line_gen():
        nonlocal line_no
        for ln in lines:
            line_no += 1
            yield ln

    reader = csv.reader(_line_gen(), delimiter=delimiter, strict=True)

    record_no = 0  # header is the first non-empty record (record 1)
    record_start_line: Dict[int, int] = {}  # record_no -> physical start line
    raw_rows: List[List[str]] = []
    it = iter(reader)
    while True:
        start_line = line_no + 1  # physical line where the next record begins
        try:
            row = next(it)
        except StopIteration:
            break
        except csv.Error as exc:
            # A malformed/unterminated quoted field raises here. The record it
            # belongs to is the one about to be read (record_no + 1).
            raise StructuredInputError(
                f"{path}: CSV parse error at record {record_no + 1}, "
                f"line {start_line}: {exc}") from exc
        if not row:
            # A completely blank physical line -> not a record; advance.
            continue
        record_no += 1
        record_start_line[record_no] = start_line
        raw_rows.append(row)

    if not raw_rows:
        raise StructuredInputError(f"{path}: input file is empty")

    # Header is the first non-empty parsed record (record index 1).
    header = [c or "" for c in raw_rows[0]]
    if not any(h.strip() for h in header):
        raise StructuredInputError(f"{path}: input file has no header row")
    columns = _parse_header(header)

    # Ignore completely-empty records; reject partially completed unnamed rows.
    # ``record_start_line`` maps the 1-based record number to its physical line.
    ingredients: List[Dict[str, Any]] = []
    for r_idx, row in enumerate(raw_rows[1:], start=2):
        # csv.reader gives fields aligned to the columns; a ragged row (not
        # enough or too many cells) is a malformed row shape.
        if len(row) != len(columns):
            raise StructuredInputError(
                f"{path}: record {r_idx} (line "
                f"{record_start_line.get(r_idx, '?')}) has {len(row)} field(s), "
                f"expected {len(columns)} (malformed row shape)")
        cells = [c or "" for c in row]
        # A completely blank record (every cell blank) is ignored.
        if all(not c.strip() for c in cells):
            continue
        location = f"{path}: record {r_idx}, line {record_start_line.get(r_idx, '?')}"
        record: Dict[str, Any] = {}
        for col, raw in zip(columns, cells):
            key = col["key"]
            value = raw.strip()
            # Blank optional context is absent (never inferred / never a value).
            if not value:
                continue
            if key == "professional_use" or key == "ready_for_use":
                record[key] = _parse_boolean(value, location)
            else:
                # percent and all string context are passed as their raw string
                # (decimal concentrations stay strings until shared Decimal
                # validation; no numeric coercion here).
                record[key] = value
        # A partially completed unnamed row (no ingredient but other data).
        if "ingredient" not in record:
            raise StructuredInputError(
                f"{location}: missing ingredient; a row with other data but no "
                f"ingredient is not a valid record")
        ingredients.append(record)

    if not ingredients:
        raise StructuredInputError(f"{path}: input file contains no ingredient "
                                   f"records (all rows blank)")

    return {"ingredients": ingredients}
