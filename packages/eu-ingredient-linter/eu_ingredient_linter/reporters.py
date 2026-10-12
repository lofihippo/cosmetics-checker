"""Cited CSV/HTML/text reporters for eu-ingredient-linter reports.

``render(report, out_format)`` renders an existing report (from the label,
newline-batch, or structured-formula modes) into ``text``, ``json``, ``csv`` or
``html``. It NEVER reclassifies ingredients: it renders the rules / verdicts /
determinations already produced by :mod:`eu_ingredient_linter.formula` or the
label/batch report functions. Changing the output format never changes the
evaluation or the exit status.

Every evidence row carries the regulation reference, source URL, snapshot
``data_updated_at``, official reference, file-row locator and available
provenance. ``source_updated_at`` (upstream pub/update time) and ``retrieved_at``
(download time) are kept distinct from the report/snapshot ``data_updated_at``
and from each other.

CSV uses a documented, deterministic flat schema (one row per ingredient-rule
evidence; a row is also emitted for an unknown/unverified ingredient with no
rules). Nested/list values are serialised as JSON inside quoted cells so no
evidence is lost merely because CSV is flat.

HTML is a standalone printable document with embedded CSS and no external
runtime dependency. User/source text and URLs are escaped; link schemes are
validated. It preserves unknown and unresolved states and never turns a
numeric-within-limit or exit-0 into "clean/compliant".

``Regulatory information, not legal advice.``
"""

from __future__ import annotations

import csv
import html
import io
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

DISCLAIMER = "Regulatory information, not legal advice."

# ---------------------------------------------------------------------------
# URL / text safety
# ---------------------------------------------------------------------------

_ALLOWED_LINK_SCHEMES = frozenset({"https", "http"})


def _safe_link(url: Optional[str]) -> Optional[str]:
    """Return ``url`` only if it has an allowed scheme; otherwise None."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_LINK_SCHEMES or not parsed.netloc:
        return None
    return url


def _esc(text: Any) -> str:
    """HTML-escape an arbitrary value for safe inline rendering."""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# GitHub workflow-command escaping.
# ---------------------------------------------------------------------------
# GitHub annotation commands follow ``::<command> <properties> :: <message>``.
# Both command *properties* and the *message* have distinct escape rules. The
# message additionally must have newlines/carriage-returns replaced so an
# untrusted ingredient/source string cannot inject a second workflow command.
# References: https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions
_GITHUB_ESCAPE_MESSAGE = {"%": "%25", "\r": "%0D", "\n": "%0A"}
_GITHUB_ESCAPE_PROPERTY = {"%": "%25", "\r": "%0D", "\n": "%0A",
                           ":": "%3A", ",": "%2C"}


def _gh_escape(value: Any, property_value: bool = False) -> str:
    """Escape a value for a GitHub workflow command.

    ``property_value=True`` escapes for a command property (``key=value`` pair,
    where ``:``/``,`` are also escaped); otherwise escapes for the command
    message body (where a newline is replaced so a value cannot terminate and
    begin a fresh ``::`` command).
    """
    table = _GITHUB_ESCAPE_PROPERTY if property_value else _GITHUB_ESCAPE_MESSAGE
    out = ""
    for ch in str(value):
        out += table.get(ch, ch)
    return out


def _gh_property_value(value: Any) -> str:
    """Escape a command property value (``file``/``line``/``title``/...)."""
    return _gh_escape(value, property_value=True)


def _gh_message(value: Any) -> str:
    """Escape a command message body so no value can inject a new command."""
    return _gh_escape(value, property_value=False)


# ---------------------------------------------------------------------------
# Shared evidence extraction (works for formula evaluations AND label/batch
# CheckResult.as_dict() records).
# ---------------------------------------------------------------------------

def _rules_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(record.get("rules") or [])


def _linked_rules_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(record.get("linked_rules") or [])


def _membership_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(record.get("membership") or [])


def _provenance(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a merged provenance dict (formula ``provenance`` or top-level)."""
    prov = record.get("provenance") or {}
    # Per-rule provenance (official_reference, row_reference, source_file, ...)
    # is emitted per-rule; these are the record-level snapshot/he'dline fields.
    return {
        "regulation": prov.get("regulation") or record.get("regulation"),
        "source_url": prov.get("source_url") or record.get("source_url"),
        "source_updated_at": prov.get("source_updated_at")
        or record.get("source_updated_at"),
        "data_updated_at": prov.get("data_updated_at")
        or record.get("data_updated_at"),
        "retrieved_at": prov.get("retrieved_at") or record.get("retrieved_at"),
    }


def _rule_raw(rule: Dict[str, Any]) -> Dict[str, Any]:
    return rule.get("raw") or {}


