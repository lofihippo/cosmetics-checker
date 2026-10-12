"""Command-line interface for eu-ingredient-linter.

Reads a single label (``--label``) or a batch file (``--batch``, one label per
line) and prints a JSON report. Coverage (Annex II available, Annex III–VI and
ECHA missing) and official Reference Numbers are surfaced.

Usage::

    python -m eu_ingredient_linter --registry data/processed/eu_reg_lists.sqlite \
        --label "Aqua, Benzene, TotallyFakeIngredient"
    python -m eu_ingredient_linter --registry data/processed/eu_reg_lists.sqlite \
        --batch labels.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from eu_reg_lists import load

from . import batch_report, ingredient_report


def _registry_from(path: str) -> object:
    p = Path(path)
    if not p.exists():
        sys.exit(f"registry not found: {p}")
    return load(p)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="eu-ingredient-linter",
                                 description="Check ingredients/labels/formulas against the EU regulatory registry.")
    ap.add_argument("--registry", required=True,
                    help="path to eu_reg_lists.sqlite (.sqlite) or .jsonl")
    ap.add_argument("--label", help="single label string (comma/semicolon-separated)")
    ap.add_argument("--batch", help="path to a batch file, one label per line")
    ap.add_argument("--formula", help="path to a structured formula JSON file")
    ap.add_argument("--input", help="path to a structured formula file (CSV/TSV/JSON)")
    ap.add_argument("--input-format", default="csv",
                    choices=["csv", "tsv", "json"],
                    help="format for --input (default: csv)")
    ap.add_argument("--format", default="text", choices=["text", "json", "csv", "html", "github"],
                    help="output report format (default: text)")
    ap.add_argument("--github-file",
                    help="consumer input-file path (workspace-relative recommended) used "
                         "as the ``file`` property of GitHub annotations; never a line number")
    ap.add_argument("--output",
                    help="also write the cited JSON report to this path (atomic replace); "
                         "a write failure is an operational error (exit 2) but does not "
                         "change the evaluation")
    ap.add_argument("--check-report",
                    help="validate an existing JSON report file's structure; exit 0 if it "
                         "is a well-formed current EU Ingredient Linter report, 2 if it is "
                         "missing/malformed/incomplete. Requires --registry (unused for the "
                         "content). No evaluation is run.")
    ap.add_argument("--json", action="store_true", help="emit raw JSON (shorthand for --format json)")
    args = ap.parse_args(argv)

    if args.check_report:
        return _validate_report(args.check_report)

    reg = _registry_from(args.registry)
    n_inputs = sum(1 for x in (args.label, args.batch, args.formula, args.input)
                   if x)
    if n_inputs != 1:
        print("error: provide exactly one of --label, --batch, --formula or --input",
              file=sys.stderr)
        return 2
    # Resolve the output format. ``--json`` is a preserved shorthand for
    # ``--format json``; specifying both ``--json`` and a non-json ``--format``
    # is a conflict.
    out_format = args.format
    if args.json:
        if out_format != "text" and out_format != "json":
            print("error: --json conflicts with --format "
                  f"{out_format!r}", file=sys.stderr)
            return 2
        out_format = "json"
    if args.format == "text" and args.json:
        out_format = "json"

    # Confirm the structured-input/output options are mutually exclusive.
    if args.input and args.label:
        print("error: --input cannot be combined with --label", file=sys.stderr)
        return 2
    if args.input and args.batch:
        print("error: --input cannot be combined with --batch", file=sys.stderr)
        return 2
    if args.input and args.formula:
        print("error: --input cannot be combined with --formula", file=sys.stderr)
        return 2

    if args.input:
        from .inputs import read_structured
        if args.input_format == "json":
            from .formula import evaluate_formula_file
            try:
                report = evaluate_formula_file(args.input, reg)
            except (ValueError, ArithmeticError, OSError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        else:
            from .formula import evaluate_formula
            try:
                report = evaluate_formula(read_structured(args.input, args.input_format), reg)
            except (ValueError, ArithmeticError, OSError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    else:
        if args.formula:
            from .formula import evaluate_formula_file
            try:
                report = evaluate_formula_file(args.formula, reg)
            except (ValueError, ArithmeticError, OSError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        elif args.batch:
            items = [line.strip() for line in Path(args.batch).read_text(encoding="utf-8").splitlines()
                     if line.strip()]
            report = batch_report(items, reg)
        elif args.label:
            report = ingredient_report(args.label, reg)
        else:
            print("error: provide --label or --batch", file=sys.stderr)
            return 2

    _emit_report(report, out_format, args.github_file)
    if args.output:
        write_rc = _write_report_atomic(report, Path(args.output))
        if write_rc != 0:
            return write_rc
    return _exit_code(report)


def _emit_report(report: dict, out_format: str,
                 github_file: Optional[str] = None) -> None:
    """Emit ``report`` in the requested format. Changing the output format never
    changes the evaluation or the exit status."""
    if out_format == "text":
        _print_human(report)
        return
    from .reporters import render
    sys.stdout.write(render(report, out_format, github_file=github_file))


def _write_report_atomic(report: dict, path: Path) -> int:
    """Atomically write the cited JSON report to ``path``.

    A report-write failure is an OPERATIONAL error, distinct from a regulatory
    finding: it returns 2 so the caller never presents a missing/empty/stale
    report as a successful current result. The evaluation and exit policy are
    unchanged. Writing to a temp file then ``os.replace`` avoids leaving a
    partial report if the write fails midway.
    """
    import json
    import os
    import tempfile
    try:
        # The parent must exist; refuse to create directories silently so a
        # typo'd path cannot appear to succeed.
        if not path.parent.exists():
            print(f"error: report output directory does not exist: {path.parent}",
                  file=sys.stderr)
            return 2
        payload = json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                   dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"error: could not write the JSON report to {path}: {exc}",
              file=sys.stderr)
        return 2
    return 0


def _validate_report(path: str) -> int:
    """Validate that a file is a well-formed, current EU Ingredient Linter report.

    This is NOT a regulatory classification or a second evaluator: it only
    confirms the report the current invocation produced actually exists, parses
    as JSON, is a dict, carries the required disclaimer and snapshot time, and
    contains at least one ingredient record (``evaluations``, ``items`` or
    ``results``). A parseable ``{}`` is not sufficient.

    Returns 0 if valid, 2 if missing/malformed/incomplete, and prints a short
    diagnostic (never a success-shaped report).
    """
    import json
    p = Path(path)
    if not p.is_file():
        print(f"error: no report generated at {p}", file=sys.stderr)
        return 2
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"error: report is not valid JSON at {p}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print(f"error: report is not a JSON object at {p}", file=sys.stderr)
        return 2
    disclaimer = (data.get("disclaimer") or "").strip()
    if not disclaimer:
        print("error: report is missing the required disclaimer", file=sys.stderr)
        return 2
    if not data.get("data_updated_at"):
        print("error: report is missing data_updated_at", file=sys.stderr)
        return 2
    has_records = any(isinstance(data.get(k), list) and data[k]
                      for k in ("evaluations", "items", "results"))
    if not has_records:
        print("error: report has no ingredient records (evaluations/items/results)",
              file=sys.stderr)
        return 2
    print("report-valid: yes")
    return 0


def _exit_code(report: dict) -> int:
    """Exit-code convention (mirrors the Phase-0 eu_lint CLI):

    * 2 -> invalid/empty input. For a batch this means: no valid labels after
        blank-line removal, OR at least one label that yields zero tokens.
    * 1 -> a definitive ban/restriction/conditional flag was triggered.
    * 0 -> otherwise (incl. "not in dataset"/unverified, which is NOT a flag).

    An unresolved scope/exception applicability (e.g. Hydroquinone's cross-annex
    exception) is NOT inferred as a prohibition from the headline determination
    alone; it stays exit 0 (unresolved).
    """
    if "evaluations" in report:
        # Formula: exit 1 when a supported limit was EXCEEDED or an ingredient
        # carries an UNAMBIGUOUS prohibition finding (e.g. a pure Annex II ban
        # like Benzene).
        for e in report["evaluations"]:
            if e["verdict"] == "exceeds_supported_limit":
                return 1
            if e.get("prohibition_finding") == "unambiguous":
                return 1
        return 0
    if "items" in report:
        # Batch: invalid input takes precedence over any flag.
        if report.get("has_invalid_input"):
            print("error: batch contains invalid (tokenless/empty) label(s) "
                  f"{report.get('invalid_item_indices') or '[]'}", file=sys.stderr)
            return 2
        if report.get("total_flagged"):
            return 1
        return 0
    if report.get("no_ingredients"):
        print("error: no ingredients parsed from input", file=sys.stderr)
        return 2
    if report.get("n_flagged"):
        return 1
    return 0


def _print_human(report: dict) -> None:
    cov = report.get("coverage", {})
    if cov:
        print(f"Coverage: Annex II present = {cov.get('has_annex_ii')}")
        print(f"  available: {', '.join(cov.get('available', [])) or '(none)'}")
        print(f"  missing:   {', '.join(cov.get('missing', [])) or '(none)'}")
    dt = report.get("data_updated_at")
    if dt:
        print(f"data_updated_at: {dt}")
    if "items" in report:
        print(f"Batch: {report['n_items']} item(s), "
              f"{report['total_ingredients']} ingredient token(s), "
              f"{report['total_flagged']} flagged, "
              f"{report['total_banned']} banned, "
              f"{report['total_unverified']} unverified")
        if report.get("has_invalid_input"):
            print(f"  invalid_input: {report.get('invalid_item_indices') or ('all' if report['n_items']==0 else '[]')}")
        for i, item in enumerate(report["items"], 1):
            marker = " [INVALID]" if item["no_ingredients"] else ""
            print(f"  [{i}]{marker} verdict={item['verdict'] or '?'} "
                  f"n={item['n_ingredients']} flagged={item['n_flagged']} "
                  f"banned={item['n_banned']} unverified={item['n_unverified']}")
            for r in item.get("results", []):
                _print_finding(r, indent="      ")
    elif "evaluations" in report:
        print(f"Formula: {len(report['evaluations'])} ingredient(s)")
        for e in report["evaluations"]:
            _print_formula_evaluation(e, indent="  ")
    else:
        print(f"verdict={report.get('verdict')} n={report.get('n_ingredients')} "
              f"flagged={report.get('n_flagged')} banned={report.get('n_banned')} "
              f"unverified={report.get('n_unverified')}")
        for r in report.get("results", []):
            _print_finding(r, indent="  ")
    print(report.get("disclaimer", ""))


def _print_finding(r: dict, indent: str = "  ") -> None:
    """Print one ingredient finding, rendering EACH rule as a separate block.

    Each block carries the rule's own status, annex, official reference, file-row
    locator, regulation, source URL, ``data_updated_at`` and that rule's
    conditions. The single ``r['status']`` (headline strongest) is only a summary
    label on the ingredient line; it never substitutes for per-rule status.
    """
    flagged = r["status"] in ("banned", "restricted", "allowed_with_conditions")
    # A grouped member may have no OWN rules yet a linked group rule still flags
    # it; reflect that in the marker.
    linked = r.get("linked_rules") or []
    if not flagged and any(lr["status"] in ("banned", "restricted", "allowed_with_conditions")
                           for lr in linked):
        flagged = True
    marker = "FLAG" if flagged else "    "
    line = f"{indent}{marker} {r['ingredient']:<32} det={r['determination']}"
    if r.get("evaluation"):
        line += f"  eval={r['evaluation']}"
    print(line)

    rules = r.get("rules") or []
    for m in r.get("membership") or []:
        print(f"{indent}  [member_of] group={m.get('group_name')} "
              f"annex={m.get('annex')} ref={m.get('official_reference')} "
              f"<{m.get('source_kind')}>{m.get('row_reference')}")
    blocks = rules + linked
    if not blocks:
        return
    for rule in blocks:
        raw = rule.get("raw") or {}
        block_indent = indent + "  "
        # Per-rule status (NOT the ingredient headline).
        rule_status = rule.get("status") or "unknown"
        print(f"{block_indent}[rule] status={rule_status} annex={rule.get('annex') or 'Not provided'}")
        ref = _format_citation(rule, raw, r.get("data_updated_at"))
        if ref:
            print(f"{block_indent}      {ref}")
        # Official reference number vs file-row locator (kept separate).
        off = raw.get("official_reference")
        row = raw.get("row_reference")
        if off is not None:
            print(f"{block_indent}      official_reference={off}")
        if row:
            print(f"{block_indent}      row_reference={row}")
        if rule.get("resolution"):
            print(f"{block_indent}      resolution={rule['resolution']}")
        # Expose scope/exception text from the original source row (e.g. the
        # Annex II hydroquinone qualification referencing Annex III entry 14).
        # Only surfaced when the source name carries a qualification beyond the
        # substance's own name (never redundant for a plain name).
        src_row = raw.get("source_row") or {}
        src_name = (src_row.get("Chemical name / INN") or "").strip()
        cased = r.get("cased_name") or ""
        if src_name and src_name.lower() != str(cased).lower():
            print(f"{block_indent}      source_text: {src_name}")
        # That rule's own conditions (structured Annex III fragments).
        conds = raw.get("conditions_list") or []
        if conds:
            for c in conds:
                label = c.get("label") or c.get("field") or "Condition"
                value = (c.get("value") or "").replace("\n", " ").strip()
                if value:
                    print(f"{block_indent}      {label}: {value}")
        elif rule.get("conditions"):
            cond = str(rule["conditions"]).replace("\n", " ").strip()
            if cond:
                print(f"{block_indent}      Conditions: {cond}")


def _print_formula_evaluation(e: dict, indent: str = "  ") -> None:
    """Print one formula-ingredient evaluation (separate rule/context/numeric
    fields; never conflates a numeric comparison with a compliance claim)."""
    print(f"{indent}[{e['ingredient']}] verdict={e.get('verdict') or '?'}")
    print(f"{indent}    resolved={e.get('resolved_name')} resolution={e.get('resolution')}")
    print(f"{indent}    is_in_dataset={e.get('is_in_dataset')} determination={e.get('determination')}")
    if e.get("rule_status"):
        print(f"{indent}    rule_status={e.get('rule_status')} annex={e.get('annex')}")
    # Show the submitted value + unit/basis + cap and the numeric comparison.
    if e.get("submitted_value") is not None:
        print(f"{indent}    submitted={e.get('submitted_value')} unit={e.get('unit')} "
              f"basis={e.get('basis')} cap={e.get('cap') or 'n/a'}")
    # Raw submitted context (absent vs false vs true preserved distinctly).
    sub = e.get("submitted_context")
    if sub:
        print(f"{indent}    submitted_context={sub}")
    if e.get("applicable_context"):
        print(f"{indent}    context={e.get('applicable_context')}")
    if e.get("numeric_comparison"):
        print(f"{indent}    numeric={e.get('numeric_comparison')}")
    # All identified rules, retained as evidence (per-rule citation).
    for rule in e.get("rules") or []:
        raw = rule.get("raw") or {}
        off = raw.get("official_reference")
        print(f"{indent}    rule status={rule.get('status')} annex={rule.get('annex')} "
              f"{rule.get('regulation') or ''} <{rule.get('source_url') or ''}> "
              f"data_updated_at={e.get('provenance', {}).get('data_updated_at')}"
              + (f" official_reference={off}" if off is not None else ""))
    # Explicit source-backed group membership + linked rules.
    for m in e.get("membership") or []:
        print(f"{indent}    [member_of] group={m.get('group_name')} annex={m.get('annex')} "
              f"ref={m.get('official_reference')} "
              f"<{m.get('source_kind')}>{m.get('row_reference')}")
    for rule in e.get("linked_rules") or []:
        raw = rule.get("raw") or {}
        off = raw.get("official_reference")
        line = (f"{indent}    [linked rule] status={rule.get('status')} annex={rule.get('annex')} "
                f"{rule.get('regulation') or ''} <{rule.get('source_url') or ''}>")
        if off is not None:
            line += f" official_reference={off}"
        print(line)
    for req in e.get("unresolved_requirements") or []:
        print(f"{indent}    unresolved: {req}")
    for reason in e.get("reasons") or []:
        print(f"{indent}    reason: {reason}")
    src = e.get("source_rule") or {}
    if src.get("reference_number") is not None:
        print(f"{indent}    source ref={src.get('reference_number')} "
              f"{src.get('name')} regulation={src.get('regulation')}")
    prov = e.get("provenance") or {}
    if prov.get("data_updated_at"):
        print(f"{indent}    data_updated_at={prov.get('data_updated_at')}")


def _format_citation(rule: dict, raw: dict,
                     data_updated_at: Optional[str] = None) -> str:
    """Human-readable citation: regulation, source URL, data_updated_at.

    ``data_updated_at`` is the snapshot build/update time (resolved by the
    report); it is intentionally distinct from ``source_updated_at``
    (upstream pub/update date, may be None) and from ``retrieved_at``
    (download time), which is never used here.
    """
    parts = []
    if rule.get("regulation"):
        parts.append(rule["regulation"])
    if rule.get("source_url"):
        parts.append(f"<{rule['source_url']}>")
    if data_updated_at:
        parts.append(f"data_updated_at={data_updated_at}")
    if not parts:
        return ""
    return " ".join(parts)


if __name__ == "__main__":
    sys.exit(main())
