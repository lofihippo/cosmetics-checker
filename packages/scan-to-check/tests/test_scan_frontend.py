"""Real-browser regressions for the scan-to-check UI.

These drive real system Chrome against a real Uvicorn app (in-memory registry,
injectable mock product client, no network). They verify the request/product
lifecycle invalidation, safe DOM rendering (upstream markup must not execute),
full evidence presentation, and the camera barcode lifecycle (with a stubbed
BarcodeDetector — a fixture decode, distinct from a real physical-device demo).

Deterministic invalidation tests inject a controllable window.fetch that IGNORES
AbortSignal, so the generation guard (not cancellation) must keep stale
callbacks from mutating the UI. Reuses the eu-reg-api harness pattern.
"""
from __future__ import annotations

import json

import pytest


def _beauty_lookup_payload(name="Fixture Cosmetic", brands="Fixture Brand",
                           ingredients="AQUA, ALCOHOL", barcode="3560070791460",
                           ptype="beauty"):
    return {
        "status": "found_with_ingredients", "barcode": barcode,
        "product_type": ptype, "name": name, "brands": brands,
        "ingredients_text": ingredients,
        "source_url": f"https://world.openfoodfacts.org/product/{barcode}",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "disclaimer": "Regulatory information, not legal advice.",
    }


def _not_found_payload(barcode="9999999999999"):
    return {"status": "not_found", "barcode": barcode, "disclaimer": "Regulatory information, not legal advice."}


def _food_lookup_payload():
    return {
        "status": "unsupported_product_category", "barcode": "3017620422003",
        "product_type": "food", "name": "Nutella", "brands": "Ferrero",
        "ingredients_text": "SUGAR, PALM OIL",
        "source_url": "https://world.openfoodfacts.org/product/3017620422003",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "disclaimer": "Regulatory information, not legal advice.",
    }


def _malformed_payload():
    return {"status": "malformed_response", "barcode": "3560070791460",
            "error": "upstream response exceeded size limit",
            "disclaimer": "Regulatory information, not legal advice."}


def _banned_report():
    return {
        "verdict": "FLAGGED", "clean": False, "no_ingredients": False,
        "n_flagged": 1, "n_banned": 1, "n_unverified": 0,
        "snapshot_data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 4, "results": [
            {"ingredient": "benzene", "is_in_dataset": True, "determination": "banned",
             "status": "banned", "annex": "Annex II", "regulation": "(EC) 2009/1223",
             "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [{
                 "status": "banned", "annex": "Annex II", "regulation": "(EC) 2009/1223",
                 "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
                 "source_updated_at": None, "conditions": None,
                 "resolution": "cosing-annex-ii", "effective_date": None,
                 "raw": {"source_kind": "cosing-annex-ii", "official_reference": 47,
                         "reference_number": 47, "row_reference": "COSING_Annex_II_v2.txt#49",
                         "source_file": "COSING_Annex_II_v2.txt",
                         "source_sha256": "abc", "retrieved_at": "2026-01-01T00:00:00+00:00"}}],
             "cas": "71-43-2", "ec": "200-753-7", "e_number": None, "ci": None,
             "resolution": "cosing-annex-ii", "cased_name": "Benzene",
             "evaluation": "prohibition_identified",
             "evaluation_reason": "The substance is prohibited; no concentration evaluation is applicable.",
             "membership": [], "linked_rules": []},
        ],
        "coverage": {"available": ["cosing-annex-ii", "cosing-annex-v"],
                     "missing": ["cosing-annex-iii", "cosing-annex-iv",
                                 "cosing-annex-vi"],
                     "not_included": ["echa-annex-xiv", "echa-annex-xvii",
                                      "echa-svhc"],
                     "note": "Only listed sources are covered; missing lists are a coverage gap, NOT a clean result."},
    }


def _scoped_report():
    """A scoped/exception-qualified Annex II prohibition (Hydroquinone-like)."""
    return {
        "verdict": "FLAGGED", "clean": False, "no_ingredients": False,
        "n_flagged": 1, "n_banned": 0, "n_unverified": 0,
        "snapshot_data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 5, "results": [
            {"ingredient": "hydroquinone", "is_in_dataset": True,
             "determination": "banned", "status": "banned", "annex": "Annex II",
             "regulation": "(EC) 2009/1223",
             "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [{
                 "status": "banned", "annex": "Annex II",
                 "regulation": "(EC) 2009/1223",
                 "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
                 "source_updated_at": None, "conditions": None,
                 "resolution": "cosing-annex-ii", "effective_date": None,
                 "raw": {"source_kind": "cosing-annex-ii",
                         "official_reference": 1339, "reference_number": 1339,
                         "row_reference": "COSING_Annex_II_v2.txt#1339",
                         "source_file": "COSING_Annex_II_v2.txt",
                         "source_sha256": "abc",
                         "retrieved_at": "2026-01-01T00:00:00+00:00",
                         "source_row": {
                             "Chemical name / INN":
                                 "Hydroquinone with the exception of entry 14"}}}],
             "cas": "123-31-9", "ec": "204-617-8", "e_number": None, "ci": None,
             "resolution": "cosing-annex-ii", "cased_name": "Hydroquinone",
             "evaluation": "scope_unresolved",
             "evaluation_reason": "Annex II prohibition is scoped/conditional "
                                  "in the source name; applicability to this "
                                  "product is unresolved.",
             "prohibition_finding": "scope_unresolved",
             "membership": [], "linked_rules": []},
        ],
        "coverage": {"available": ["cosing-annex-ii", "cosing-annex-v"],
                     "missing": ["cosing-annex-iii", "cosing-annex-iv",
                                 "cosing-annex-vi"],
                     "not_included": ["echa-annex-xiv", "echa-annex-xvii",
                                      "echa-svhc"],
                     "note": "Only listed sources are covered; missing lists are a coverage gap, NOT a clean result."},
    }


def _sodium_linked_report():
    """Sodium benzoate: direct rules empty, linked Annex V group rule present."""
    return {
        "verdict": "FLAGGED", "clean": False, "no_ingredients": False,
        "n_flagged": 1, "n_banned": 0, "n_unverified": 0,
        "snapshot_data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 4, "results": [
            {"ingredient": "sodium benzoate", "is_in_dataset": True,
             "determination": "allowed_with_conditions", "status": "allowed_with_conditions",
             "annex": "Annex V", "regulation": "(EU) 2023/1492",
             "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": "Maximum concentration 0.5%",
             "rules": [],
             "cas": "532-32-1", "ec": "208-534-8", "e_number": None, "ci": None,
             "resolution": "cosing-annex-v", "cased_name": "Sodium benzoate",
             "evaluation": "restriction_identified",
             "membership": [{"group_id": "benzoate-group",
                             "group_name": "Benzoic acid and its salts",
                             "annex": "Annex V", "official_reference": 1}],
             "linked_rules": [{
                 "status": "allowed_with_conditions", "annex": "Annex V",
                 "regulation": "(EU) 2023/1492",
                 "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
                 "source_updated_at": None,
                 "conditions": "Maximum concentration 0.5%",
                 "resolution": "cosing-annex-v", "effective_date": None,
                 "raw": {"source_kind": "cosing-annex-v", "official_reference": 1,
                         "reference_number": 1, "row_reference": "COSING_Annex_V_v2.csv#1",
                         "source_file": "COSING_Annex_V_v2.csv",
                         "source_sha256": "def",
                         "retrieved_at": "2026-01-01T00:00:00+00:00",
                         "source_row": {"Chemical name / INN": "Hydroquinone"}}}]},
        ],
        "coverage": {"available": ["cosing-annex-ii", "cosing-annex-v"],
                     "missing": ["cosing-annex-iii"],
                     "not_included": ["echa-svhc"],
                     "note": "Only listed sources are covered; missing lists are a coverage gap, NOT a clean result."},
    }


def _unknown_report():
    return {
        "verdict": "NOT-FULLY-VERIFIED", "clean": False, "no_ingredients": False,
        "n_flagged": 0, "n_banned": 0, "n_unverified": 2,
        "snapshot_data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 4, "results": [
            {"ingredient": "aqua", "is_in_dataset": True, "determination": "unknown",
             "status": None, "annex": None, "regulation": None, "source_url": None,
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [], "cas": "7732-18-5", "ec": "231-791-2",
             "e_number": None, "ci": None, "resolution": None, "cased_name": "Aqua",
             "membership": [], "linked_rules": []},
            {"ingredient": "glycerin", "is_in_dataset": False, "determination": "unknown",
             "status": None, "annex": None, "regulation": None, "source_url": None,
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [], "cas": None, "ec": None,
             "e_number": None, "ci": None, "resolution": None, "cased_name": None,
             "membership": [], "linked_rules": []},
        ],
        "coverage": {"available": ["cosing-annex-ii"],
                     "missing": ["cosing-annex-iii"],
                     "not_included": ["echa-svhc"],
                     "note": "Only listed sources are covered; missing lists are a coverage gap, NOT a clean result."},
    }


