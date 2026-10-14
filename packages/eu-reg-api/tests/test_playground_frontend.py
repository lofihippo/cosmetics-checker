"""Repeatable frontend regression tests for the browser playground.

These start a real Uvicorn app (built from the in-memory conftest registry) on a
free port and drive it with Playwright (system Chrome) so the request/input
lifecycle, health-failure honesty, and unknown-ingredient wording are verified
against real HTTP responses. They do not touch the workspace snapshot.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from eu_reg_api.main import create_app
from playwright.sync_api import sync_playwright


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(registry):
    reg, metadata = registry
    app = create_app(reg, metadata)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server_uv = uvicorn.Server(config)
    thread = threading.Thread(target=server_uv.run, daemon=True)
    thread.start()
    # wait for readiness
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
    p.goto(server + "/")
    p.wait_for_selector("#snap-version")
    yield (p, server)
    p.close()


def _run_check(page, server, label):
    page.fill("#ingredients", label)
    page.click("#check-btn")
    page.wait_for_selector("#ingredient-results .ingredient-card", timeout=8000)
    page.wait_for_timeout(200)


def _state(page):
    return (page.locator("#ingredient-results .ingredient-card").count(),
            page.eval_on_selector("#summary", "el=>el.hidden"),
            (page.text_content("#results-message") or "").strip())


def test_manual_edit_clears_previous_results(page):
    p, server = page
    _run_check(p, server, "Benzene")
    assert p.locator("#ingredient-results .ingredient-card").count() == 1
    p.fill("#ingredients", "Aqua, Glycerin")   # triggers invalidate on input
    p.wait_for_timeout(150)
    cards, hidden, msg = _state(p)
    assert cards == 0 and hidden is True and msg == ""


def test_example_click_clears_previous_results(page):
    p, server = page
    _run_check(p, server, "Benzene")
    assert p.locator("#ingredient-results .ingredient-card").count() == 1
    p.click(".example-btn[data-label='Aqua, Glycerin']")
    p.wait_for_timeout(150)
    cards, hidden, _ = _state(p)
    assert cards == 0 and hidden is True


def test_then_submit_empty_tokenless_and_oversize_hide_previous(page):
    p, server = page
    for bad, expect in [(";;;", "No ingredient tokens"), ("x" * 10001, "10000-character limit"),
                        ("", "No ingredient tokens")]:
        _run_check(p, server, "Benzene")
        p.fill("#ingredients", bad)
        p.click("#check-btn")
        p.wait_for_timeout(200)
        cards, hidden, msg = _state(p)
        assert cards == 0 and hidden is True, (bad, cards, hidden)
        assert expect in msg, (bad, msg)


def test_stale_error_after_newer_success_does_not_replace_status(page):
    """Reject A after B succeeds: A's error must not replace B's status."""
    p, server = page
    _install_controlled_fetch(p, server)
    _submit(p, "Benzene")          # A (id=1), will reject later
    _submit(p, "1,4-dioxane")      # B (id=2) succeeds
    _settle(p, 1, _banned_payload())
    assert p.text_content("#results-message").strip() == "Check complete."
    _reject(p, 0)                  # A rejects AFTER B succeeded
    assert p.text_content("#results-message").strip() == "Check complete."


def test_health_503_json_not_ready(browser, server):
    _assert_health_failure(browser, server, lambda r: r.fulfill(
        status=503, content_type="application/json",
        body='{"error":{"code":"not_ready","message":"snapshot missing"}}'))


def test_health_malformed_json_not_ready(browser, server):
    _assert_health_failure(browser, server, lambda r: r.fulfill(
        status=200, content_type="application/json", body="not-json{{{"))


def test_health_malformed_200_not_ready(browser, server):
    _assert_health_failure(browser, server, lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"status":"ready"}'))


def test_health_network_failure_not_ready(browser, server):
    _assert_health_failure(browser, server, lambda r: r.abort())


def _assert_health_failure(browser, server, handler):
    p = browser.new_page()
    p.route("**/health", handler)
    p.goto(server + "/")
    p.wait_for_timeout(600)
    status = p.text_content("#snapshot-status").strip()
    assert "Ready" not in status, status
    assert "Could not load snapshot" in status, status
    # snapshot fields must be marked unavailable, never confirmed empty lists.
    for f in ("version", "dt", "substances", "verified", "available", "missing"):
        assert p.text_content(f"#snap-{f}").strip() == "Unavailable", f
    p.close()


def test_unmatched_ingredient_stays_unknown_without_no_rule_claim(page):
    p, server = page
    _run_check(p, server, "TotallyFake999")
    body = p.text_content("#ingredient-results")
    assert "No matching record was found in this snapshot" in body
    assert "Regulatory status is unknown" in body
    assert "Not found does not mean unrestricted" in body
    assert "No rule applies" not in body
    assert "not found / unresolved" in body
    # disclaimer present
    assert "Regulatory information, not legal advice" in p.content()


def test_known_but_unverified_wording(page):
    p, server = page
    _run_check(p, server, "Aqua")
    body = p.text_content("#ingredient-results")
    assert "unknown, never clean" in body
    assert "No verified rule was found" in body


def test_conditional_positive_list_is_flagged_not_clean(page):
    """A conditional positive-list ingredient (Annex IV/VI) is flagged with its
    conditions/citations and never shown as clean."""
    p, server = page
    _run_check(p, server, "Zinc oxide")
    cards = p.locator("#ingredient-results .ingredient-card")
    assert cards.count() == 1
    assert "FLAGGED" in cards.first.get_attribute("class")
    summary = p.text_content("#summary")
    assert "FLAGGED" in summary
    body = p.text_content("#ingredient-results")
    assert "Annex IV" in body or "Annex VI" in body
    assert "25%" in body
    # The conditional-allowance wording is explicit (not a clean claim).
    assert "allowed with conditions (not clean)" in body
    assert "Regulatory information, not legal advice" in p.content()


def test_retained_successful_flows(page):
    p, server = page
    _run_check(p, server, "Aqua, Benzene, TotallyFake123")
    p.wait_for_timeout(300)
    assert p.text_content("#summary h3").strip().endswith("FLAGGED")
    body = p.text_content("#ingredient-results")
    assert "(EC) 2009/1223" in body and "Annex II" in body
    assert "https://ec.europa.eu" in body and "data_updated_at" in body
    assert p.locator(".ingredient-card.FLAGGED").count() == 1
    assert p.locator(".ingredient-card.UNKNOWN").count() == 1
    assert p.locator(".ingredient-card.NOTFOUND").count() == 1
    # source details expand
    p.locator(".source-toggle").first.click()
    p.wait_for_timeout(150)
    assert p.locator(".source-details:not([hidden])").count() == 1

    _run_check(p, server, "1,4-dioxane")
    assert p.locator(".ingredient-card.FLAGGED").count() == 1

    _run_check(p, server, "Aqua, Glycerin")
    assert "NOT-FULLY-VERIFIED" in p.text_content("#summary h3")
    # fixture has Aqua (unknown identity) and no Glycerin record (not found).
    assert p.locator(".ingredient-card.UNKNOWN").count() == 1
    assert p.locator(".ingredient-card.NOTFOUND").count() == 1


def test_scoped_prohibition_is_not_presented_as_unconditional_banned(page):
    """A scoped/exception-qualified Annex II prohibition must produce a
    'Prohibition scope unresolved' headline (not an unconditional BANNED badge),
    even though the original per-rule 'banned' status is preserved in the
    labelled evidence. Benzene (unambiguous) keeps its clear prohibition."""
    p, server = page
    _run_check(p, server, "Hydroquinone")
    card = p.locator("#ingredient-results .ingredient-card.FLAGGED").first
    assert card.count() == 1
    badge = card.locator(".badge").first.text_content()
    assert badge == "Prohibition scope unresolved", badge
    body = card.text_content()
    # The per-rule status is still visible as evidence (not erased).
    assert "banned" in body, body
    assert "Annex II" in body and "(EC) 2009/1223" in body
    # The unconditional-sounding body message must not appear.
    assert "Prohibition scope unresolved." in body, body
    assert "applicability to this product is not evaluated" in body
    # The linked Annex III restriction is shown as an evaluated/identified
    # restriction (its per-rule status preserved as evidence).
    assert "Annex III" in body, body
    assert "restriction_identified" in body, body

    # Benzene must remain an unambiguous prohibition for contrast.
    _run_check(p, server, "Benzene")
    bcard = p.locator("#ingredient-results .ingredient-card.FLAGGED").first
    assert bcard.locator(".badge").first.text_content() == "banned"
    assert "Prohibition scope unresolved" not in bcard.text_content()


def test_scoped_and_unambiguous_prohibitions_agree_in_summary(page):
    """A label mixing a scoped prohibition and an unambiguous one keeps the
    summary counts consistent: 2 flagged, only 1 definitive ban."""
    p, server = page
    _run_check(p, server, "Hydroquinone, Benzene")
    summary = p.text_content("#summary")
    assert "FLAGGED" in summary
    assert "Banned: 1" in summary
    assert "Flagged: 2" in summary
    cards = p.locator("#ingredient-results .ingredient-card")
    assert cards.count() == 2
    # One card is scope-unresolved, the other unambiguous banned.
    assert p.locator(".ingredient-card .badge", has_text="Prohibition scope unresolved").count() == 1
    assert p.locator(".ingredient-card .badge", has_text="banned").count() == 1


def test_d4_independent_prohibition_is_prominent_over_entry70(page):
    """D4 (556-67-2) carries an independent, unambiguous CosIng Annex II
    prohibition AND a source-bound REACH entry 70 (missing-context) finding.
    The prominent headline/body must prioritise the CosIng prohibition while the
    entry-70 finding is shown separately — never prioritised over the ban."""
    p, server = page
    _run_check(p, server, "556-67-2")
    card = p.locator("#ingredient-results .ingredient-card.FLAGGED").first
    assert card.count() == 1
    # Summary counts the definitive ban.
    assert "Banned: 1" in p.text_content("#summary")
    # Headline badge prioritises the CosIng prohibition.
    assert card.locator(".badge").first.text_content() == "banned"
    body = card.text_content()
    # The CosIng prohibition is prominent (unconditional wording).
    assert "Banned/prohibited by CosIng" in body, body
    assert "No concentration evaluation is applicable" in body, body
    # The REACH entry 70 finding is preserved separately and not asserted as a
    # trigger/applicable restriction.
    assert "entry 70" in body.lower() or "Reach Annex XVII entry 70".lower() in body.lower()
    assert "context-required" in body.lower() or "context required" in body.lower()
    assert "never a category violation" in body.lower()
    # The category-violation-style headline must not take priority.
    assert "REACH entry 70 restriction identified (context required)" not in (
        card.locator(".badge").first.text_content())
    # Scoped phrasing preserved for the D4 CosIng rule (it is unambiguous).
    assert "Prohibition scope unresolved" not in body


def test_d5_label_only_wording_identifies_without_asserting_applicability(page):
    """A label-only D5/D6 check identifies a REACH entry 70 restriction without
    asserting that it applies or was triggered (missing context)."""
    p, server = page
    _run_check(p, server, "Cyclopentasiloxane")
    card = p.locator("#ingredient-results .ingredient-card.FLAGGED").first
    assert card.count() == 1
    body = card.text_content()
    assert "context required" in body.lower()
    assert "restriction identified" in body.lower()
    # The prominent interpretation never asserts applicability/trigger; it only
    # says the restriction is identified and applicability requires the missing
    # context. (The entry-70 evidence reason legitimately says "which branch
    # applies is unresolved" — that is about the unresolved branch, not a claim
    # that the restriction applies to the product.)
    assert "no supported restriction trigger is asserted" in body.lower()
    assert "applicability requires the missing" in body.lower()
    assert "no threshold violation has been established" in body.lower()
    # Unknown-with-no-rule wording is not used (entry 70 finding is real evidence).
    assert "No verified rule was found" not in body
    # The summary uses identified language, not triggered.
    assert "was identified" in p.text_content("#summary")
    assert "triggered" not in p.text_content("#summary").lower()


def test_scoped_and_unknown_cases_remain_correct(page):
    """Scoped-prohibition and unknown/not-found cases keep their established
    wording (not regressed by the entry-70 presentation change)."""
    p, server = page
    # Scoped Hydroquinone keeps scope-unresolved headline.
    _run_check(p, server, "Hydroquinone")
    assert "Prohibition scope unresolved" in p.text_content("#ingredient-results")
    # Unknown-Aqua keeps unknown wording.
    _run_check(p, server, "Aqua")
    assert "unknown, never clean" in p.text_content("#ingredient-results")
    assert "No verified rule was found" in p.text_content("#ingredient-results")


# ---------------------------------------------------------------------------
# Deterministic request-invalidation tests.
# These inject a controllable window.fetch that IGNORES AbortSignal, so the
# request-id generation guard (not cancellation) must keep stale callbacks from
# mutating the UI. Promises are resolved explicitly, not by sleep timing.
# A pending request leaves the Check button disabled, so to start a SECOND
# request while the first is still pending we dispatch the click event directly
# (bypassing Playwright's enabled-button auto-wait). Submission still goes
# through the same checkIngredients() path.
# ---------------------------------------------------------------------------

_CONTROLLED_FETCH = r"""
window.__requests = [];
window.__realFetch = window.fetch.bind(window);
window.__deferredFetch = function (url, opts) {
  // Only defer /v1/check; let /health (and anything else) pass through so the
  // page still loads snapshot/coverage normally.
  if (String(url).indexOf("/v1/check") === -1) {
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
  var body = new TextEncoder().encode(JSON.stringify(payload));
  e.resolve(new Response(body, { status: status || 200, headers: {
    "content-type": "application/json" } }));
  e.resolved = true;
};
window.rejectRequest = function (index) {
  var e = window.__requests[index];
  e.reject(new TypeError("network failure"));
  e.resolved = true;
};
window.__requestCount = function () { return window.__requests.length; };
window.dispatchCheckClick = function () {
  document.getElementById("check-btn").dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true }));
};
window.fetch = window.__deferredFetch;
"""


def _install_controlled_fetch(page, server):
    page.add_init_script(_CONTROLLED_FETCH)
    page.goto(server + "/")
    page.wait_for_selector("#snap-version")


def _submit(page, label):
    # Fill the field and dispatch a click event directly so a check can be
    # started even while the button is disabled (pending request).
    page.fill("#ingredients", label)
    page.evaluate("window.dispatchCheckClick()")


def _click(page, label):
    page.fill("#ingredients", label)
    page.click("#check-btn")


def _settle(page, index, payload, status=200):
    page.evaluate("([i,p,s]) => window.settleRequest(i, p, s)",
                  [index, payload, status])
    page.wait_for_timeout(30)


def _reject(page, index):
    page.evaluate("(i) => window.rejectRequest(i)", [index])
    page.wait_for_timeout(30)


def _reqs(page):
    return page.evaluate("window.__requestCount()")


def _banned_payload():
    return {
        "verdict": "FLAGGED", "clean": False, "no_ingredients": False,
        "n_flagged": 1, "n_banned": 1, "n_unverified": 0,
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 3, "results": [
            {"ingredient": "benzene", "is_in_dataset": True, "determination": "banned",
             "status": "banned", "annex": "Annex II", "regulation": "(EC) 2009/1223",
             "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [], "cas": "71-43-2", "ec": "200-753-7",
             "e_number": None, "ci": None, "resolution": "cosing-annex-ii",
             "cased_name": "Benzene"},
        ],
    }


def _unknown_payload():
    return {
        "verdict": "NOT-FULLY-VERIFIED", "clean": False, "no_ingredients": False,
        "n_flagged": 0, "n_banned": 0, "n_unverified": 2,
        "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
        "registry_size": 3, "results": [
            {"ingredient": "aqua", "is_in_dataset": True, "determination": "unknown",
             "status": None, "annex": None, "regulation": None, "source_url": None,
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [], "cas": "7732-18-5", "ec": "231-791-2",
             "e_number": None, "ci": None, "resolution": None, "cased_name": "Aqua"},
            {"ingredient": "glycerin", "is_in_dataset": False, "determination": "unknown",
             "status": None, "annex": None, "regulation": None, "source_url": None,
             "data_updated_at": "2026-10-08T14:35:34.921319+00:00",
             "conditions": None, "rules": [], "cas": None, "ec": None,
             "e_number": None, "ci": None, "resolution": None, "cased_name": None},
        ],
    }


def test_pending_request_then_manual_edit_old_success_does_not_return(page):
    """Pending request -> manual edit -> old success resolved: nothing reappears."""
    p, server = page
    _install_controlled_fetch(p, server)
    _click(p, "Benzene")
    assert _reqs(p) == 1
    p.fill("#ingredients", "Aqua, Glycerin")   # invalidate (no resubmit)
    p.wait_for_timeout(40)
    assert _reqs(p) == 1                        # no new request fired
    assert p.locator("#ingredient-results .ingredient-card").count() == 0
    _settle(p, 0, _banned_payload())            # resolve the abort-ignored original
    cards, hidden, msg = _state(p)
    assert cards == 0, "old success must not render cards"
    assert hidden is True, "old success must not show summary"
    assert "Check complete" not in msg, "old success status must not reappear"
    assert msg == "", msg


def test_pending_request_then_example_change_old_rejection_does_not_appear(page):
    """Pending request -> example change -> old rejection: no old error."""
    p, server = page
    _install_controlled_fetch(p, server)
    _click(p, "Benzene")
    assert _reqs(p) == 1
    p.click(".example-btn[data-label='Aqua, Glycerin']")   # invalidate
    p.wait_for_timeout(40)
    _reject(p, 0)   # old rejection, abort-ignored
    msg = p.text_content("#results-message").strip()
    assert "Network failure" not in msg
    assert msg == "", msg


def test_invalidation_enables_check_button_immediately(page):
    """Invalidating restores the Check button even if the old request never settles."""
    p, server = page
    _install_controlled_fetch(p, server)
    _click(p, "Benzene")
    assert p.eval_on_selector("#check-btn", "el=>el.disabled") is True
    p.fill("#ingredients", "Aqua, Glycerin")   # invalidate, request never settles
    p.wait_for_timeout(40)
    assert p.eval_on_selector("#check-btn", "el=>el.disabled") is False


def test_two_requests_resolved_B_then_A_keeps_B(page):
    """Resolve B first then A: A must not replace B's results."""
    p, server = page
    _install_controlled_fetch(p, server)
    _submit(p, "Benzene")          # A (id=1), pending -> returns FLAGGED if applied
    assert _reqs(p) == 1
    _submit(p, "Aqua, Glycerin")   # B (id=2), pending -> returns unknown if applied
    assert _reqs(p) == 2
    _settle(p, 1, _unknown_payload())   # B resolves FIRST (newer)
    _settle(p, 0, _banned_payload())    # A resolves SECOND (older) -> ignored
    # B is the newest; its names, verdict and status must remain.
    cards = p.locator("#ingredient-results .ingredient-card").count()
    names = [p.locator(".ingredient-name").nth(i).text_content() for i in range(cards)]
    assert names == ["aqua", "glycerin"], names
    assert cards == 2, cards
    verdict = p.text_content("#summary h3").strip()
    assert "NOT-FULLY-VERIFIED" in verdict, verdict
    assert "FLAGGED" not in verdict  # older A's banned result must not override B
    assert p.locator(".ingredient-card.FLAGGED").count() == 0
    # status text belongs to B
    assert "unknown determination" in p.text_content("#ingredient-results")
    assert "not found / unresolved" in p.text_content("#ingredient-results")