def _source_scope_text(rule: Dict[str, Any]) -> Optional[str]:
    """Return the source-row scope/exception text for a rule (e.g. Hydroquinone's
    ``... with the exception of entry 14 in Annex III``), or None when absent/redundant.

    Retained as its own evidence so a scoped/conditional source name is not lost
    by CSV/HTML (it is already present in the JSON ``raw.source_row``).
    """
    raw = _rule_raw(rule)
    src_row = raw.get("source_row") or {}
    src_name = (src_row.get("Chemical name / INN") or "").strip()
    if not src_name:
        return None
    # Only surface when the name carries a qualification beyond the substance's
    # own plain name (never redundant for an unqualified name).
    return src_name


# Deterministic CSV column list. Order is part of the documented schema.
CSV_COLUMNS = [
    "report_kind", "batch_item_index", "item_ingredient_index",
    "ingredient", "resolved_name", "resolution", "is_in_dataset",
    "determination", "evidence_kind", "rule_status", "annex",
    "status", "verdict", "evaluation", "evaluation_reason",
    "official_reference", "reference_number", "regulation", "source_url",
    "source_updated_at", "data_updated_at", "retrieved_at",
    "row_reference", "source_file", "source_sha256", "source_kind",
    "submitted_value", "unit", "basis", "cap", "numeric_comparison",
    "applicable_context", "prohibition_finding",
    "submission_context", "conditions_fingerprint", "conditions_list",
    "source_scope_text", "conditions_summary",
    "unresolved_requirements", "reasons", "membership", "meta",
]


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _record_line(record: Dict[str, Any],
                 report_kind: str,
                 batch_item_index: Optional[int],
                 item_ingredient_index: int,
                 evidence_kind: str,
                 rule: Optional[Dict[str, Any]],
                 report_data_updated_at: Optional[str] = None) -> Dict[str, Any]:
    """Build one CSV row for a single rule evidence (or a no-rule marker)."""
    raw = _rule_raw(rule) if rule is not None else {}
    prov = _provenance(record)
    # Snapshot timestamp: prefer the record provenance, else the report-level
    # snapshot time. Every evidence row (incl. unknown / no-rule / linked-only
    # rows whose record has no rule provenance) must carry the snapshot time.
    data_updated_at = prov.get("data_updated_at") or report_data_updated_at
    # Retain structured conditions (conditions_list) AND the summary conditions
    # field; a rule may populate only one of them (e.g. an Annex II prohibition
    # with a summary ``conditions`` but no ``conditions_list``).
    conditions_list = raw.get("conditions_list")
    conditions_summary = (rule or {}).get("conditions") if rule is not None else None
    if not conditions_list and conditions_summary:
        conditions_list = [{"label": "conditions", "value": conditions_summary}]
    # Preserve the raw submitted context exactly as supplied (from the shared
    # evaluation, which now carries it). This keeps absent vs false vs true
    # distinct and never reconstructs user input from the rule's context.
    sub_ctx = record.get("submitted_context") or {
        "unit": record.get("unit"),
        "basis": record.get("basis"),
        "product_type": record.get("product_type"),
        "body_area": record.get("body_area"),
        "professional_use": record.get("professional_use"),
        "ready_for_use": record.get("ready_for_use"),
    }
    return {
        "report_kind": report_kind,
        "batch_item_index": batch_item_index,
        "item_ingredient_index": item_ingredient_index,
        "ingredient": record.get("ingredient"),
        "resolved_name": record.get("resolved_name"),
        "resolution": record.get("resolution"),
        "is_in_dataset": record.get("is_in_dataset"),
        "determination": record.get("determination"),
        "evidence_kind": evidence_kind,
        "rule_status": (rule or {}).get("status") if rule is not None else None,
        "annex": (rule or {}).get("annex") if rule is not None else record.get("annex"),
        "status": (rule or {}).get("status") if rule is not None else record.get("status"),
        "verdict": record.get("verdict"),
        "evaluation": record.get("evaluation"),
        "evaluation_reason": record.get("evaluation_reason"),
        "official_reference": raw.get("official_reference"),
        "reference_number": raw.get("reference_number"),
        "regulation": (rule or {}).get("regulation") if rule is not None
        else prov.get("regulation"),
        "source_url": (rule or {}).get("source_url") if rule is not None
        else prov.get("source_url"),
        "source_updated_at": (rule or {}).get("source_updated_at") if rule is not None
        else prov.get("source_updated_at"),
        "data_updated_at": data_updated_at,
        "retrieved_at": raw.get("retrieved_at") or prov.get("retrieved_at"),
        "row_reference": raw.get("row_reference"),
        "source_file": raw.get("source_file"),
        "source_sha256": raw.get("source_sha256"),
        "source_kind": raw.get("source_kind"),
        "submitted_value": record.get("submitted_value"),
        "unit": record.get("unit"),
        "basis": record.get("basis"),
        "cap": record.get("cap"),
        "numeric_comparison": record.get("numeric_comparison"),
        "applicable_context": record.get("applicable_context"),
        "prohibition_finding": record.get("prohibition_finding"),
        "submission_context": _json_cell(sub_ctx),
        "conditions_fingerprint": raw.get("conditions_fingerprint")
        or prov.get("conditions_fingerprint"),
        "conditions_list": _json_cell(conditions_list),
        "source_scope_text": _source_scope_text(rule) if rule is not None else None,
        "conditions_summary": conditions_summary if rule is not None else None,
        "unresolved_requirements": _json_cell(record.get("unresolved_requirements")),
        "reasons": _json_cell(record.get("reasons")),
        "membership": _json_cell(_membership_of(record)),
        "meta": "",
    }