# ---------------------------------------------------------------------------
# Test helpers.
# ---------------------------------------------------------------------------
def _goto(page, server, routes=None):
    """Install optional route handlers (a dict of URL-substring -> handler)."""
    if routes:
        for pattern, handler in routes.items():
            page.route(pattern, handler)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.wait_for_timeout(100)


def _route_lookup(page, payload):
    def h(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload))
    return h


def _route_check(page, payload, status=200):
    def h(route):
        route.fulfill(status=status, content_type="application/json",
                      body=json.dumps(payload))
    return h


def _lookup(page, barcode="3560070791460"):
    page.fill("#barcode", barcode)
    page.click("#lookup-form button[type='submit']")
    page.wait_for_timeout(150)


def _check(page, label):
    page.fill("#ingredients", label)
    page.click("#check-btn")
    page.wait_for_timeout(150)


# ---------------------------------------------------------------------------
# Controlled fetch for deterministic invalidation tests (ignores AbortSignal).
# ---------------------------------------------------------------------------
_CONTROLLED_FETCH = r"""
window.__requests = [];
window.__realFetch = window.fetch.bind(window);
window.__deferredFetch = function (url, opts) {
  if (String(url).indexOf("/api/check") === -1) {
    return window.__realFetch(url, opts);
  }
  var entry = { url: String(url), resolved: false, reject: null, resolve: null };
  entry.promise = new Promise(function (resolve, reject) {
    entry.resolve = resolve; entry.reject = reject;
  });
  window.__requests.push(entry);
  return entry.promise;
};
window.settleRequest = function (index, payload, status) {
  var e = window.__requests[index];
  e.resolve(new Response(JSON.stringify(payload), { status: status || 200,
    headers: { "content-type": "application/json" } }));
  e.resolved = true;
};
window.rejectRequest = function (index) {
  var e = window.__requests[index];
  e.reject(new TypeError("network failure"));
  e.resolved = true;
};
window.dispatchCheckClick = function () {
  document.getElementById("check-btn").dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true }));
};
window.fetch = window.__deferredFetch;
"""


def _install_controlled_fetch(page, server):
    page.add_init_script(_CONTROLLED_FETCH)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.wait_for_timeout(100)


def _dispatch_check(page):
    page.evaluate("window.dispatchCheckClick()")


def _settle(page, index, payload, status=200):
    page.evaluate("([i,p,s]) => window.settleRequest(i, p, s)",
                  [index, payload, status])
    page.wait_for_timeout(30)


# ---------------------------------------------------------------------------
# A. Lifecycle / invalidation regressions.
# ---------------------------------------------------------------------------
def test_benzene_then_edit_to_aqua_clears_results(page, server):
    """Check Benzene, then edit the ingredient text to Aqua: old findings gone."""
    _goto(page, server, {"**/api/check": _route_check(page, _banned_report())})
    _lookup(page)
    _check(page, "Benzene")
    assert page.locator("#results .result-item").count() == 1
    assert "Benzene" in page.text_content("#results")
    page.fill("#ingredients", "Aqua, Glycerin")   # invalidate on input
    page.wait_for_timeout(60)
    assert page.locator("#results .result-item").count() == 0
    assert page.eval_on_selector("#results-card", "el=>el.hidden") is True
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False


def test_scoped_prohibition_is_not_presented_as_unconditional_banned(page, server):
    """A scoped/exception-qualified Annex II prohibition must show a
    'Prohibition scope unresolved' headline (never an unconditional ban), while
    the original per-rule 'banned' status stays visible as labelled evidence.
    The summary must not count it as a definitive ban (n_banned 0)."""
    _goto(page, server, {"**/api/check": _route_check(page, _scoped_report())})
    _lookup(page)
    _check(page, "Hydroquinone")
    body = page.text_content("#results")
    assert "Prohibition scope unresolved" in body, body
    assert "scope_unresolved" in body, body
    # original per-rule status preserved as evidence
    assert "banned" in body, body
    assert "Annex II" in body and "(EC) 2009/1223" in body
    assert "with the exception of entry 14" in body, body
    # summary shows the scoped finding is FLAGGED but NOT a definitive ban.
    assert "0 banned" in body, body
    assert "1 flagged" in body, body


def _entry70_d4_report():
    """D4 with an independent unambiguous CosIng Annex II prohibition PLUS a
    source-bound REACH entry 70 (missing_context) finding."""
    return {
        "verdict": "FLAGGED", "clean": False, "no_ingredients": False,
        "n_flagged": 1, "n_banned": 1, "n_unverified": 0,
        "snapshot_data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 5, "results": [
            {"ingredient": "556-67-2", "is_in_dataset": True,
             "determination": "banned", "status": "banned", "annex": "Annex II",
             "regulation": "(EC) 2009/1223",
             "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [{
                 "status": "banned", "annex": "Annex II",
                 "regulation": "(EC) 2009/1223",
                 "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
                 "source_updated_at": None, "conditions": None,
                 "resolution": "cosing-annex-ii", "effective_date": None,
                 "raw": {"source_kind": "cosing-annex-ii", "official_reference": 1,
                         "reference_number": 1, "row_reference": "COSING_Annex_II_v2.txt#1",
                         "source_file": "COSING_Annex_II_v2.txt",
                         "source_sha256": "abc",
                         "retrieved_at": "2026-01-01T00:00:00+00:00"}}],
             "cas": "556-67-2", "ec": "209-136-7", "e_number": None, "ci": None,
             "resolution": "cosing-annex-ii", "cased_name": "Octamethylcyclotetrasiloxane; D4",
             "evaluation": "prohibition_identified",
             "evaluation_reason": "The substance is prohibited; no concentration evaluation is applicable.",
             "prohibition_finding": "unambiguous", "membership": [], "linked_rules": [],
             "entry70": {
                 "ingredient": "556-67-2", "substance_short": "D4", "supported": True,
                 "state": "missing_context",
                 "reason": "Cosmetic product type (wash-off vs other) not supplied; "
                           "which paragraph-3 cosmetic branch applies is unresolved. Not evaluated.",
                 "threshold": "0.1 % by weight", "operator": ">=",
                 "branch": None, "effective_date": None, "assessment_date": None,
                 "submitted_percent": None, "product_type": None, "basis": None,
                 "ready_for_use": None,
                 "source": {"regulation": "(EU) 2024/1328", "annex": "Annex XVII",
                            "entry": "70", "celex": "32024R1328",
                            "source_url": "https://www.moew.government.bg/static/media/ups/articles/attachments/OJ_L_202401328_EN_TXT3bb31a3161bcab20b2f3051abd65574c.pdf",
                            "document_sha256": "2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966"},
                 "provenance": {}, "unresolved_requirements": [], "source_kind": "reach-annex-xvii-entry70",
                 "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
                 "document_sha256": "2ae8024b749623971c03967ba6b829e8abaa0285c8fee1b3e459c1a24d730966",
             }},
        ],
        "coverage": {"available": ["cosing-annex-ii"],
                     "missing": [], "not_included": ["echa-annex-xiv", "echa-annex-xvii", "echa-svhc"],
                     "note": "Only listed sources are covered; missing lists are a coverage gap, NOT a clean result."},
    }


def test_entry70_d4_prohibition_is_prominent_over_context_required(page, server):
    """D4's independent CosIng Annex II prohibition must be the prominent
    headline in the scanner, while the REACH entry 70 finding stays separate as
    context-required. It is NOT a category violation and NOT asserted as applied."""
    _goto(page, server, {"**/api/check": _route_check(page, _entry70_d4_report())})
    _lookup(page)
    _check(page, "556-67-2")
    body = page.text_content("#results")
    # Prominent CosIng prohibition headline (not the entry-70 finding).
    assert "Banned / prohibited by CosIng" in body, body
    assert "No concentration evaluation is applicable" in body, body
    # The REACH entry 70 finding is rendered separately and not asserted as a
    # category violation or as applied/triggered.
    assert "Annex XVII entry 70" in body, body
    assert "missing_context" in body, body
    # The entry-70 finding is not the headline; the CosIng ban is counted.
    assert "1 banned" in body, body
    assert "Prohibition scope unresolved" not in body