def test_reject_A_after_B_succeeds_keeps_B_status(page):
    """Reject A after B succeeds: B's status remains."""
    p, server = page
    _install_controlled_fetch(p, server)
    _submit(p, "Benzene")          # A (id=1)
    _submit(p, "1,4-dioxane")      # B (id=2) succeeds
    _settle(p, 1, _banned_payload())
    assert p.text_content("#results-message").strip() == "Check complete."
    _reject(p, 0)                  # A rejects AFTER B succeeded
    assert p.text_content("#results-message").strip() == "Check complete."


def test_A_finally_cannot_enable_button_while_B_pending(page):
    """Starting B after invalidating A: A's finally can't enable while B pending."""
    p, server = page
    _install_controlled_fetch(p, server)
    _submit(p, "Benzene")          # A (id=1), pending
    _submit(p, "1,4-dioxane")      # B (id=2), pending
    _settle(p, 0, _banned_payload())   # A settles (old) -> its finally runs
    assert p.eval_on_selector("#check-btn", "el=>el.disabled") is True


def test_validation_submissions_leave_no_stale_results_and_enabled(page):
    """Empty/tokenless/oversized after a pending request leave no stale results."""
    p, server = page
    _install_controlled_fetch(p, server)
    _click(p, "Benzene")   # pending
    p.fill("#ingredients", ";;;")
    p.evaluate("window.dispatchCheckClick()")   # tokenless, no fetch fired
    p.wait_for_timeout(40)
    cards, hidden, msg = _state(p)
    assert cards == 0 and hidden is True
    assert "No ingredient tokens" in msg
    assert p.eval_on_selector("#check-btn", "el=>el.disabled") is False
