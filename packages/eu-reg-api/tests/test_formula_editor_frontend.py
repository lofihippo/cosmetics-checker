"""Repeatable frontend regression tests for the formula editor (/formula).

These start a real Uvicorn app on a free port and drive it with Playwright
(system Chrome) so the three-state context handling, request lifecycle,
partially-completed-row validation, and rendering are verified against real
HTTP responses. They use an isolated in-memory registry (never the workspace
snapshot); controllable promise-based requests that ignore AbortSignal prove
the generation guard independently of request timing.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from eu_ingredient_linter.formula import SUPPORTED_RULES
from eu_reg_api.main import create_app
from eu_reg_lists import Registry, Rule, Substance
from playwright.sync_api import sync_playwright

DATA_UPDATED_AT = "2026-10-09T00:00:00+00:00"


def _annex3_rule(ref):
    conds = [dict(c) for c in SUPPORTED_RULES[ref]["conditions_template"]]
    return Rule(
        status="restricted", annex="Annex III", regulation="(EC) 2013/344",
        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
        source_updated_at=None, resolution="cosing-annex-iii",
        raw={"official_reference": ref, "row_reference": f"f.txt#{ref}",
             "source_file": "COSING_Annex_III_v2.txt", "source_sha256": "abc",
             "reference_number": ref, "accepted": True, "conditions_list": conds},
    )


def _registry():
    verified = {
        "cosing-annex-ii": {"present": True, "accepted": True},
        "cosing-annex-iii": {"present": True, "accepted": True},
        "cosing-annex-iv": {"present": True, "accepted": True},
        "cosing-annex-vi": {"present": True, "accepted": True},
        "echa-svhc": {"present": False, "accepted": False},
    }
    toluene = Substance(
        id="toluene", name="Toluene", cas="108-88-3", ec="203-625-9",
        determination="restricted", rules=[_annex3_rule("185")],
        sources=["cosing-annex-iii"])
    benzene = Substance(
        id="benzene", name="Benzene", cas="71-43-2", ec="200-753-7",
        determination="banned",
        rules=[Rule(status="banned", annex="Annex II",
                    regulation="(EC) 2009/1223",
                    source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                    source_updated_at=None, resolution="cosing-annex-ii",
                    raw={"official_reference": 47, "row_reference": "f#49",
                         "conditions_list": None})],
        sources=["cosing-annex-ii"])
    hydroq = Substance(
        id="hydroquinone", name="Hydroquinone", cas="123-31-9", ec="204-617-8",
        determination="banned",
        rules=[Rule(status="banned", annex="Annex II",
                    regulation="(EC) 2013/344",
                    source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                    source_updated_at=None, resolution="cosing-annex-ii",
                    raw={"official_reference": 1339, "row_reference": "f#1339",
                         "conditions_list": None,
                         "source_row": {"Chemical name / INN": "Hydroquinone"}}),
               _annex3_rule("14")],
        sources=["cosing-annex-ii", "cosing-annex-iii"])
    aqua = Substance(id="water", name="Water", aliases=["Aqua"],
                     cas="7732-18-5", determination="unknown", rules=[])
    # Conditional positive-list substance (Annex IV colorant + Annex VI UV filter):
    # used to verify the browser preserves cross-list evidence without inventing a
    # numeric verdict or declaring the ingredient clean.
    zno = Substance(
        id="zinc-oxide", name="Zinc oxide", cas="1314-13-2", ci="CI 77947",
        determination="allowed_with_conditions",
        rules=[Rule(status="allowed_with_conditions", annex="Annex IV",
                    regulation="(EU) 2017/1413",
                    source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                    source_updated_at=None, resolution="cosing-annex-iv",
                    conditions="Maximum concentration in ready for use preparation: 25%",
                    raw={"official_reference": 144, "row_reference": "IV#145",
                         "conditions_list": [{"field": "max_concentration",
                                              "label": "Maximum concentration in ready for use preparation",
                                              "value": "25%"}]}),
               Rule(status="allowed_with_conditions", annex="Annex VI",
                    regulation="(EU) 2016/621",
                    source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                    source_updated_at=None, resolution="cosing-annex-vi",
                    conditions="Maximum concentration in ready for use preparation: 25%",
                    raw={"official_reference": "30a", "row_reference": "VI#29",
                         "conditions_list": [{"field": "max_concentration",
                                              "label": "Maximum concentration in ready for use preparation",
                                              "value": "25%"}]})],
        sources=["cosing-annex-iv", "cosing-annex-vi"])
    metadata = {"data_updated_at": DATA_UPDATED_AT, "version": "0.2.0",
                "count_substances": 5, "verified_sources": verified,
                "snapshot": verified}
    reg = Registry([toluene, benzene, hydroq, aqua, zno],
                   data_updated_at=DATA_UPDATED_AT,
                   verified_sources=verified, metadata=metadata)
    return reg, metadata


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    reg, metadata = _registry()
    app = create_app(reg, metadata)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server_uv = uvicorn.Server(config)
    thread = threading.Thread(target=server_uv.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server_uv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    p = browser.new_page()
    p.add_init_script(
        "window.dispatchSubmit = function () {\n"
        "  document.getElementById('submit').dispatchEvent(\n"
        "    new MouseEvent('click', { bubbles: true, cancelable: true }));\n"
        "};")
    p.goto(server + "/formula")
    p.wait_for_selector("#snap-version")
    yield (p, server)
    p.close()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _row(page, idx):
    return page.locator(".ingredient-row").nth(idx)


def _fill(page, idx, **kw):
    row = _row(page, idx)
    if "ingredient" in kw:
        row.locator(".f-ingredient").fill(kw["ingredient"])
    if "percent" in kw:
        row.locator(".f-percent").fill(kw["percent"])
    if "unit" in kw:
        row.locator(".f-unit").select_option(kw["unit"])
    if "basis" in kw:
        row.locator(".f-basis").select_option(kw["basis"])
    if "product" in kw:
        row.locator(".f-product").select_option(kw["product"])
    if "body" in kw:
        row.locator(".f-body-area").fill(kw["body"])
    if "professional" in kw:
        row.locator(".f-professional").select_option(kw["professional"])
    if "ready" in kw:
        row.locator(".f-ready").select_option(kw["ready"])


def _evaluate(page):
    page.click("#submit")
    page.wait_for_selector("#formula-results .eval-card", timeout=8000)
    page.wait_for_timeout(120)


def _message(page):
    return (page.text_content("#results-message") or "").strip()


def _count_cards(page):
    return page.locator("#formula-results .eval-card").count()


# ---------------------------------------------------------------------------
# Controllable fetch that ignores AbortSignal and records every request body.
# ---------------------------------------------------------------------------
_CONTROLLED_FETCH = r"""
window.__formulaRequests = [];
window.__realFetch = window.fetch.bind(window);
window.__deferredFetch = function (url, opts) {
  if (String(url).indexOf("/v1/check-formula") === -1) {
    return window.__realFetch(url, opts);
  }
  var entry = { url: String(url), body: null, resolved: false, resolve: null, reject: null };
  if (opts && opts.body != null) entry.body = JSON.parse(String(opts.body));
  entry.promise = new Promise(function (resolve, reject) {
    entry.resolve = resolve; entry.reject = reject;
  });
  window.__formulaRequests.push(entry);
  return entry.promise;
};
window.settleFormula = function (index, payload, status) {
  var e = window.__formulaRequests[index];
  var body = new TextEncoder().encode(JSON.stringify(payload));
  e.resolve(new Response(body, { status: status || 200, headers: {
    "content-type": "application/json" } }));
  e.resolved = true;
};
window.rejectFormula = function (index) {
  var e = window.__formulaRequests[index];
  e.reject(new TypeError("network failure"));
  e.resolved = true;
};
window.__formulaCount = function () { return window.__formulaRequests.length; };
window.fetch = window.__deferredFetch;
"""


def _install_controlled(page, server):
    page.add_init_script(_CONTROLLED_FETCH)
    page.goto(server + "/formula")
    page.wait_for_selector("#snap-version")


def _reqs(page):
    return page.evaluate("window.__formulaCount()")


def _settle(page, index, payload, status=200):
    page.evaluate("([i,p,s]) => window.settleFormula(i, p, s)", [index, payload, status])
    page.wait_for_timeout(30)


def _reject(page, index):
    page.evaluate("(i) => window.rejectFormula(i)", [index])
    page.wait_for_timeout(30)


def _body(page, index):
    return page.evaluate("(i) => window.__formulaRequests[i].body", [index])


# Synthetic per-ingredient evaluation payloads. Distinguishable by name so an
# out-of-order resolution can be detected.
def _toluene_payload(percent=None, comp="within_supported_numeric_limit"):
    return {
        "evaluations": [{
            "ingredient": "Toluene", "resolved_name": "Toluene", "resolution": "exact_name_or_alias",
            "is_in_dataset": True, "determination": "restricted", "rule_status": "restricted",
            "annex": "Annex III", "applicable_context": "Nail products",
            "numeric_comparison": comp, "submitted_value": percent,
            "unit": "%", "basis": "ready_for_use", "cap": "25%",
            "unresolved_requirements": ["Wording of conditions of use and warnings: Keep out of reach of children. To be used by adults only"],
            "reasons": ["note"], "verdict": comp,
            "rules": [], "source_rule": {}, "provenance": {},
        }],
        "data_updated_at": "2026-10-09T00:00:00+00:00", "registry_size": 4,
        "coverage": {"available": ["cosing-annex-ii", "cosing-annex-iii"],
                     "missing": ["cosing-annex-iv"], "has_annex_ii": True, "note": "n"},
        "disclaimer": "Regulatory information, not legal advice.",
    }


def _benzene_payload():
    return {
        "evaluations": [{
            "ingredient": "Benzene", "resolved_name": "Benzene", "resolution": "exact_name_or_alias",
            "is_in_dataset": True, "determination": "banned", "rule_status": "banned",
            "annex": "Annex II", "applicable_context": "Annex II prohibition (no Annex III numeric mapping).",
            "numeric_comparison": None, "submitted_value": "1", "unit": "%", "basis": None, "cap": None,
            "unresolved_requirements": ["Annex II prohibition retained; no Annex III numeric mapping."],
            "reasons": ["note"], "verdict": "requires_manual_review",
            "rules": [], "source_rule": {}, "provenance": {},
        }],
        "data_updated_at": "2026-10-09T00:00:00+00:00", "registry_size": 4,
        "coverage": {"available": ["cosing-annex-ii", "cosing-annex-iii"],
                     "missing": ["cosing-annex-iv"], "has_annex_ii": True, "note": "n"},
        "disclaimer": "Regulatory information, not legal advice.",
    }


# ---------------------------------------------------------------------------
# Context / request payload correctness.
# ---------------------------------------------------------------------------
def test_new_row_context_not_supplied(page):
    p, server = page
    # The initial blank row must come up with unit/basis/product/bools all
    # "not supplied" (empty value) — never defaulted in the browser.
    row = _row(p, 0)
    assert row.locator(".f-unit").input_value() == ""
    assert row.locator(".f-basis").input_value() == ""
    assert row.locator(".f-product").input_value() == ""
    assert row.locator(".f-professional").input_value() == ""
    assert row.locator(".f-ready").input_value() == ""


def test_partial_row_without_name_is_rejected(page):
    p, server = page
    _row(p, 0).locator(".f-percent").fill("20")
    _row(p, 0).locator(".f-unit").select_option("%")
    p.evaluate("window.dispatchSubmit()")
    p.wait_for_timeout(80)
    assert _count_cards(p) == 0
    msg = _message(p)
    assert "missing" in msg.lower() or "name" in msg.lower(), msg
    assert "Row 1" in msg, msg


def test_fully_empty_row_submit_shows_add_message(page):
    p, server = page
    p.evaluate("window.dispatchSubmit()")
    p.wait_for_timeout(80)
    msg = _message(p)
    assert "Add at least one" in msg, msg


def test_payload_preserves_boolean_states_absent_true_false(page):
    p, server = page
    _install_controlled(p, server)
    # Row 1: professional=Yes, ready=No (both supplied as explicit booleans).
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%", basis="ready_for_use",
          product="nail_product", professional="true", ready="false")
    p.evaluate("window.dispatchSubmit()")
    p.wait_for_timeout(40)
    assert _reqs(p) == 1
    ing = _body(p, 0)["ingredients"][0]
    assert ing["professional_use"] is True
    assert ing["ready_for_use"] is False
    # Concentration remains the exact decimal string the user typed.
    assert ing["percent"] == "20"

    # Row 2: leave both booleans "not supplied" -> fields absent (not false/null).
    _fill(p, 0, professional="", ready="")
    p.wait_for_timeout(30)  # changes invalidate; resubmit a fresh request
    p.evaluate("window.dispatchSubmit()")
    p.wait_for_timeout(40)
    assert _reqs(p) >= 2
    ing2 = _body(p, 1)["ingredients"][0]
    assert "professional_use" not in ing2
    assert "ready_for_use" not in ing2


def test_examples_populate_documented_context(page):
    p, server = page
    p.click(".example-btn[data-example='supported']")
    p.wait_for_timeout(40)
    _fill(p, 0, ingredient="")
    p.click(".example-btn[data-example='supported']")   # reset to example
    p.wait_for_timeout(40)
    row = _row(p, 0)
    assert row.locator(".f-ingredient").input_value() == "Toluene"
    assert row.locator(".f-unit").input_value() == "%"
    assert row.locator(".f-basis").input_value() == "ready_for_use"
    assert row.locator(".f-product").input_value() == "nail_product"


# ---------------------------------------------------------------------------
# Rendering against real HTTP responses.
# ---------------------------------------------------------------------------
def test_supported_comparison_renders_unresolved_warnings(page):
    p, server = page
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%", basis="ready_for_use",
          product="nail_product")
    _evaluate(p)
    body = p.text_content("#formula-results")
    assert "within the supported numeric limit" in body.lower()
    assert "Nail products" in body
    assert "Keep out of reach of children" in body
    assert "data_updated_at" in p.text_content("#summary")
    assert "Regulatory information, not legal advice" in p.content()


def test_exact_decimal_boundary_through_request_and_response(page):
    p, server = page
    captured = []
    def handler(route, request):
        ctype = request.headers.get("content-type", "")
        if request.method == "POST" and "json" in ctype:
            captured.append(request.post_data_json)
        route.continue_()
    p.route("**/v1/check-formula", handler)
    _fill(p, 0, ingredient="Toluene", percent="25.0000000000000001", unit="%",
          basis="ready_for_use", product="nail_product")
    _evaluate(p)
    # Request preserved the exact decimal string (no float coercion).
    assert captured, "no POST body captured"
    assert captured[0]["ingredients"][0]["percent"] == "25.0000000000000001"
    # Response from the real API: exceeds the 25% cap, retained as exactly that string.
    body = p.text_content("#formula-results")
    assert "above the supported numeric limit" in body.lower()
    assert "25.0000000000000001" in body


def test_missing_context_result_unevaluated(page):
    p, server = page
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")   # no basis/product
    _evaluate(p)
    body = p.text_content("#formula-results")
    assert "requires context" in body.lower()
    assert "not evaluated" in body.lower()


def test_benzene_hydroquinone_recognised_unverified_unmatched(page):
    p, server = page
    # Benzene -> Annex II prohibition retained.
    p.click(".example-btn[data-example='benzene']")
    p.wait_for_timeout(30)
    _evaluate(p)
    assert "annex ii" in p.text_content("#formula-results").lower()
    assert "requires manual review" in p.text_content("#formula-results").lower()

    # Hydroquinone -> cross-annex manual review.
    p.click(".example-btn[data-example='hydroquinone']")
    p.wait_for_timeout(30)
    _evaluate(p)
    hq = p.text_content("#formula-results")
    assert "requires manual review" in hq.lower()
    assert "cross-annex" in hq.lower()

    # Recognised-but-unverified (Aqua: in dataset, no verified rule).
    p.click(".example-btn[data-example='supported']")
    p.wait_for_timeout(30)
    p.click(".example-btn[data-example='missing-context']")
    p.wait_for_timeout(30)
    _fill(p, 0, ingredient="Aqua", percent="1", unit="%")
    _evaluate(p)
    assert "known" in p.text_content("#formula-results").lower() or \
        "unverified" in p.text_content("#formula-results").lower()

    # Unmatched / unknown ingredient -> never "clean".
    p.click(".example-btn[data-example='unknown']")
    p.wait_for_timeout(30)
    _evaluate(p)
    body = p.text_content("#formula-results")
    assert "not found" in body.lower()
    assert "clean" not in body.lower()


def test_positive_list_evidence_rendered_without_clean_claim(page):
    """A conditional positive-list ingredient (Zinc oxide : Annex IV + VI) is
    rendered with its conditions/citations but never presented as clean."""
    p, server = page
    _fill(p, 0, ingredient="Zinc oxide", percent="10", unit="%")
    _evaluate(p)
    body = p.text_content("#formula-results")
    assert "zinc oxide" in body.lower()
    # Cross-list rules retained with conditions.
    assert "Annex IV" in body
    assert "Annex VI" in body
    assert "25%" in body
    # No numeric verdict / clean claim; unresolved/manual-review wording.
    assert "within the supported numeric limit" not in body.lower()
    assert "not evaluated" in body.lower() or "requires manual review" in body.lower()
    assert "Regulatory information, not legal advice" in p.content()


# ---------------------------------------------------------------------------
# Request lifecycle (controllable, abort-ignoring).
# ---------------------------------------------------------------------------
def test_supported_example_renders_real_snapshot(page):
    p, server = page
    p.click(".example-btn[data-example='supported']")
    p.wait_for_timeout(30)
    _evaluate(p)
    assert _count_cards(p) == 1


def test_edit_invalidates_pending_and_ignores_old_success(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 1
    # Edit invalidates; the still-pending request cannot render.
    _fill(p, 0, percent="30")
    p.wait_for_timeout(40)
    _settle(p, 0, _toluene_payload("20"))
    assert _count_cards(p) == 0
    assert _message(p) == ""


def test_add_remove_rows_invalidate_pending(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 1
    p.click("#add-row")       # add row invalidates
    p.wait_for_timeout(30)
    p.click(".ingredient-row .remove-row")   # remove last row -> adds blank row
    p.wait_for_timeout(30)
    assert _count_cards(p) == 0
    assert _message(p) == ""


def test_ab_responses_resolved_out_of_order_keeps_newer(page):
    p, server = page
    _install_controlled(p, server)
    # A = Toluene, B = Benzene. Start A then B.
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%", basis="ready_for_use",
          product="nail_product")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 1
    _fill(p, 0, ingredient="Benzene", percent="1", unit="%")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 2
    _settle(p, 1, _benzene_payload())          # B resolves first (newer)
    _settle(p, 0, _toluene_payload("20"))      # A resolves second (older) -> ignored
    body = p.text_content("#formula-results")
    assert "benzen" in body.lower() or "annex ii" in body.lower()
    # The newer B result must win; A's "within limit" must not be present.
    assert "within the supported numeric limit" not in body.lower()


def test_stale_rejection_and_old_finally_do_not_override(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")       # A pending
    _fill(p, 0, percent="30")                   # invalidate A (no new request)
    p.wait_for_timeout(30)
    _reject(p, 0)                                # stale rejection
    assert _message(p) == ""
    assert _count_cards(p) == 0


def test_network_error_without_stale_results(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 1
    _reject(p, 0)
    assert "Network failure" in _message(p)
    assert _count_cards(p) == 0


def test_api_error_without_stale_results(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")
    assert _reqs(p) == 1
    _settle(p, 0, {"error": {"code": "invalid_formula", "message": "bad"}}, status=422)
    assert "Request error" in _message(p)
    assert _count_cards(p) == 0


# ---------------------------------------------------------------------------
# Accessibility / layout.
# ---------------------------------------------------------------------------
def test_keyboard_focus_and_controls(page):
    p, server = page
    # The editor controls are keyboard-focusable and show a focus outline.
    p.click("#add-row")
    p.wait_for_timeout(30)
    p.focus(".ingredient-row .f-ingredient")
    assert p.evaluate("document.activeElement.className") == "f-ingredient"
    outline = p.eval_on_selector(".f-ingredient:focus", "el=>getComputedStyle(el).outlineStyle")
    assert outline != "none", outline
    # Remove via keyboard (Enter on focused button).
    _row(p, 1).locator(".remove-row").focus()
    p.keyboard.press("Enter")
    p.wait_for_timeout(30)
    assert _count_cards(p) == 0


def test_narrow_screen_no_horizontal_overflow(page):
    p, server = page
    p.set_viewport_size({"width": 390, "height": 900})
    p.reload()
    p.wait_for_selector("#snap-version")
    scroll_w = p.evaluate("document.documentElement.scrollWidth")
    inner_w = p.evaluate("window.innerWidth")
    assert scroll_w <= inner_w + 1, (scroll_w, inner_w)


def test_status_announcement_on_submit(page):
    p, server = page
    _install_controlled(p, server)
    _fill(p, 0, ingredient="Toluene", percent="20", unit="%")
    p.evaluate("window.dispatchSubmit()")
    p.wait_for_timeout(20)
    assert "Evaluating" in _message(p)