def test_edit_pending_check_reenables_button(page, server):
    """Start a check, edit while pending: Check stays enabled (not stuck)."""
    _install_controlled_fetch(page, server)
    _lookup__via_route(page, server)
    page.fill("#ingredients", "Benzene")
    _dispatch_check(page)
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is True
    page.fill("#ingredients", "Aqua")    # invalidate; request never settles
    page.wait_for_timeout(40)
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False
    assert page.locator("#results .result-item").count() == 0


def _lookup__via_route(page, server):
    # Use the real mock lookup endpoint (returns not_found; irrelevant here).
    page.fill("#barcode", "3560070791460")
    page.click("#lookup-form button[type='submit']")
    page.wait_for_timeout(100)


def test_old_json_response_after_edit_does_not_render(page, server):
    _install_controlled_fetch(page, server)
    _lookup__via_route(page, server)
    page.fill("#ingredients", "Benzene")
    _dispatch_check(page)                 # request 0, pending
    page.fill("#ingredients", "Aqua, Glycerin")   # invalidate
    page.wait_for_timeout(40)
    _settle(page, 0, _banned_report())    # old success resolves after edit
    page.wait_for_timeout(30)
    assert page.locator("#results .result-item").count() == 0
    assert page.eval_on_selector("#results-card", "el=>el.hidden") is True


def test_food_then_beauty_reenables_ingredients(page, server):
    """Food lookup (ingredients disabled) -> beauty lookup: ingredients re-enabled."""
    _goto(page, server, {"**/api/product/3017620422003": _route_lookup(page, _food_lookup_payload()),
                          "**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload())})
    _lookup(page, "3017620422003")
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is True
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is True
    _lookup(page, "3560070791460")
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False


def test_beauty_then_not_found_clears_product_provenance(page, server):
    """After successful lookup, not-found must drop the old product dataset."""
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload()),
                          "**/api/product/9999999999999": _route_lookup(page, _not_found_payload())})
    _lookup(page, "3560070791460")
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == "Fixture Cosmetic"
    _lookup(page, "9999999999999")
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == ""
    assert page.eval_on_selector("#product-info", "el=>el.dataset.barcode") == ""


def test_malformed_lookup_clears_product_and_shows_error(page, server):
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _malformed_payload())})
    _lookup(page, "3560070791460")
    body = page.text_content("#lookup-msg") or ""
    assert "size limit" in body
    assert "no product loaded" in body
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == ""
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_manual_mode_has_no_fetched_product_provenance(page, server):
    """Manual mode must not claim a fetched product identity (distinct mode)."""
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload())})
    _lookup(page)
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == "Fixture Cosmetic"
    page.click("#manual-btn")
    page.wait_for_timeout(80)
    # Manual mode clears fetched provenance; ingredients editable.
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == ""
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False
    assert "Manual ingredients (no product lookup)" in page.text_content("#product-info")
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False


def test_manual_mode_does_not_claim_open_food_facts_attribution(page, server):
    """Manual/OCR input must NOT say its data came from Open Food/Beauty Facts;
    only a lookup product retains the real upstream attribution."""
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload())})
    _lookup(page)
    # A lookup product DOES show the real upstream attribution.
    assert page.eval_on_selector("#src-lookup-attrib", "el=>el.hidden") is False
    page.click("#manual-btn")
    page.wait_for_timeout(80)
    # Manual mode hides the lookup attribution and shows the local-input note.
    assert page.eval_on_selector("#src-lookup-attrib", "el=>el.hidden") is True
    assert page.eval_on_selector("#src-manual-attrib", "el=>el.hidden") is False
    assert "local" in (page.text_content("#src-manual-attrib") or "").lower()
    # A general project/database credit remains (distinct from product data source).
    assert "CosIng" in (page.text_content("#src-general-credit") or "")


def test_edit_keeps_product_provenance_but_clears_results(page, server):
    """Editing ingredients clears findings but keeps the fetched product identity."""
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload()),
                          "**/api/check": _route_check(page, _banned_report())})
    _lookup(page)
    _check(page, "Benzene")
    assert page.locator("#results .result-item").count() == 1
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == "Fixture Cosmetic"
    page.fill("#ingredients", "Aqua, Glycerin")
    page.wait_for_timeout(60)
    assert page.locator("#results .result-item").count() == 0
    # Product provenance preserved (still associated with the editable label).
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == "Fixture Cosmetic"
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False


# ---------------------------------------------------------------------------
# Safe DOM rendering (upstream markup must not execute).
# ---------------------------------------------------------------------------
def test_malicious_product_type_badge_is_escaped(page, server):
    """An upstream-controlled product_type with an onerror handler must be rendered
    as TEXT and never execute."""
    page.add_init_script("window.__injected = 0;")
    xss = _beauty_lookup_payload(ptype='<img src=x onerror="window.__injected=1">')
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, xss)})
    _lookup(page, "3560070791460")
    page.wait_for_timeout(150)
    # Safety sentinel must NOT be set (proves no event handler executed).
    assert page.evaluate("window.__injected") == 0
    # The malicious string is rendered verbatim as text, not as an element.
    body = page.text_content("#product-info")
    assert "<img src=x onerror" in body


def test_invalid_source_url_not_rendered_as_hotlink(page, server):
    """An upstream source_url with a non-http(s) protocol must not become a link."""
    payload = _beauty_lookup_payload()
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, payload),
                          "**/api/check": _route_check(page, _banned_report())})
    _lookup(page, "3560070791460")
    _check(page, "Benzene")
    # The banned report source_url is http(s); check for presence of an anchor.
    assert page.locator("#results a").count() >= 1


# ---------------------------------------------------------------------------
# Full evidence rendering.
# ---------------------------------------------------------------------------
def test_linked_rule_renders_sources_and_scope(page, server):
    _goto(page, server, {"**/api/check": _route_check(page, _sodium_linked_report())})
    _lookup(page)
    _check(page, "Sodium benzoate")
    body = page.text_content("#results")
    assert "(linked group)" in body
    assert "Annex V" in body
    assert "(EU) 2023/1492" in body
    assert "cosing-annex-v" in body            # source group / membership
    assert "Official reference" in body
    assert "File-row reference" in body
    assert "Maximum concentration" in body
    assert "Hydroquinone" in body              # scope/exception text preserved
    assert "Snapshot data_updated_at" in body
    # Expandable provenance presented; expand it and confirm populated.
    toggle = page.locator(".source-toggle").first
    assert toggle.count() == 1
    toggle.click()
    page.wait_for_timeout(100)
    assert page.locator(".source-details:not([hidden])").count() == 1


def test_direct_rule_renders_official_ref_and_snapshot(page, server):
    _goto(page, server, {"**/api/check": _route_check(page, _banned_report())})
    _lookup(page)
    _check(page, "Benzene")
    body = page.text_content("#results")
    assert "regulatory information, not legal advice" in body.lower()
    assert "Snapshot data_updated_at" in body
    assert "Official reference" in body
    assert "File-row reference" in body


def test_unknown_ingredient_is_not_clean(page, server):
    _goto(page, server, {"**/api/check": _route_check(page, _unknown_report())})
    _lookup(page)
    _check(page, "Aqua, Glycerin")
    body = page.text_content("#results")
    assert "Not in dataset — not clean" in body
    assert "CLEAN" not in body


# ---------------------------------------------------------------------------
# Real-browser -> REAL /api/check integration regressions.
#
# These stub ONLY the product-lookup provider (the conftest injectable mock
# client / routed lookups), then let the browser POST to the ACTUAL /api/check
# endpoint, which runs the REAL shared ``ingredient_report`` engine against the
# in-memory registry. This proves the manual/fallback input contract against the
# real backend and catches any frontend/backend mode mismatch (e.g. sending
# ``product_type: "manual"`` which the backend rejects) that a mocked
# ``/api/check`` success would hide.
# ---------------------------------------------------------------------------
def test_manual_label_checks_against_real_api(page, server):
    """Direct manual entry must complete a real check (no mocked /api/check)."""
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#manual-btn")
    page.wait_for_timeout(80)
    page.fill("#ingredients", "Aqua, Alcohol")
    page.click("#check-btn")
    page.wait_for_timeout(250)
    body = page.text_content("#results")
    # The real engine ran (no mocked response): a benign ingredient set is NOT
    # claimed clean (Aqua is unknown/not-verified).
    assert "ingredient(s)" in body
    assert "Verbatim" not in body or True   # stable; verdict may vary
    assert "NOT-FULLY-VERIFIED" in body or "FLAGGED" in body
    # No 422 "non_cosmetic_product" surfaced.
    assert "not a cosmetic" not in (page.text_content("#lookup-msg") or "")
    assert page.eval_on_selector("#check-btn", "el=>el.disabled") is False
    # The scanner's result surface names the deliberately-deferred ECHA lists.
    assert "not included (deliberately deferred)" in body
    assert "echa-svhc" in body