def _meta_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return machine-readable metadata rows (coverage + disclaimer).

    Emitted as normal CSV rows with ``report_kind="meta"`` so a conforming
    ``csv.reader`` parses them as data, never as a lost comment block. They are
    distinguishable from ingredient rows by ``report_kind``.
    """
    cov = report.get("coverage") or {}
    return [
        {
            "report_kind": "meta", "batch_item_index": "", "item_ingredient_index": "",
            "ingredient": "", "resolved_name": "", "resolution": "",
            "is_in_dataset": "", "determination": "", "evidence_kind": "coverage",
            "rule_status": "", "annex": "", "status": "",
            "verdict": "", "evaluation": "", "evaluation_reason": "",
            "official_reference": "", "reference_number": "",
            "regulation": "", "source_url": "", "source_updated_at": "",
            "data_updated_at": report.get("data_updated_at"),
            "retrieved_at": "", "row_reference": "", "source_file": "",
            "source_sha256": "", "source_kind": "", "submitted_value": "",
            "unit": "", "basis": "", "cap": "", "numeric_comparison": "",
            "applicable_context": "", "prohibition_finding": "",
            "submission_context": "", "conditions_fingerprint": "",
            "conditions_list": "", "source_scope_text": "",
            "conditions_summary": "",
            "unresolved_requirements": "",
            "reasons": "", "membership": "",
            "meta": _json_cell(cov),
        },
        {
            "report_kind": "meta", "batch_item_index": "", "item_ingredient_index": "",
            "ingredient": "", "resolved_name": "", "resolution": "",
            "is_in_dataset": "", "determination": "", "evidence_kind": "disclaimer",
            "rule_status": "", "annex": "", "status": "",
            "verdict": "", "evaluation": "", "evaluation_reason": "",
            "official_reference": "", "reference_number": "",
            "regulation": "", "source_url": "", "source_updated_at": "",
            "data_updated_at": report.get("data_updated_at"),
            "retrieved_at": "", "row_reference": "", "source_file": "",
            "source_sha256": "", "source_kind": "", "submitted_value": "",
            "unit": "", "basis": "", "cap": "", "numeric_comparison": "",
            "applicable_context": "", "prohibition_finding": "",
            "submission_context": "", "conditions_fingerprint": "",
            "conditions_list": "", "source_scope_text": "",
            "conditions_summary": "",
            "unresolved_requirements": "",
            "reasons": "", "membership": "",
            "meta": report.get("disclaimer") or DISCLAIMER,
        },
    ]


def _iter_rule_rows(record: Dict[str, Any], report_kind: str,
                    batch_item_index: Optional[int],
                    item_ingredient_index: int,
                    report_data_updated_at: Optional[str] = None) -> Iterable[Dict[str, Any]]:
    """Yield CSV rows for one ingredient record.

    Each direct rule is its own row (``evidence_kind="direct"``); each linked
    group rule is its own row (``evidence_kind="linked"``). An ingredient with
    no own rules and no linked rules yields a single ``evidence_kind="no_rule"``
    row, so unknown/unverified/clean-but-unresolved states are always present.
    """
    direct = _rules_of(record)
    linked = _linked_rules_of(record)
    if not direct and not linked:
        yield _record_line(record, report_kind, batch_item_index,
                           item_ingredient_index, "no_rule", None,
                           report_data_updated_at)
        return
    for rule in direct:
        yield _record_line(record, report_kind, batch_item_index,
                           item_ingredient_index, "direct", rule,
                           report_data_updated_at)
    for rule in linked:
        yield _record_line(record, report_kind, batch_item_index,
                           item_ingredient_index, "linked", rule,
                           report_data_updated_at)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def render(report: Dict[str, Any], out_format: str,
           github_file: Optional[str] = None) -> str:
    """Render ``report`` as text/json/csv/html/github.

    ``github_file`` is the consumer's input-file path (workspace-relative is
    recommended) used only as the ``file`` property of GitHub annotations. It is
    escaped and never used as a line number or a regulatory reference.
    """
    fmt = (out_format or "").strip().lower()
    if fmt == "json":
        return json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
    if fmt == "csv":
        return _render_csv(report)
    if fmt == "html":
        return _render_html(report)
    if fmt == "github":
        return _render_github(report, github_file)
    # Fallback for text (normally handled by __main__).
    return _render_human(report)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _iter_all_rows(report: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield all CSV rows for any report kind (formula / label / batch).

    Batch reports additionally emit an explicit per-item ``item_state`` row so a
    saved CSV remains self-describing even without the process exit code: the
    item number, its validity, its ``no_ingredients`` state and (where applicable)
    the invalid item index list. Invalid/empty items always yield an
    ``item_state`` row; no ingredient finding is invented for them.
    """
    report_dt = report.get("data_updated_at")
    if "evaluations" in report:
        for i, e in enumerate(report["evaluations"], 1):
            yield from _iter_rule_rows(e, "formula", None, i, report_dt)
    elif "items" in report:
        invalid_indices = report.get("invalid_item_indices") or []
        for item_i, item in enumerate(report["items"], 1):
            # Per-item validity/state row (self-describing, always emitted).
            yield {
                "report_kind": "batch", "batch_item_index": item_i,
                "item_ingredient_index": "",
                "ingredient": "", "resolved_name": "", "resolution": "",
                "is_in_dataset": "", "determination": "",
                "evidence_kind": "item_state",
                "rule_status": "", "annex": "", "status": "",
                "verdict": item.get("verdict"), "evaluation": "",
                "evaluation_reason": "",
                "official_reference": "", "reference_number": "",
                "regulation": "", "source_url": "", "source_updated_at": "",
                "data_updated_at": report_dt, "retrieved_at": "",
                "row_reference": "", "source_file": "", "source_sha256": "",
                "source_kind": "", "submitted_value": "", "unit": "", "basis": "",
                "cap": "", "numeric_comparison": "", "applicable_context": "",
                "prohibition_finding": "", "submission_context": "",
                "conditions_fingerprint": "", "conditions_list": "",
                "source_scope_text": "", "conditions_summary": "",
                "unresolved_requirements": "", "reasons": "", "membership": "",
                "meta": _json_cell({
                    "is_valid": not item.get("no_ingredients"),
                    "no_ingredients": bool(item.get("no_ingredients")),
                    "invalid_item_index": item_i - 1,
                    "all_invalid_indices": invalid_indices,
                    "item_input_state": "no_ingredients" if item.get("no_ingredients") else "ok",
                }),
            }
            for ing_i, r in enumerate(item.get("results") or [], 1):
                yield from _iter_rule_rows(r, "batch", item_i, ing_i, report_dt)
    else:
        # Single label report.
        for i, r in enumerate(report.get("results") or [], 1):
            yield from _iter_rule_rows(r, "label", None, i, report_dt)