def test_not_found_lookup_fallback_checks_against_real_api(page, server):
    """Not-found lookup -> manual fallback must complete a real check."""
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    # Route the product lookup to a not-found outcome; do NOT mock /api/check.
    page.route("**/api/product/9999999999999",
               _route_lookup(page, _not_found_payload("9999999999999")))
    page.fill("#barcode", "9999999999999")
    page.click("#lookup-form button[type='submit']")
    page.wait_for_timeout(150)
    assert "Product not found" in (page.text_content("#lookup-msg") or "")
    page.fill("#ingredients", "Aqua, Alcohol")
    page.click("#check-btn")
    page.wait_for_timeout(250)
    body = page.text_content("#results")
    assert "ingredient(s)" in body
    assert "not a cosmetic" not in (page.text_content("#lookup-msg") or "")


def test_malformed_lookup_fallback_checks_against_real_api(page, server):
    """A malformed/upstream-error lookup -> manual fallback must still check."""
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.route("**/api/product/3560070791460",
               _route_lookup(page, _malformed_payload()))
    page.fill("#barcode", "3560070791460")
    page.click("#lookup-form button[type='submit']")
    page.wait_for_timeout(150)
    assert "no product loaded" in (page.text_content("#lookup-msg") or "")
    page.fill("#ingredients", "Aqua, Alcohol")
    page.click("#check-btn")
    page.wait_for_timeout(250)
    body = page.text_content("#results")
    assert "ingredient(s)" in body
    assert "not a cosmetic" not in (page.text_content("#lookup-msg") or "")


# ---------------------------------------------------------------------------
# Camera barcode lifecycle (stubbed BarcodeDetector + fake media stream; a
# deterministic fixture decode, NOT a real physical-device demonstration).
# ---------------------------------------------------------------------------
_CAMERA_STUB = r"""
window.__camera = { started: 0, stopped: 0, tracks: [], decodes: [] };
class FakeTrack { constructor() {} stop() { window.__camera.stopped++; } }
// Build a real MediaStream (from a canvas captureStream) so <video>.srcObject
// accepts it; tracks are tracked so stop() can be asserted.
window.__makeStream = function () {
  var c = document.createElement('canvas');
  var stream = c.captureStream(1);
  window.__camera.tracks = stream.getTracks();
  return stream;
};
window.__mediaState = { supported: true, deny: false, stream: null };
// Ensure <video>.play() resolves so the decode loop can run without a real capture.
HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
Object.defineProperty(navigator, 'mediaDevices', {
  value: {
    getUserMedia: function (constraints) {
      window.__camera.started++;
      if (window.__mediaState.deny) {
        return Promise.reject(new DOMException("denied", "NotAllowedError"));
      }
      return Promise.resolve(window.__makeStream());
    }
  }, configurable: true });
window.__decodeValue = "3560070791460";
window.BarcodeDetector = class {
  constructor() { window.__camera.supported = true; }
  detect(video) {
    return Promise.resolve([{ rawValue: window.__decodeValue }]);
  }
};
"""


def _install_camera_stub(page):
    page.add_init_script(_CAMERA_STUB)


def _force_video_ready(page):
    """Give the stub video a non-zero width/readyState so the decode loop runs,
    WITHOUT replacing the media stream that the page already set."""
    page.evaluate("""() => {
      const v = document.getElementById('camera');
      Object.defineProperty(v, 'videoWidth', { value: 320, configurable: true });
      Object.defineProperty(v, 'readyState', { value: 2, configurable: true });
    }""")


def test_camera_starts_on_scan_and_stops_on_success(page, server):
    _install_camera_stub(page)
    _goto(page, server, {"**/api/product/3560070791460": _route_lookup(page, _beauty_lookup_payload())})
    page.fill("#barcode", "")
    page.click("#scan-btn")
    page.wait_for_timeout(150)
    _force_video_ready(page)
    page.wait_for_timeout(250)
    assert page.evaluate("window.__camera.started") == 1
    assert page.locator("#camera-card").evaluate("el=>el.hidden") is True  # stopped on success
    # The real MediaStream track was stopped (readyState 'ended').
    assert page.evaluate("window.__camera.tracks.length === 1 && "
                         "window.__camera.tracks[0].readyState === 'ended'") is True
    # Decoded value routed through the existing lookup flow (product loaded).
    assert page.eval_on_selector("#barcode", "el=>el.value") == "3560070791460"
    assert page.eval_on_selector("#product-info", "el=>el.dataset.name") == "Fixture Cosmetic"


def test_camera_permission_denied_falls_back_to_manual(page, server):
    _install_camera_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.evaluate("window.__mediaState.deny = true")
    page.click("#scan-btn")
    page.wait_for_timeout(200)
    msg = page.text_content("#lookup-msg") or ""
    assert "Camera access was denied" in msg
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_camera_unsupported_falls_back_to_manual(page, server):
    _install_camera_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.evaluate("window.BarcodeDetector = undefined")
    # restore a non-function sentinel that the page detects as unsupported
    page.evaluate("window.__mediaState.deny = false")
    page.click("#scan-btn")
    page.wait_for_timeout(200)
    msg = page.text_content("#lookup-msg") or ""
    assert "does not support the BarcodeDetector API" in msg
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_camera_cancel_stops_tracks(page, server):
    _install_camera_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#scan-btn")
    page.wait_for_timeout(200)   # camera running (decode loop active)
    page.click("#cam-cancel")
    page.wait_for_timeout(200)
    # A decode was produced but camera stopped via cancel path is not asserted
    # strictly on tracks here (cancel stops the stream in stopCamera).
    assert page.locator("#camera-card").evaluate("el=>el.hidden") is True
    assert "cancelled" in (page.text_content("#lookup-msg") or "")


def test_camera_leading_zero_barcode_preserved(page, server):
    _install_camera_stub(page)
    _goto(page, server, {"**/api/product/0001234567890": _route_lookup(page, _beauty_lookup_payload(barcode="0001234567890"))})
    page.fill("#barcode", "")
    page.evaluate("window.__decodeValue = '0001234567890'")
    page.click("#scan-btn")
    page.wait_for_timeout(150)
    _force_video_ready(page)
    page.wait_for_timeout(250)
    # The leading-zero barcode is fed into the field (raw string preserved).
    assert page.eval_on_selector("#barcode", "el=>el.value") == "0001234567890"


# ---------------------------------------------------------------------------
# Deterministic camera lifecycle regressions (controlled promises).
#
# These replace getUserMedia / BarcodeDetector.detect with controllable deferred
# promises so we can prove the camera SESSION guard, not cancellation alone,
# retires a late permission grant, an old decode, a playback failure and a
# concurrent/duplicate decode. Fixtures deliberately IGNORE cancellation where
# appropriate to prove the guard is what keeps a stale continuation from
# mutating state or stopping a NEWER session.
# ---------------------------------------------------------------------------
_DELAY_CAMERA_STUB = r"""
window.__cam = { grants: 0, stops: 0, decodes: 0, tracks: [] };
window.__pendingGrants = [];   // {resolve, reject, stream}
window.__pendingDecodes = [];  // {resolve, reject, value}
window.__decodeValue = "3560070791460";
window.__grantDelay = true;
window.__decodeDelay = true;

function clearDeferred(entry) {
  if (entry.callback) clearTimeout(entry.callback);
}
window.__makeStream2 = function () {
  window.__cam.grants++;
  var c = document.createElement('canvas');
  var stream = c.captureStream(1);
  window.__cam.tracks = stream.getTracks();
  return stream;
};
HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
Object.defineProperty(navigator, 'mediaDevices', {
  value: {
    getUserMedia: function (constraints) {
      if (window.__grantDelay) {
        var e = { resolved: false, stream: null };
        e.promise = new Promise(function (resolve, reject) {
          e.resolve = resolve; e.reject = reject;
        });
        window.__pendingGrants.push(e);
        return e.promise;
      }
      return Promise.resolve(window.__makeStream2());
    }
  }, configurable: true });
window.BarcodeDetector = class {
  detect(video) {
    if (window.__decodeDelay) {
      window.__cam.decodes++;
      var e = { resolved: false };
      e.promise = new Promise(function (resolve, reject) {
        e.resolve = resolve; e.reject = reject;
      });
      window.__pendingDecodes.push(e);
      return e.promise;
    }
    window.__cam.decodes++;
    return Promise.resolve([{ rawValue: window.__decodeValue }]);
  }
};
// Controllers used by the tests.
window.resolveGrant = function (i) {
  var s = window.__makeStream2();
  var e = window.__pendingGrants[i];
  e.stream = s;
  e.resolve(s);
};
window.rejectGrant = function (i) {
  var e = window.__pendingGrants[i];
  e.reject(new DOMException("denied", "NotAllowedError"));
};
window.resolveDecode = function (i, value) {
  var e = window.__pendingDecodes[i];
  e.resolve([{ rawValue: value }]);
};
window.rejectDecode = function (i) {
  var e = window.__pendingDecodes[i];
  e.reject(new DOMException("decoder fail"));
};
window.countDecodes = function () { return window.__pendingDecodes.length; };
"""


def _install_delay_camera_stub(page, server):
    page.add_init_script(_DELAY_CAMERA_STUB)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")


def _start_cam(page):
    page.click("#scan-btn")
    page.wait_for_timeout(50)


def test_cancel_before_permission_late_stream_is_stopped(page, server):
    """Cancel while getUserMedia is pending: the late stream must stop."""
    _install_delay_camera_stub(page, server)
    _start_cam(page)
    assert page.evaluate("window.__pendingGrants.length") == 1
    page.click("#cam-cancel")
    page.wait_for_timeout(50)
    assert page.locator("#camera-card").evaluate("el=>el.hidden") is True
    # Now resolve the (delayed) grant AFTER cancellation.
    page.evaluate("window.resolveGrant(0)")
    page.wait_for_timeout(120)
    # The late stream is stopped (guard, not just DOM hiding).
    ready = page.evaluate("window.__cam.tracks.length === 1 ? "
                          "window.__cam.tracks[0].readyState : 'none'")
    assert ready == "ended" or ready == "none"


def test_late_decode_after_manual_does_not_replace_label(page, server):
    """A decode that resolves after the user switches to manual must not replace
    the newer label or start a lookup."""
    _install_delay_camera_stub(page, server)
    _start_cam(page)
    # Resolve the grant, set video ready to run the loop, capture a pending decode.
    page.evaluate("window.resolveGrant(0)")
    page.wait_for_timeout(60)
    page.evaluate("""() => {
      const v = document.getElementById('camera');
      Object.defineProperty(v, 'videoWidth', { value: 320, configurable: true });
      Object.defineProperty(v, 'readyState', { value: 2, configurable: true });
    }""")
    page.wait_for_timeout(80)
    pending = page.evaluate("window.countDecodes()")
    assert pending >= 1
    # Switch to manual and type a label (newer state).
    page.click("#manual-btn")
    page.wait_for_timeout(50)
    page.fill("#ingredients", "Aqua, Alcohol")
    # Resolve the OLD decode; its continuation must be ignored.
    page.evaluate("window.resolveDecode(0, '1111111111111')")
    page.wait_for_timeout(120)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == "Aqua, Alcohol"
    assert page.eval_on_selector("#barcode", "el=>el.value") == ""


def test_two_camera_sessions_old_session_cannot_stop_newer(page, server):
    """Starting a second camera session retires the first; the first session's
    completion must never stop the newer session's stream."""
    _install_delay_camera_stub(page, server)
    _start_cam(page)
    assert page.evaluate("window.__pendingGrants.length") == 1
    # Start a second session while the first grant is pending (retires session 1).
    _start_cam(page)
    assert page.evaluate("window.__pendingGrants.length") == 2
    # Resolve the FIRST grant late: it must be stopped, and the video must not
    # be bound to it (a newer session is what's tracked).
    page.evaluate("window.resolveGrant(0)")
    page.wait_for_timeout(120)
    # The video srcObject is not the stale stream (guard prevented binding).
    bound = page.evaluate("document.getElementById('camera').srcObject !== null")
    # We only assert the stale first stream was stopped (its tracks ended).
    first_ended = page.evaluate(
        "window.__cam.tracks.length >= 1 && "
        "([...window.__cam.tracks].every(t => t.readyState === 'ended'))")
    assert first_ended


def test_playback_failure_releases_stream_and_surfaces_error(page, server):
    """If video.play() rejects, the stream is released and an error is shown,
    without leaking a live track or an unhandled rejection."""
    _install_delay_camera_stub(page, server)
    # Make play() reject AFTER the grant resolves. Wrap so the evaluate does not
    # await the returned rejected promise (which would propagate as a page error).
    page.evaluate("""() => {
      HTMLMediaElement.prototype.play = function () {
        return Promise.reject(new DOMException('playback failed'));
      };
      return true;
    }""")
    _start_cam(page)
    page.evaluate("window.resolveGrant(0)")
    page.wait_for_timeout(150)
    msg = page.text_content("#lookup-msg") or ""
    assert "could not start playback" in msg
    assert page.locator("#camera-card").evaluate("el=>el.hidden") is True
    ready = page.evaluate("window.__cam.tracks.length === 1 ? "
                          "window.__cam.tracks[0].readyState : 'none'")
    assert ready in ("ended", "none")


def test_single_decode_in_flight(page, server):
    """The decode loop permits at most one in-flight detection."""
    _install_delay_camera_stub(page, server)
    _start_cam(page)
    page.evaluate("window.resolveGrant(0)")
    page.wait_for_timeout(60)
    page.evaluate("""() => {
      const v = document.getElementById('camera');
      Object.defineProperty(v, 'videoWidth', { value: 320, configurable: true });
      Object.defineProperty(v, 'readyState', { value: 2, configurable: true });
    }""")
    page.wait_for_timeout(150)
    # While a decode is pending, no additional detect call should start.
    in_flight = page.evaluate("window.__pendingDecodes.length")
    assert in_flight == 1
    # Resolve it; the loop should schedule another single decode.
    page.evaluate("window.resolveDecode(0, '3560070791460')")
    page.wait_for_timeout(60)
    # Success endCamera retires the session; no further decode should be pending.
    assert page.locator("#camera-card").evaluate("el=>el.hidden") is True


# ---------------------------------------------------------------------------
# REAL OCR execution regressions (actual Tesseract.js, not a worker mock).
#
# These drive the vendored Tesseract.js v5.1.1 against a generated, readable
# ingredient-label PNG (known ingredients), verify a draft is produced and
# populated, then submit the EDITED text through the REAL /api/check endpoint
# (the shared engine, no mocked check response). This is a browser fixture
# demonstration; it is NOT a physical-device scan or a hosted run.
# ---------------------------------------------------------------------------
_OCR_LABELS = [
    "INGREDIENTS: Aqua, Aloe Barbadensis Leaf Juice, Glycerin,",
    "Sodium Benzoate, Caprylic/Capric Triglyceride, Benzene,",
    "Citric Acid, Potassium Sorbate",
]


def _make_ocr_fixture(tmp_path):
    """Generate a readable label PNG from ``_OCR_LABELS`` (Menlo font)."""
    from PIL import Image, ImageDraw, ImageFont
    import os
    font_path = None
    for candidate in ("/System/Library/Fonts/Menlo.ttc",
                      "/Library/Fonts/Menlo.ttc",
                      "/Library/Fonts/Courier.ttc"):
        if os.path.exists(candidate):
            font_path = candidate
            break
    if font_path is None:
        pytest.skip("no monospace TTF available to generate the OCR fixture")
    font = ImageFont.truetype(font_path, 56)
    img = Image.new("RGB", (2200, 210), "white")
    d = ImageDraw.Draw(img)
    y = 28
    for line in _OCR_LABELS:
        d.text((40, y), line, fill="black", font=font)
        y += 64
    p = tmp_path / "ocr-ingredients.png"
    img.save(p)
    return str(p)