def _render_csv(report: Dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in _iter_all_rows(report):
        writer.writerow({k: row.get(k) for k in CSV_COLUMNS})
    # Machine-readable coverage + disclaimer as valid data rows (report_kind
    # "meta"), so a conforming csv reader parses them and evidence is never lost.
    for meta in _meta_rows(report):
        writer.writerow({k: meta.get(k) for k in CSV_COLUMNS})
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _render_html(report: Dict[str, Any]) -> str:
    cov = report.get("coverage") or {}
    parts: List[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append("<html lang='en'>")
    parts.append("<head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append("<title>EU Ingredient Linter Report</title>")
    parts.append(_CSS)
    parts.append("</head><body>")
    parts.append("<h1>EU Ingredient Linter Report</h1>")

    # Snapshot / coverage / summary.
    parts.append("<section id='snapshot'>")
    parts.append("<h2>Snapshot and coverage</h2>")
    parts.append("<dl class='grid'>")
    parts.append(f"<dt>data_updated_at</dt><dd>{_esc(report.get('data_updated_at'))}</dd>")
    parts.append(f"<dt>registry_size</dt><dd>{_esc(report.get('registry_size'))}</dd>")
    parts.append(f"<dt>available</dt><dd>{_esc(', '.join(cov.get('available', [])) or '(none)')}</dd>")
    parts.append(f"<dt>missing</dt><dd>{_esc(', '.join(cov.get('missing', [])) or '(none)')}</dd>")
    parts.append("<dt>coverage_note</dt><dd>"
                 f"{_esc(cov.get('note', 'Missing lists are a coverage gap, NOT a clean result.'))}</dd>")
    parts.append("</dl>")
    parts.append("</section>")

    # Summary per report kind (formula / batch / label).
    parts.append("<section id='summary'>")
    parts.append("<h2>Summary</h2>")
    if "evaluations" in report:
        parts.append(f"<p>{len(report['evaluations'])} ingredient(s) evaluated.</p>")
    elif "items" in report:
        parts.append(f"<p>{report['n_items']} item(s), "
                     f"{report['total_ingredients']} ingredient token(s), "
                     f"{report['total_flagged']} flagged, "
                     f"{report['total_banned']} banned, "
                     f"{report['total_unverified']} unverified.</p>")
        invalid_indices = report.get("invalid_item_indices") or []
        if report.get("has_invalid_input"):
            parts.append(f"<p class='warn'>Invalid input: item(s) "
                         f"{_esc(invalid_indices)} (0-based) had no "
                         f"ingredient tokens; not evaluated.</p>")
    else:
        parts.append(f"<p>verdict={_esc(report.get('verdict'))} "
                     f"n={_esc(report.get('n_ingredients'))} "
                     f"flagged={_esc(report.get('n_flagged'))} "
                     f"banned={_esc(report.get('n_banned'))} "
                     f"unverified={_esc(report.get('n_unverified'))}.</p>")
    parts.append("</section>")

    # Per-ingredient sections.
    report_dt = report.get("data_updated_at")
    parts.append("<section id='ingredients'>")
    parts.append("<h2>Ingredients</h2>")
    if "evaluations" in report:
        for i, e in enumerate(report["evaluations"], 1):
            parts.append(_ingredient_html(e, f"Formula ingredient {i}", formula=True,
                                          report_data_updated_at=report_dt))
    elif "items" in report:
        for item_i, item in enumerate(report["items"], 1):
            parts.append(f"<h3>Batch item {item_i}</h3>")
            if item.get("no_ingredients"):
                parts.append("<p class='warn'>No ingredient tokens parsed; this "
                             "item is invalid and was not evaluated.</p>")
            for ing_i, r in enumerate(item.get("results") or [], 1):
                parts.append(_ingredient_html(r, f"Ingredient {ing_i}", formula=False,
                                              report_data_updated_at=report_dt))
    else:
        for r in report.get("results") or []:
            parts.append(_ingredient_html(r, r.get("ingredient") or "Ingredient",
                                          formula=False,
                                          report_data_updated_at=report_dt))
    parts.append("</section>")

    parts.append(f"<footer class='disclaimer'><p>{_esc(report.get('disclaimer') or DISCLAIMER)}</p></footer>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def _ingredient_html(record: Dict[str, Any], heading: str,
                     formula: bool,
                     report_data_updated_at: Optional[str] = None) -> str:
    """Render one ingredient's direct + linked evidence, unresolved requirements,
    citations and unknown/unresolved state."""
    parts = []
    parts.append("<article class='ingredient'>")
    parts.append(f"<h4>{_esc(heading)}: {_esc(record.get('ingredient'))}</h4>")
    parts.append("<dl class='grid'>")
    fields = [
        ("submitted", record.get("ingredient")),
        ("resolved_name", record.get("resolved_name")),
        ("resolution", record.get("resolution")),
        ("is_in_dataset", record.get("is_in_dataset")),
        ("determination", record.get("determination")),
        ("verdict", record.get("verdict")),
        ("rule_status", record.get("rule_status")),
        ("annex", record.get("annex")),
        ("evaluation", record.get("evaluation")),
        ("evaluation_reason", record.get("evaluation_reason")),
        ("submitted_value", record.get("submitted_value")),
        ("unit", record.get("unit")),
        ("basis", record.get("basis")),
        ("cap", record.get("cap")),
        ("numeric_comparison", record.get("numeric_comparison")),
        ("applicable_context", record.get("applicable_context")),
        ("prohibition_finding", record.get("prohibition_finding")),
    ]
    for label, value in fields:
        if value is not None and value != "":
            parts.append(f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>")
    # Raw submitted context, preserved exactly as supplied (absent vs false vs true).
    sub_ctx = record.get("submitted_context")
    if sub_ctx:
        parts.append("<dl class='grid context'>")
        for key in ("unit", "basis", "product_type", "body_area",
                    "professional_use", "ready_for_use"):
            if key in sub_ctx:
                parts.append(f"<dt>{_esc(key)}</dt><dd>{_esc(sub_ctx[key])}</dd>")
        parts.append("</dl>")
    parts.append("</dl>")

    # Unresolved requirements (never claim these are satisfied).
    unresolved = record.get("unresolved_requirements") or []
    if unresolved:
        parts.append("<div class='unresolved'><h5>Unresolved requirements</h5><ul>")
        for u in unresolved:
            parts.append(f"<li>{_esc(u)}</li>")
        parts.append("</ul></div>")

    # Direct evidence.
    direct = _rules_of(record)
    if direct:
        parts.append("<div><h5>Direct evidence</h5>")
        for rule in direct:
            parts.append(_rule_html(rule, "direct", record, report_data_updated_at))
        parts.append("</div>")
    # Linked (group) evidence.
    linked = _linked_rules_of(record)
    if linked:
        parts.append("<div><h5>Linked group evidence</h5>")
        for rule in linked:
            parts.append(_rule_html(rule, "linked", record, report_data_updated_at))
        parts.append("</div>")
    # Membership records.
    membership = _membership_of(record)
    if membership:
        parts.append("<div class='membership'><h5>Group membership</h5><ul>")
        for m in membership:
            parts.append(f"<li>{_esc(m.get('group_name'))} "
                         f"(annex {_esc(m.get('annex'))}, "
                         f"ref {_esc(m.get('official_reference'))})</li>")
        parts.append("</ul></div>")
    # Unknown / unresolved marker.
    if record.get("is_in_dataset") is False or record.get("determination") == "unknown":
        parts.append("<p class='unknown'>Unknown / unresolved identity — never "
                     "a clean result.</p>")
    if not direct and not linked and record.get("is_in_dataset") is not False:
        parts.append("<p class='unresolved'>No direct or linked regulatory rule "
                     "retained in this snapshot; not a compliance determination.</p>")
    parts.append("</article>")
    return "\n".join(parts)


def _citation_text(rule: Dict[str, Any], record: Dict[str, Any],
                   evidence_kind: str,
                   report_data_updated_at: Optional[str] = None) -> str:
    """A single-line citation for a rule (regulation, source URL, timestamps).

    The snapshot timestamp is threaded from the report so a linked-only result
    (e.g. Sodium benzoate, whose record has no own-rule provenance) still
    carries the report's snapshot ``data_updated_at``. Retrieval and publication
    times are never substituted for the snapshot time.
    """
    parts = []
    if rule.get("regulation"):
        parts.append(rule["regulation"])
    url = _safe_link(rule.get("source_url"))
    if url:
        parts.append(url)
    dt = rule.get("source_updated_at")
    if dt:
        parts.append(f"pub/updated {dt}")
    snapshot = (record.get("provenance") or {}).get("data_updated_at") \
        or record.get("data_updated_at") or report_data_updated_at
    if snapshot:
        parts.append(f"snapshot {snapshot}")
    raw = _rule_raw(rule)
    if raw.get("retrieved_at"):
        parts.append(f"retrieved {raw['retrieved_at']}")
    return " ".join(parts) or "(no citation available)"


def _rule_html(rule: Dict[str, Any], evidence_kind: str,
               record: Dict[str, Any],
               report_data_updated_at: Optional[str] = None) -> str:
    raw = _rule_raw(rule)
    parts = []
    cls = "rule " + ("linked" if evidence_kind == "linked" else "direct")
    parts.append(f"<div class='{cls}'>")
    parts.append(f"<p><strong>status</strong>={_esc(rule.get('status'))}; "
                 f"<strong>annex</strong>={_esc(rule.get('annex'))}; "
                 f"<strong>reference</strong>={_esc(raw.get('official_reference'))}; "
                 f"<strong>row</strong>={_esc(raw.get('row_reference'))}")
    parts.append(f"<br><span class='cite'>{_esc(_citation_text(rule, record, evidence_kind, report_data_updated_at))}</span></p>")
    # Rule conditions (unsupported / errors preserved). Retain structured
    # conditions (conditions_list) AND the summary ``conditions`` field; a rule
    # may populate only one of them (e.g. an Annex II prohibition with a summary
    # conditions but no conditions_list).
    conds = raw.get("conditions_list")
    if not conds and rule.get("conditions"):
        conds = [{"label": "conditions", "value": rule.get("conditions")}]
    if conds:
        parts.append("<ul class='conditions'>")
        for c in conds:
            label = c.get("label") or c.get("field") or "Condition"
            value = (c.get("value") or "").replace("\n", " ").strip()
            if value:
                parts.append(f"<li>{_esc(label)}: {_esc(value)}</li>")
        parts.append("</ul>")
    # Source row scope/exception text (e.g. Hydroquinone's Annex II qualification).
    scope = _source_scope_text(rule)
    if scope:
        parts.append(f"<p class='prov'>source_text: {_esc(scope)}</p>")
    if raw.get("source_sha256"):
        parts.append(f"<p class='prov'>source_sha256={_esc(raw['source_sha256'])}; "
                     f"source_file={_esc(raw.get('source_file'))}</p>")
    parts.append("</div>")
    return "\n".join(parts)


# Embedded CSS (standalone, no external dependency).
_CSS = """<style>
:root{--fg:#1a1a1a;--bg:#fff;--muted:#555;--accent:#0b5;--warn:#b00;--link:#036;}
*{box-sizing:border-box;}body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--fg);background:var(--bg);margin:0;padding:1.5rem;line-height:1.5;}
h1{font-size:1.5rem;border-bottom:2px solid var(--accent);padding-bottom:.3rem;}
h2{font-size:1.15rem;margin-top:1.6rem;}h3{font-size:1.05rem;margin-top:1.2rem;}
h4{font-size:.98rem;margin:.8rem 0 .3rem;}h5{font-size:.9rem;margin:.4rem 0;}
section,article{max-width:60rem;margin:0 auto 1rem;}
dl.grid{display:grid;grid-template-columns:max-content 1fr;gap:.2rem .9rem;margin:.4rem 0;}
dl.grid dt{font-weight:600;color:var(--muted);}dl.grid dd{margin:0;}
.rule{border:1px solid #ccc;border-left:3px solid var(--accent);padding:.5rem .7rem;margin:.4rem 0;
border-radius:4px;background:#fafafa;}
.rule.linked{border-left-color:#888;background:#f4f4f4;}
.rule.direct{border-left-color:var(--accent);}
.cite{color:var(--muted);font-size:.82rem;word-break:break-word;}
ul{margin:.2rem 0 .4rem;padding-left:1.2rem;}
.conditions{font-size:.86rem;}
.unresolved{background:#fff6e0;border-left:3px solid var(--warn);padding:.4rem .7rem;margin:.4rem 0;}
.unknown{color:var(--warn);font-weight:600;}
.warn{color:var(--warn);}
.prov{color:var(--muted);font-size:.78rem;}
footer.disclaimer{max-width:60rem;margin:2rem auto 0;padding-top:1rem;border-top:1px solid #ddd;
font-style:italic;color:var(--muted);}
@media print{body{padding:.5rem;}}
</style>"""


# ---------------------------------------------------------------------------
# GitHub workflow annotations (--format github).
# ---------------------------------------------------------------------------


def _finding_level(record: Dict[str, Any]) -> str:
    """Map an ingredient finding to a GitHub annotation level (error|warning|notice).

    This is a presentational severity policy, separate from regulatory
    classification. A definitive prohibition or a supported-limit exceedance is
    ``error``; an identified restriction/conditional/positive-list evidence or an
    unresolved scope/exception assessment is ``warning``; an unknown /
    not-in-dataset ingredient is ``notice`` (never a proven violation).

    The level is derived ONLY from the evaluator's assessment (``verdict`` /
    ``prohibition_finding``), NOT from a single source rule's ``status``. A
    source rule that is ``banned`` does not by itself force ``error``: an
    ``exception_unresolved`` or ``scope_unresolved`` assessment stays
    ``warning``, and linked-group evidence is never promoted to a definitive
    prohibition.
    """
    verdict = record.get("verdict")
    if verdict == "exceeds_supported_limit":
        return "error"
    if record.get("prohibition_finding") == "unambiguous":
        return "error"
    if record.get("determination") == "unknown" and not _rules_of(record) \
            and not _linked_rules_of(record):
        return "notice"
    return "warning"


def _finding_type(record: Dict[str, Any]) -> str:
    """A short human label for the finding (presentational, not a claim)."""
    verdict = record.get("verdict")
    if verdict == "exceeds_supported_limit":
        return "exceeds_supported_limit"
    if record.get("prohibition_finding") == "unambiguous":
        return "unambiguous_prohibition"
    if record.get("prohibition_finding") == "scope_unresolved":
        return "scope_unresolved"
    if record.get("prohibition_finding") == "exception_unresolved":
        return "exception_unresolved"
    if verdict == "unknown_ingredient":
        return "unknown_ingredient"
    if _linked_rules_of(record) and not _rules_of(record):
        return "linked_group_evidence"
    return verdict or "identified_evidence"


def _citation_for_annotation(rule: Dict[str, Any], record: Dict[str, Any],
                             report_data_updated_at: Optional[str]) -> str:
    """A concise citation string for an annotation (regulation, source URL,
    official reference, snapshot timestamp)."""
    parts = []
    if rule.get("regulation"):
        parts.append(rule["regulation"])
    url = _safe_link(rule.get("source_url"))
    if url:
        parts.append(url)
    ref = (rule.get("raw") or {}).get("official_reference")
    if ref is not None:
        parts.append(f"ref {ref}")
    snapshot = (record.get("provenance") or {}).get("data_updated_at") \
        or record.get("data_updated_at") or report_data_updated_at
    if snapshot:
        parts.append(f"snapshot {snapshot}")
    return " ".join(parts) or "(no citation available)"


def _linked_group_label(rule: Dict[str, Any],
                        record: Dict[str, Any]) -> Optional[str]:
    """Return a short source-group label for a LINKED group rule, or None.

    The group identity lives in the record's ``membership`` (group_name, annex,
    official_reference), which is matched to the linked rule by annex and
    official_reference. Returns a label like ``Salicylic acid and its salts
    (Annex V, ref 3)`` so a linked annotation is identifiable without changing
    regulatory interpretation. Multi-line values are single-spaced.
    """
    raw = rule.get("raw") or {}
    annex = rule.get("annex")
    ref = raw.get("official_reference")
    group = ""
    for m in _membership_of(record):
        if annex and m.get("annex") != annex:
            continue
        if ref is not None and str(m.get("official_reference")) != str(ref):
            continue
        if m.get("group_name"):
            group = " ".join(str(m["group_name"]).split())
            break
    parts = []
    if group:
        parts.append(group)
    elif annex:
        parts.append(f"Annex {annex}")
    if ref is not None:
        parts.append(f"ref {ref}")
    return "; ".join(parts) if parts else None


def _annotation_command(level: str, message: str,
                        title: str,
                        file: Optional[str] = None,
                        line: Optional[int] = None) -> str:
    """Emit one ``::<level>`` annotation command with escaped props/message."""
    props = [f"title={_gh_property_value(title)}"]
    if file:
        props.insert(0, f"file={_gh_property_value(file)}")
    if line is not None:
        props.append(f"line={line}")
    return f"::{level} {','.join(props)} ::{_gh_message(message)}"


def _render_github(report: Dict[str, Any],
                   github_file: Optional[str] = None) -> str:
    """Render GitHub workflow-annotation commands for the report's findings.

    Emits one annotation per direct/linked rule and one for an ingredient with no
    rules (unknown / not-in-dataset). Severity is ``_finding_level`` — a
    presentational policy that never derives a definitive prohibition from a
    single source rule's ``banned`` status or from linked group evidence.

    ``github_file`` is the consumer's input-file path used only as the ``file``
    property (escaped); GitHub attaches the annotation to that file. A precise
    input-file line is never invented: no ``line`` property and never a
    regulatory source ``row_reference`` as the user's line. A required
    disclaimer notice is appended.
    """
    report_dt = report.get("data_updated_at")
    out: List[str] = []

    def _records() -> Iterable[Tuple[Optional[int], int, Dict[str, Any]]]:
        # Yield (batch_item_index|None, item_ingredient_index|1, record).
        if "evaluations" in report:
            for i, e in enumerate(report["evaluations"], 1):
                yield None, i, e
        elif "items" in report:
            for item_i, item in enumerate(report["items"], 1):
                for ing_i, r in enumerate(item.get("results") or [], 1):
                    yield item_i, ing_i, r
        else:
            for i, r in enumerate(report.get("results") or [], 1):
                yield None, i, r

    for item_i, idx, record in _records():
        ingredient = record.get("ingredient") or "(unknown)"
        level = _finding_level(record)
        title = f"EU ingredient {ingredient}"
        resolved = record.get("resolved_name") or ingredient
        determination = record.get("determination") or "unknown"
        # If an ingredient has rules, emit one annotation per rule (so the
        # citation/snapshot is per-rule). If it has no rules (unknown /
        # not-in-dataset), emit a single annotation describing the unresolved
        # identity — never presented as a proven violation.
        direct = _rules_of(record)
        linked = _linked_rules_of(record)
        if not direct and not linked:
            base = (f"{ingredient} [{_finding_type(record)}] resolved={resolved} "
                    f"determination={determination}; no regulatory rule in this "
                    f"snapshot; not a compliance determination")
            if record.get("unresolved_requirements"):
                base += "; unresolved scope/conditions stay unresolved"
            out.append(_annotation_command(level, base, title, file=github_file))
            continue
        # A record may carry BOTH direct and linked evidence (e.g. Salicylic acid
        # has a direct Annex III restriction and a linked Annex V group rule).
        # Preserve the origin so a linked finding is never read as a definitive
        # direct prohibition. Iterate each origin separately.
        for evidence_kind, these_rules in (("direct", direct), ("linked", linked)):
            for rule in these_rules:
                # Severity comes from the evaluator's assessment, NOT from the
                # rule's source status. A banned source rule on an unresolved
                # exception/scope stays a warning; linked evidence is never a
                # definitive prohibition.
                origin = "direct" if evidence_kind == "direct" else "linked group"
                rtitle = (f"EU {rule.get('annex')} {rule.get('status')} — "
                          f"{ingredient}")
                rmsg = (f"{ingredient} [{_finding_type(record)}] resolved={resolved} "
                        f"determination={determination}; origin={origin}; "
                        f"{_citation_for_annotation(rule, record, report_dt)}")
                if evidence_kind == "linked":
                    grp = _linked_group_label(rule, record)
                    if grp:
                        rmsg += f"; source group {grp}"
                if record.get("unresolved_requirements"):
                    rmsg += "; scope/conditions unresolved (not assessed)"
                out.append(_annotation_command(level, rmsg, rtitle,
                                               file=github_file))

    out.append(_annotation_command("notice", DISCLAIMER, "EU Ingredient Linter"))
    return "\n".join(out) + ("\n" if out else "")


def _render_human(report: Dict[str, Any]) -> str:
    """Minimal text fallback for text format (used only outside the CLI)."""
    from io import StringIO
    buf = StringIO()
    cov = report.get("coverage", {})
    if cov:
        buf.write(f"Coverage available: {', '.join(cov.get('available', [])) or '(none)'}\n")
        buf.write(f"Coverage missing:   {', '.join(cov.get('missing', [])) or '(none)'}\n")
    if report.get("data_updated_at"):
        buf.write(f"data_updated_at: {report['data_updated_at']}\n")
    for e in report.get("evaluations", []):
        buf.write(f"[{e.get('ingredient')}] verdict={e.get('verdict')} "
                  f"det={e.get('determination')}\n")
        for req in e.get("unresolved_requirements") or []:
            buf.write(f"    unresolved: {req}\n")
    return buf.getvalue() + (report.get("disclaimer") or DISCLAIMER) + "\n"