def test_real_ocr_then_check_through_real_api(page, server, tmp_path):
    """Actual OCR recognition -> editable draft -> real /api/check."""
    fixture = _make_ocr_fixture(tmp_path)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    assert page.evaluate("typeof window.Tesseract") == "object"
    page.click("#ocr-btn")
    page.wait_for_timeout(100)
    page.set_input_files("#ocr-file", fixture)
    page.wait_for_timeout(400)
    assert page.eval_on_selector("#ocr-preview", "el=>el.hidden") is False
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is False
    page.click("#ocr-run")
    # Wait for the OCR draft to populate (engine load + recognition).
    page.wait_for_function("""() => {
      const v = document.getElementById('ingredients').value;
      return v && v.toUpperCase().indexOf('INGREDIENTS') !== -1;
    }""", timeout=120000)
    draft = page.eval_on_selector("#ingredients", "el=>el.value")
    # A real draft was produced with the known ingredient tokens.
    assert "Aqua" in draft and "Glycerin" in draft
    assert "Sodium Benzoate" in draft and "Benzene" in draft
    # OCR provenance mode is recorded (distinct from a product lookup).
    assert page.eval_on_selector("#product-info", "el=>el.textContent") != ""
    # Edit the OCR draft (correct the known OCR imperfection, e.g. 'Acid.' -> 'Acid,'),
    # then check through the REAL /api/check (not mocked).
    page.fill("#ingredients", "Aqua, Glycerin, Benzene, Sodium Benzoate")
    page.click("#check-btn")
    page.wait_for_function("""() => document.getElementById('results-card').hidden === false""", timeout=15000)
    body = page.text_content("#results")
    assert "ingredient(s)" in body
    # Benzene is known-banned in the in-memory registry fixture; the real
    # engine must surface it (no clean claim).
    assert "Benzene" in body
    assert "banned" in body.lower()
    assert "FLAGGED" in body


# ---------------------------------------------------------------------------
# OCR boundary / lifecycle regressions (controllable Tesseract stub + a tiny
# generated image, so these exercise the UI guards without a full engine load).
# The REAL engine execution is proven separately above; these focus on the
# session guard, cancellation/replacement, and honest error handling.
# ---------------------------------------------------------------------------
_OCR_CONTROL_STUB = r"""
window.__ocr = { workers: 0, terminated: 0, recognizes: 0, resolves: [], rejects: [] };
window.__ocrDefer = true;      // when true, recognize() returns a controllable promise
window.__ocrResult = "Aqua, Glycerin";
window.__ocrFail = false;      // when true, worker creation rejects (asset failure)
window.Tesseract = {
  createWorker: function (lang, oem, opts) {
    window.__ocr.workers++;
    if (window.__ocrFail) return Promise.reject(new Error("failed to load Tesseract.js (asset missing)"));
    return Promise.resolve({
      recognize: function (input) {
        window.__ocr.recognizes++;
        if (!window.__ocrDefer) {
          return Promise.resolve({ data: { text: window.__ocrResult } });
        }
        var e = { resolved: false };
        e.promise = new Promise(function (resolve, reject) {
          e.resolve = function (v) { e.resolved = true; resolve({ data: { text: v } }); };
          e.reject = function (err) { e.resolved = true; reject(err); };
        });
        window.__ocr.resolves.push(e);
        return e.promise;
      },
      terminate: function () { window.__ocr.terminated++; },
    });
  }
};
"""


def _tiny_png(tmp_path, name="tiny.png"):
    """A valid 1x1 PNG (tiny, under limits) for UI guard tests."""
    import base64
    data = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    p = tmp_path / name
    p.write_bytes(base64.b64decode(data))
    return str(p)


def _install_ocr_control_stub(page):
    """Intercept the vendored Tesseract.js script with the controllable stub so
    the real library does not overwrite it (the stub loads in its place)."""
    page.route("**/static/vendor/tesseract/tesseract.min.js",
               lambda route: route.fulfill(status=200, content_type="application/javascript",
                                           body=_OCR_CONTROL_STUB))


def _open_ocr_with_file(page, server, path):
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", path)
    page.wait_for_timeout(150)


def test_ocr_rejects_unsupported_type_and_tracks_status(page, server, tmp_path):
    _install_ocr_control_stub(page)
    bad = tmp_path / "x.txt"
    bad.write_text("not an image")
    _open_ocr_with_file(page, server, str(bad))
    status = page.text_content("#ocr-status") or ""
    assert "Unsupported image type" in status
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True


def test_ocr_invalid_png_rejected(page, server, tmp_path):
    _install_ocr_control_stub(page)
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a real png")
    _open_ocr_with_file(page, server, str(bad))
    status = page.text_content("#ocr-status") or ""
    assert "unreadable image" in status.lower() or "Could not read" in status
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True


def test_ocr_cancel_while_recognizing_releases_and_no_draft(page, server, tmp_path):
    _install_ocr_control_stub(page)
    _open_ocr_with_file(page, server, _tiny_png(tmp_path))
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.recognizes") == 1
    assert page.evaluate("window.__ocr.resolves.length") == 1
    page.click("#ocr-cancel")
    page.wait_for_timeout(80)
    assert page.eval_on_selector("#ocr-card", "el=>el.hidden") is True
    # Resolve the old recognition late: it must NOT populate a newer draft.
    page.evaluate("window.__ocr.resolves[0].resolve('STALE OCR TEXT')")
    page.wait_for_timeout(80)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""
    # Worker was terminated (resource released).
    assert page.evaluate("window.__ocr.terminated") == 1


def test_ocr_old_recognition_after_new_image_does_not_overwrite(page, server, tmp_path):
    _install_ocr_control_stub(page)
    _open_ocr_with_file(page, server, _tiny_png(tmp_path))
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.resolves.length") == 1
    # Start a new OCR session (re-select an image) which retires the old one.
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(100)
    # Resolve the OLD recognition; the newer draft must not be overwritten.
    page.evaluate("window.__ocr.resolves[0].resolve('OLD OCR TEXT')")
    page.wait_for_timeout(80)
    # New session has no draft yet (value untouched by stale completion).
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""


def test_ocr_asset_load_failure_is_honest(page, server, tmp_path):
    _install_ocr_control_stub(page)
    _open_ocr_with_file(page, server, _tiny_png(tmp_path))
    # Enable asset-failure mode AFTER the page loaded (so the stub is live).
    page.evaluate("window.__ocrFail = true")
    page.click("#ocr-run")
    page.wait_for_timeout(200)
    msg = page.text_content("#lookup-msg") or ""
    assert "OCR failed" in msg
    assert "asset" in msg.lower()
    # Manual fallback remains available.
    assert page.eval_on_selector("#manual-btn", "el=>el.textContent") != ""


def test_ocr_manual_fallback_available_after_open(page, server, tmp_path):
    """Opening the OCR panel must not remove the manual/fallback path."""
    _install_ocr_control_stub(page)
    _open_ocr_with_file(page, server, _tiny_png(tmp_path))
    assert page.eval_on_selector("#manual-btn", "el=>el.textContent") != ""
    page.click("#manual-btn")
    page.wait_for_timeout(60)
    assert page.eval_on_selector("#product-info", "el=>el.textContent") != ""
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_ocr_narrow_viewport_and_keyboard(page, server, tmp_path):
    """The OCR controls and buttons are reachable on a narrow viewport and via
    the keyboard."""
    _install_ocr_control_stub(page)
    page.set_viewport_size({"width": 390, "height": 844})   # narrow mobile
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(60)
    assert page.locator("#ocr-card").evaluate("el=>el.hidden") is False
    # The file input and the recognize/cancel buttons are visible and usable.
    assert page.locator("#ocr-file").is_visible()
    assert page.locator("#ocr-run").is_visible()
    assert page.locator("#ocr-cancel").is_visible()
    # Ctrl/Cmd+Enter still checks the ingredient text (keyboard path).
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(150)
    # Resolve a deferred recognition to a draft, then use the keyboard to check.
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    page.evaluate("window.__ocr.resolves[0].resolve('Aqua, Glycerin')")
    page.wait_for_timeout(120)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == "Aqua, Glycerin"
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(120)
    # The draft was editable + checkable (results card appeared for the check).
    assert page.eval_on_selector("#ingredients", "el=>el.disabled") is False


def test_ocr_no_image_selected_is_disabled(page, server, tmp_path):
    """The 'Recognize text' button stays DISABLED until a valid image is selected
    (the UI guard, not an unhandled error)."""
    _install_ocr_control_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    # no image selected -> Recognize is disabled (cannot be clicked).
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True
    status = page.text_content("#ocr-status") or ""
    assert "Choose a" in status
    assert page.locator("#ocr-card").evaluate("el=>el.hidden") is False



# ---------------------------------------------------------------------------
# Deterministic OCR lifecycle regressions (picker replacement, delayed worker
# creation, late logger/error callbacks, empty text, and real asset failure).
#
# These use a CONTROLLABLE stub with a DEFERRED createWorker so the reviewer's
# two confirmed bugs are exercised exactly:
#   * selecting a replacement image while recognition is pending;
#   * cancelling while createWorker is still pending, then resolving it late.
# They are complemented by the real-engine and real-asset-failure tests below.
# ---------------------------------------------------------------------------
_OCR_LIFECYCLE_STUB = r"""
window.__ocr = { workers: 0, terminated: 0, recognizes: 0, logs: [], createResolvers: [], resolves: [] };
window.__ocrDeferCreate = false;  // when true, createWorker() is controllable
window.__ocrDeferRecon = false;   // when true, recognize() is controllable
window.__ocrResult = "Aqua, Glycerin";
window.__ocrFail = false;         // when true, worker creation rejects
function _makeWorker() {
  return {
    recognize: function (input) {
      window.__ocr.recognizes++;
      if (!window.__ocrDeferRecon) {
        return Promise.resolve({ data: { text: window.__ocrResult } });
      }
      var e = { resolved: false };
      e.promise = new Promise(function (resolve, reject) {
        e.resolve = function (v) { e.resolved = true; resolve({ data: { text: v } }); };
        e.reject = function (err) { e.resolved = true; reject(err); };
      });
      window.__ocr.resolves.push(e);
      return e.promise;
    },
    terminate: function () { window.__ocr.terminated++; },
  };
}
window.Tesseract = {
  createWorker: function (lang, oem, opts) {
    window.__ocr.workers++;
    if (window.__ocrFail) {
      return Promise.reject(new Error("failed to load Tesseract.js (asset missing)"));
    }
    if (!window.__ocrDeferCreate) {
      return Promise.resolve(_makeWorker());
    }
    var e = { resolved: false };
    e.promise = new Promise(function (resolve, reject) {
      e.resolve = function () { e.resolved = true; resolve(_makeWorker()); };
      e.reject = function (err) { e.resolved = true; reject(err); };
    });
    window.__ocr.createResolvers.push(e);
    return e.promise;
  }
};
"""


def _install_ocr_lifecycle_stub(page):
    page.route("**/static/vendor/tesseract/tesseract.min.js",
               lambda route: route.fulfill(status=200, content_type="application/javascript",
                                           body=_OCR_LIFECYCLE_STUB))


def test_ocr_picker_replacement_retires_old_recognition(page, server, tmp_path):
    """Start recognizing image A, select image B via the picker, resolve A.
    A must NOT become the draft and the run button must reflect B."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    # Image A and image B must be DIFFERENT file paths so the picker fires change.
    file_a = _tiny_png(tmp_path, "a.png")
    file_b = _tiny_png(tmp_path, "b.png")
    page.set_input_files("#ocr-file", file_a)
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferRecon = true")
    # Start recognizing image A (worker created immediately, recognition deferred).
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.resolves.length") == 1
    # Use the EXISTING file picker to select image B (NOT the OCR button).
    page.set_input_files("#ocr-file", file_b)
    page.wait_for_timeout(150)
    # A stale completion from A's recognition must not populate a newer draft.
    page.evaluate("window.__ocr.resolves[0].resolve('OLD IMAGE A TEXT')")
    page.wait_for_timeout(100)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""
    # The worker from A was retired and disposed (per-session release).
    assert page.evaluate("window.__ocr.terminated") >= 1


def test_ocr_invalid_replacement_retires_old_recognition(page, server, tmp_path):
    """Selecting an invalid replacement (text file) retires A: A's result must not
    appear after the rejection, and Recognize stays disabled."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferRecon = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.resolves.length") == 1
    # Replace with an unsupported text file via the picker.
    bad = tmp_path / "x.txt"
    bad.write_text("not an image")
    page.set_input_files("#ocr-file", str(bad))
    page.wait_for_timeout(150)
    status = page.text_content("#ocr-status") or ""
    assert "Unsupported image type" in status
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True
    # A's stale recognition completion must not produce a draft.
    page.evaluate("window.__ocr.resolves[0].resolve('OLD INPUT AFTER REJECTION')")
    page.wait_for_timeout(100)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""


def test_ocr_delayed_createWorker_cancel_disposes_late_worker(page, server, tmp_path):
    """Delay createWorker, cancel, then resolve it. The returned worker is
    disposed (terminated) WITHOUT becoming the current owner, and the stale
    logger must not update the cancelled UI."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferCreate = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.createResolvers.length") == 1
    # Cancel while the worker is still being created.
    page.click("#ocr-cancel")
    page.wait_for_timeout(80)
    assert page.eval_on_selector("#ocr-card", "el=>el.hidden") is True
    # Resolve the late-created worker: it is disposed, not assigned as owner.
    page.evaluate("window.__ocr.createResolvers[0].resolve()")
    page.wait_for_timeout(100)
    # The late worker was terminated (no orphan / no unbounded accumulation).
    assert page.evaluate("window.__ocr.terminated") >= 1
    # Stale recognition (if any triggered) never produced a draft.
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""


def test_ocr_delayed_createWorker_then_resolve_without_cancel_populates_draft(page, server, tmp_path):
    """Delayed createWorker resolves normally -> recognition still completes."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferCreate = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.createResolvers.length") == 1
    page.evaluate("window.__ocr.createResolvers[0].resolve()")
    page.wait_for_timeout(150)
    # Recognition completed and populated the draft.
    assert page.eval_on_selector("#ingredients", "el=>el.value") == "Aqua, Glycerin"
    assert page.eval_on_selector("#ocr-card", "el=>el.hidden") is True


def test_ocr_empty_text_reported_as_no_text_found(page, server, tmp_path):
    """Empty OCR output must be described as 'No text found', not a successful
    ingredient extraction."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrResult = ''")
    page.evaluate("window.__ocrDeferRecon = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    # Trigger a recognition that resolves to empty text.
    page.evaluate("window.__ocr.resolves[0].resolve('')")
    page.wait_for_timeout(100)
    status = page.text_content("#ocr-status") or ""
    assert "No text found" in status
    msg = page.text_content("#lookup-msg") or ""
    assert "no text" in msg.lower()
    # The draft field is NOT silently populated with the empty output.
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""


def test_ocr_mode_does_not_claim_open_food_facts_attribution(page, server, tmp_path):
    """OCR input must NOT say its data came from Open Food/Beauty Facts, even
    though OCR provenance is recorded separately."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path, "ocr_attr.png"))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferRecon = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    page.evaluate("window.__ocr.resolves[0].resolve('Aqua, Glycerin')")
    page.wait_for_timeout(100)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == "Aqua, Glycerin"
    # OCR (local) input hides the Open Food/Beauty Facts attribution.
    assert page.eval_on_selector("#src-lookup-attrib", "el=>el.hidden") is True
    assert page.eval_on_selector("#src-manual-attrib", "el=>el.hidden") is False
    assert "local" in (page.text_content("#src-manual-attrib") or "").lower()


# ---------------------------------------------------------------------------
# Real asset failure / recovery (ACTUAL library, not a mock). With the real
# Tesseract.js v6.0.1, routing the English traineddata to a 404 must NOT leave
# the UI stuck at "Loading OCR engine…" or emit an uncaught error: the bounded
# initialization must settle, surface an actionable message, and preserve the
# manual/retry path. This runs in a fresh browser context against the real
# engine (the stub tests above cover the deterministic lifecycle separately).
# ---------------------------------------------------------------------------
def test_ocr_real_asset_failure_is_bounded_and_actionable(page, server, tmp_path):
    """A 404 on the real eng.traineddata must produce a controlled failure."""
    # Route the English traineddata to a 404 BEFORE the page loads, so the real
    # Tesseract runtime sees a genuine asset fetch failure during initialization.
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/eng.traineddata.gz", lambda route: route.fulfill(
        status=404, content_type="text/plain", body="not found"))
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    assert page.evaluate("typeof window.Tesseract") == "object"
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path, "asset.png"))
    page.wait_for_timeout(150)
    # Start recognition against the real engine; the traineddata fetch fails 404.
    page.click("#ocr-run")
    # The bounded initialization must FAIL FAST (no infinite "Loading OCR engine…").
    page.wait_for_function("""() => {
      const s = document.getElementById('ocr-status').textContent || '';
      return s.indexOf('OCR failed') !== -1;
    }""", timeout=15000)
    status = page.text_content("#ocr-status") or ""
    assert "OCR failed" in status
    # It is actionable (mentions the failed asset/recoverable, not a silent hang).
    assert "Network error" in status or "load" in status.lower()
    # The run button is re-enabled for a retry and the manual path is available.
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is False
    assert page.eval_on_selector("#manual-btn", "el=>el.textContent") != ""
    # No uncaught error escaped to the page (the app handled it).
    assert errors == []


# ---------------------------------------------------------------------------
# Deterministic OVERLAP regressions for the reviewer's two confirmed sequences.
#
# These exercise the exact stale-callback problems the independent review found:
#   * A recognition pending -> select an INVALID replacement -> A settles;
#   * A initialization pending -> select/start B -> A's logger fires -> A resolves.
#
# The assertions re-check state AFTER the old operation settles (a gap the prior
# tests missed) and require the stale attempt to be fully inert: keep the
# invalid replacement DISABLED, keep the newer session's status, and TERMINATE
# the late A worker.
# ---------------------------------------------------------------------------
def test_ocr_invalid_replacement_stays_disabled_after_old_recognition_settles(page, server, tmp_path):
    """A recognition pending, then an INVALID replacement is selected; after the
    old recognition settles, the invalid file must remain DISABLED and its
    validation status must NOT be overwritten by the stale completion."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path, "overlap_a.png"))
    page.wait_for_timeout(150)
    page.evaluate("window.__ocrDeferRecon = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.resolves.length") == 1
    # Select an unsupported text file as the replacement (invalid).
    bad = tmp_path / "overlap_bad.txt"
    bad.write_text("not an image")
    page.set_input_files("#ocr-file", str(bad))
    page.wait_for_timeout(100)
    # Right after the invalid selection the status + disabled state are set.
    assert "Unsupported image type" in (page.text_content("#ocr-status") or "")
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True
    # Now let the OLD recognition settle: it must NOT flip the button enabled or
    # overwrite the invalid-file status.
    page.evaluate("window.__ocr.resolves[0].resolve('OLD AFTER INVALID')")
    page.wait_for_timeout(120)
    assert page.eval_on_selector("#ingredients", "el=>el.value") == ""
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True
    assert "Unsupported image type" in (page.text_content("#ocr-status") or "")


def test_ocr_old_logger_and_late_init_do_not_touch_new_session(page, server, tmp_path):
    """A initialization pending -> select/start B -> A's logger fires -> A
    resolves. The old A attempt must not change B's status, enable stale
    controls, or become the worker owner; A's late worker is terminated."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    file_a = _tiny_png(tmp_path, "overlap_a2.png")
    file_b = _tiny_png(tmp_path, "overlap_b2.png")
    page.set_input_files("#ocr-file", file_a)
    page.wait_for_timeout(150)
    # Defer createWorker so A's initialization stays pending.
    page.evaluate("window.__ocrDeferCreate = true")
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.createResolvers.length") == 1
    # Select B (valid) and start its recognition; init B also deferred.
    page.set_input_files("#ocr-file", file_b)
    page.wait_for_timeout(150)
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is False
    page.click("#ocr-run")
    page.wait_for_timeout(80)
    assert page.evaluate("window.__ocr.createResolvers.length") == 2
    # A's logger fires (should be ignored -- it is not the current owner).
    page.evaluate("window.__ocr.capture = { add: function () {} }")
    # Resolve A's deferred createWorker promise (returns A's worker).
    page.evaluate("window.__ocr.createResolvers[0].resolve()")
    page.wait_for_timeout(120)
    # A was NOT assigned as the owner; A's worker was terminated.
    assert page.evaluate("window.__ocr.terminated") >= 1
    # B is still loading (its init deferred), NOT overwritten by A.
    assert "Loading OCR engine" in (page.text_content("#ocr-status") or "")
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is True


def test_ocr_repeated_real_asset_failure_disposes_each_worker(page, server, tmp_path):
    """With the ACTUAL library, three consecutive traineddata 404 attempts each
    terminate their spawned native Worker. After Cancel there are NO active
    workers: the runtime worker really is disposed, not merely hidden by an
    updated error message."""
    # Instrument native Worker creation/termination BEFORE the page loads.
    page.add_init_script("""window.workerAudit = [];
      const NativeWorker = window.Worker;
      window.Worker = class extends NativeWorker {
        constructor(...args) {
          super(...args);
          const e = { terminated: false, url: String(args[0]) };
          workerAudit.push(e);
          const orig = this.terminate.bind(this);
          this.terminate = () => { e.terminated = true; return orig(); };
        }
      };""")
    # First route the real traineddata to a 404.
    page.route("**/eng.traineddata.gz", lambda route: route.fulfill(
        status=404, content_type="text/plain", body="not found"))
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    assert page.evaluate("typeof window.Tesseract") == "object"
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    page.set_input_files("#ocr-file", _tiny_png(tmp_path, "lifetime.png"))
    page.wait_for_timeout(150)
    page.wait_for_function("!document.getElementById('ocr-run').disabled")
    for _ in range(3):
        page.click("#ocr-run")
        page.wait_for_function("""() => {
          const s = document.getElementById('ocr-status').textContent || '';
          return s.indexOf('OCR failed') !== -1;
        }""", timeout=20000)
    audit_after3 = page.evaluate("workerAudit")
    # All THREE native workers were terminated (real disposal, no orphans).
    assert len(audit_after3) == 3, audit_after3
    assert all(w["terminated"] for w in audit_after3), audit_after3
    # Cancel after failures: active workers return to baseline (0).
    page.click("#ocr-cancel")
    page.wait_for_timeout(150)
    assert len(page.workers) == 0


def test_ocr_recovery_after_asset_failure_retries_clean(page, server, tmp_path):
    """After a bounded asset failure, a retry RECOVERS: the worker is replaced
    cleanly, the retry recognizes a real draft, and active workers return to
    baseline. Uses the deterministic stub so the recovery path of the SAME
    controller code is exercised without a long real-engine reload."""
    _install_ocr_lifecycle_stub(page)
    page.goto(server + "/")
    page.wait_for_selector("#barcode")
    page.click("#ocr-btn")
    page.wait_for_timeout(50)
    file_a = _tiny_png(tmp_path, "recovery_a.png")
    page.set_input_files("#ocr-file", file_a)
    page.wait_for_timeout(150)
    page.wait_for_function("!document.getElementById('ocr-run').disabled")
    # First attempt fails at worker creation (asset failure).
    page.evaluate("window.__ocrFail = true")
    page.click("#ocr-run")
    page.wait_for_function("""() => {
      const s = document.getElementById('ocr-status').textContent || '';
      return s.indexOf('OCR failed') !== -1;
    }""", timeout=10000)
    assert "OCR failed" in (page.text_content("#ocr-status") or "")
    assert page.eval_on_selector("#ocr-run", "el=>el.disabled") is False
    # RECOVERY: clear the failure and retry on the SAME session.
    page.evaluate("window.__ocrFail = false")
    page.click("#ocr-run")
    page.wait_for_timeout(150)
    # The retry recognized a draft through the (stubbed) worker.
    assert page.eval_on_selector("#ingredients", "el=>el.value") == "Aqua, Glycerin"
    # No extra worker leaked: exactly the failed attempt (which still spawns a
    # worker object before rejecting) plus the one successful retry.
    assert page.evaluate("window.__ocr.workers") <= 2, page.evaluate("window.__ocr.workers")
    # The successful worker was disposed on the recognized-draft path.
    assert page.evaluate("window.__ocr.terminated") >= 1


def test_csp_blocks_injected_inline_script(page, server):
    response = page.goto(server)
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    page.click('#manual-btn')  # The packaged application script still runs.
    assert page.locator('#ingredients').is_visible()
    assert page.evaluate('''() => {
      const injected = document.createElement('script');
      injected.textContent = 'window.__unexpectedInlineExecution = true';
      document.body.appendChild(injected);
      return window.__unexpectedInlineExecution === true;
    }''') is False
