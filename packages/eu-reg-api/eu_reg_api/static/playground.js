"use strict";

(function () {
  var MAX_TEXT_LENGTH = 10000;

  // ---- elements ----
  var $ = function (id) { return document.getElementById(id); };
  var ingredientsEl = $("ingredients");
  var charCountEl = $("char-count");
  var checkBtn = $("check-btn");
  var resultsMessage = $("results-message");
  var summaryEl = $("summary");
  var resultsList = $("ingredient-results");
  var snapshotStatus = $("snapshot-status");

  // ---- request lifecycle ----
  // generation increments on EVERY invalidation (manual edit, example change,
  // replacement submission) AND on every new check, so a request id is current
  // only while its generation matches. Retiring on invalidation means a request
  // that ignores AbortSignal can still never mutate the UI after the input
  // changed. Only the newest generation may touch the UI.
  var generation = 0;
  var controller = null;      // AbortController for the in-flight request

  function text(s) { return document.createTextNode(s == null ? "" : String(s)); }
  function el(tag, cls, parent) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (parent) parent.appendChild(n);
    return n;
  }
  function dlFrom(entries, parent) {
    var d = el("dl", null, parent);
    entries.forEach(function (e) {
      var dt = el("dt", null, d);
      dt.textContent = e[0];
      var dd = el("dd", null, d);
      dd.textContent = e[1] == null || e[1] === "" ? "Not provided" : String(e[1]);
    });
    return d;
  }

  // ---- linking ----
  function isHttpUrl(u) {
    return typeof u === "string" && /^https?:\/\//i.test(u);
  }
  function safeLink(url, label) {
    label = label || url;
    var a = document.createElement("a");
    a.className = "citation-link";
    a.textContent = label;
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  function setStatus(textValue, kind) {
    resultsMessage.className = "status-banner" + (kind ? " " + kind : "");
    resultsMessage.replaceChildren();
    if (textValue) resultsMessage.appendChild(text(textValue));
  }

  // Clear the status banner entirely (no text, neutral styling).
  function clearStatus() {
    resultsMessage.className = "status-banner";
    resultsMessage.replaceChildren();
  }

  // Clear any current findings, retire the in-flight request's generation, and
  // restore the Check button. Called whenever the input is edited (manually or
  // via an example) and before a new check, so no previous result can be
  // mistaken for the current label and a request that ignores abort can never
  // mutate the UI after the input changes.
  function invalidateResults() {
    generation++;                        // retire the current request id
    if (controller) { controller.abort(); controller = null; }
    resultsList.replaceChildren();
    summaryEl.hidden = true;
    summaryEl.replaceChildren();
    checkBtn.disabled = false;           // restore immediately
  }

  // Only the newest generation may touch the UI. Every success/error/finally
  // continuation re-checks identity AFTER any await/async boundary before
  // mutating anything.
  function isCurrent(id) { return id === generation; }

  // ---- char counter ----
  function refreshCount() {
    var n = ingredientsEl.value.length;
    charCountEl.textContent = n + " / " + MAX_TEXT_LENGTH + " characters";
    ingredientsEl.classList.toggle("over-limit", n > MAX_TEXT_LENGTH);
  }

  // ---- examples ----
  document.querySelectorAll(".example-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      // Same invalidation path as a manual edit: clear prior findings + status.
      invalidateResults(); clearStatus();
      ingredientsEl.value = btn.getAttribute("data-label") || "";
      refreshCount();
      ingredientsEl.focus();
    });
  });

  // Manual edits: abort in-flight request and clear prior findings.
  ingredientsEl.addEventListener("input", function () {
    invalidateResults(); clearStatus();
    refreshCount();
  });

  // ---- health ----
  function setText(id, value) {
    $("snap-" + id).textContent = value == null || value === "" ? "Not provided" : String(value);
  }

  function markHealthUnavailable(msg) {
    ["version", "dt", "substances", "verified", "available", "missing",
     "not-included"].forEach(function (k) {
      $("snap-" + k).textContent = "Unavailable";
    });
    snapshotStatus.className = "status-banner err";
    snapshotStatus.textContent = msg;
  }

  // A health payload is only trusted when it is an HTTP 2xx AND carries the
  // readiness/snapshot fields the page depends on. Otherwise we report the
  // failure honestly instead of claiming readiness (and never treat its
  // missing coverage fields as confirmed empty lists).
  function healthy(h) {
    return h && typeof h === "object" && h.status === "ready" && !!(h.coverage) &&
      h.data_updated_at !== undefined && h.count_substances !== undefined &&
      h.count_verified_rules !== undefined && Array.isArray(h.coverage.available) &&
      Array.isArray(h.coverage.missing);
  }

  function applyHealth(h) {
    setText("version", h.version);
    setText("dt", h.data_updated_at);
    setText("substances", h.count_substances);
    setText("verified", h.count_verified_rules);
    $("snap-available").textContent = h.coverage.available.length
      ? h.coverage.available.join(", ") : "None";
    $("snap-missing").textContent = h.coverage.missing.length
      ? h.coverage.missing.join(", ") : "None";
    $("snap-not-included").textContent =
      (h.coverage.not_included && h.coverage.not_included.length)
        ? h.coverage.not_included.join(", ") : "None";
    // Name the source-bound REACH Annex XVII entry 70 coverage (entry + the
    // supported cosmetic branches) explicitly — never "full Annex XVII" and
    // never ECHA.
    var a17 = (h.coverage.annex17_entries || []);
    var a17b = (h.coverage.annex17_branches || []);
    $("snap-annex17").textContent = a17.length
      ? ("entry " + a17.join(", ") + " cosmetic branches: "
         + (a17b.length ? a17b.join(", ") : "n/a"))
      : "None";
    snapshotStatus.className = "status-banner ok";
    snapshotStatus.textContent =
      "Ready. Coverage: available lists above; missing lists are gaps and "
      "excluded lists are deliberately out of scope — all not clean results. "
      "REACH Annex XVII is covered only for entry 70 D4/D5/D6 cosmetic branches; "
      "it is not full Annex XVII or REACH coverage.";
  }

  function loadHealth() {
    snapshotStatus.className = "status-banner";
    snapshotStatus.textContent = "Loading snapshot…";
    fetch("/health")
      .then(function (r) {
        if (!r.ok) {
          // Non-2xx (e.g. 503 with a JSON/error body) is a failure, not ready.
          throw new Error("HTTP " + r.status + (r.statusText ? " " + r.statusText : ""));
        }
        return r.json();
      })
      .then(function (h) {
        if (!healthy(h)) {
          throw new Error("Health response was missing expected snapshot fields.");
        }
        applyHealth(h);
      })
      .catch(function (err) {
        markHealthUnavailable(
          "Could not load snapshot. Check that the API is running and EU_REG_SNAPSHOT " +
          "points to a valid snapshot: " + (err && err.message ? err.message : "network error"));
      });
  }

  // ---- check ----
  function verdictLabel(v, clean) {
    if (clean) return "No EU ban/restriction triggered for every fully verified ingredient (not a safety certificate).";
    if (v === "FLAGGED") return "At least one banned/restricted/conditional rule, or a retained REACH entry 70 restriction, was identified. A conditional allowance (Annex IV/V/VI positive list) is not a clean/green result; any identified-but-unevaluated restriction requires the missing context before applicability is established.";
    if (v === "NO-INGREDIENTS") return "No ingredient tokens were parsed.";
    return "Not fully verified: one or more ingredients are unknown or unresolved.";
  }

  function classify(res) {
    if (res.status === "banned" || res.status === "restricted" || res.status === "listed" || res.status === "allowed_with_conditions") {
      return "FLAGGED";
    }
    // A grouped member whose evidence is only a linked group rule is still a
    // conditional/restricted/listing finding (explicit source-backed membership).
    var linked = res.linked_rules || [];
    if (linked.some(function (lr) {
      return lr.status === "banned" || lr.status === "restricted" || lr.status === "listed" || lr.status === "allowed_with_conditions";
    })) {
      return "FLAGGED";
    }
    // A retained, source-bound REACH Annex XVII entry 70 (D4/D5/D6) finding is a
    // restriction finding (identified-but-unevaluated on a label-only check).
    // It is never a category violation and never an unverified "no verified rule"
    // identity. Only a PRESENT entry70 verdict with a real state is treated as
    // evidence; an undefined/absent entry70 (non-D4/D5/D6 identity) is untouched.
    var e70 = res.entry70;
    if (e70 && e70.state && e70.state !== "not_entry70_substance" && e70.state !== "source_not_active") {
      return "FLAGGED";
    }
    if (!res.is_in_dataset) return "NOTFOUND";
    return "UNKNOWN";
  }

  function badgeClass(cls, label) {
    var b = el("span", "badge " + cls);
    b.textContent = label;
    return b;
  }

  function statusLabelFor(res, cls) {
    if (cls === "FLAGGED") {
      // Derive the user-facing headline from the SHARED assessment fields the
      // backend already computed (prohibition_finding / evaluation), so a scoped
      // or exception-qualified prohibition is never presented as an
      // unconditional "banned". The original per-rule status ("banned") remains
      // in the labelled evidence rows below; this only changes the headline.
      var finding = res.prohibition_finding || null;
      var evalState = res.evaluation || null;
      // An independent, source-bound CosIng determination takes headline
      // priority over the supplementary REACH entry 70 finding, so a definitive
      // CosIng prohibition is prominent while the entry 70 context-required
      // evidence stays separate (see the entry70 block in the card body). A
      // scoped/exception-qualified prohibition is still never an unconditional
      // "banned".
      if (finding === "unambiguous" || evalState === "prohibition_identified") {
        return "banned";
      }
      if (finding === "scope_unresolved") {
        if (res.status === "banned") return "Prohibition scope unresolved";
        return "Exception requires review";
      }
      if (evalState === "listing_identified") {
        return "Listing identified (contextual review)";
      }
      if (evalState === "restriction_identified") {
        return "Restriction identified (context required)";
      }
      // Otherwise a retained, source-bound REACH Annex XVII entry 70 (D4/D5/D6)
      // finding is a restriction finding (identified-but-unevaluated on a
      // label-only check), never a category violation and never an unverified
      // "no verified rule". It is only treated as evidence when present with a
      // real state.
      var e70 = res.entry70;
      if (e70 && e70.state && e70.state !== "not_entry70_substance" && e70.state !== "source_not_active") {
        return "REACH entry 70 restriction identified (context required)";
      }
      var st = res.status || res.determination || "flag";
      if (st === "allowed_with_conditions") return "allowed with conditions (not clean)";
      return st.replace(/_/g, " ");
    }
    if (cls === "NOTFOUND") return "not found";
    return "unknown (recognised, no verified rule)";
  }

  function renderFinding(res, finding, parent) {
    var f = el("div", "finding", parent);
    var reg = finding.regulation || "Not provided";
    var annex = finding.annex || "Not provided";
    var url = finding.source_url;
    // Use THIS rule's status (per-rule), not the ingredient headline status.
    var ruleStatus = finding.status || "unknown";

    var d = el("dl", null, f);
    var rows = [
      ["Status", ruleStatus],
      ["Regulation", reg],
      ["Annex", annex],
      ["Source URL", url ? "link" : "Not provided"],
      ["data_updated_at", res.data_updated_at],
    ];
    // Expose the machine-readable evaluation state of the ingredient's rules
    // (distinguishing "restriction identified" from "violation evaluated").
    if (res.evaluation) {
      rows.push(["Evaluation", res.evaluation]);
      if (res.evaluation_reason) {
        rows.push(["Evaluation detail", res.evaluation_reason]);
      }
    }
    // Expose the source scope/exception text from the original source row (e.g.
    // Hydroquinone's Annex II qualification referencing Annex III entry 14), so
    // it is not accessible only through the raw JSON.
    var scope = scopeText(finding);
    if (scope) {
      rows.push(["Source scope / exception", scope]);
    }
    // Each condition fragment (product types, concentration limit, other
    // restrictions, warnings) is shown separately so the restriction is
    // traceable. A restriction requires evaluation; it is not a claim that a
    // concentration limit was exceeded.
    var condList = (finding.raw && finding.raw.conditions_list) || [];
    if (condList.length) {
      rows.push(["Conditions", "See restriction details below"]);
    } else if (finding.conditions) {
      rows.push(["Conditions", finding.conditions]);
    }
    rows.forEach(function (r) {
      el("dt", null, d).textContent = r[0];
      var dd = el("dd", null, d);
      if (r[0] === "Source URL" && isHttpUrl(url)) {
        dd.appendChild(safeLink(url));
      } else {
        dd.textContent = r[1];
      }
    });

    if (condList.length) {
      var cl = el("ol", "condition-list", f);
      condList.forEach(function (c) {
        var li = el("li", null, cl);
        var label = el("span", "condition-label", li);
        label.textContent = (c.label || c.field || "Condition") + ": ";
        li.appendChild(text(c.value || ""));
      });
    }
  }

  // Extract the source-row scope/exception text (the original 'Chemical name /
  // INN' when it carries a qualification beyond the substance's own name).
  function scopeText(rule) {
    var src = (rule.raw && rule.raw.source_row) || {};
    var name = (src["Chemical name / INN"] || "").trim();
    return name || null;
  }

  function sourceDetailEntries(res, rule) {
    var raw = rule.raw || {};
    var prov = [];
    prov.push(["Official reference number", raw.official_reference]);
    prov.push(["File-row reference", raw.row_reference]);
    prov.push(["Resolution method", rule.resolution]);
    prov.push(["Source filename", raw.source_file]);
    prov.push(["Source SHA-256", raw.source_sha256]);
    prov.push(["source_updated_at", rule.source_updated_at]);
    prov.push(["retrieved_at", raw.retrieved_at]);
    prov.push(["Status", rule.status]);
    prov.push(["Annex", rule.annex]);
    prov.push(["Regulation", rule.regulation]);
    prov.push(["Conditions", rule.conditions]);
    prov.push(["Effective date", rule.effective_date]);
    var scope = scopeText(rule);
    if (scope) {
      prov.push(["Source scope / exception", scope]);
    }
    return prov;
  }

  function renderCard(res) {
    var cls = classify(res);
    var card = el("article", "ingredient-card " + cls);
    var header = el("header", null, card);
    el("span", "ingredient-name", header).textContent = res.ingredient;
    var badgeLabel = cls === "FLAGGED" ? statusLabelFor(res, cls)
      : cls === "NOTFOUND" ? "not found / unresolved"
      : "unknown determination";
    var badgeCls = cls === "FLAGGED" ? "flag" : cls === "NOTFOUND" ? "notfound" : "unknown";
    header.appendChild(badgeClass(badgeCls, badgeLabel));

    var body = el("div", null, card);
    var bodyMsg;
    if (cls === "FLAGGED") {
      // Keep the original per-rule status in the evidence, but make the
      // prominent body statement match the interpreted headline derived from
      // the shared assessment (never an unconditional "banned" for a scoped /
      // exception-qualified prohibition). An independent, source-bound CosIng
      // determination takes priority over the supplementary REACH entry 70
      // finding, which is rendered separately below.
      var finding = res.prohibition_finding || null;
      var evalState = res.evaluation || null;
      if (finding === "unambiguous" || evalState === "prohibition_identified") {
        bodyMsg = "Banned/prohibited by CosIng (Annex II). No concentration "
          + "evaluation is applicable. The separate REACH entry 70 finding is "
          + "shown below as context-required (never a category violation).";
      } else if (finding === "scope_unresolved") {
        bodyMsg = (res.status === "banned"
                   ? "Prohibition scope unresolved. "
                   : "Exception requires review. ")
          + "Matched rule status \u201c" + (res.status || "unknown")
          + "\u201d is preserved as evidence; applicability to this product is "
          + "not evaluated.";
      } else if (evalState === "restriction_identified") {
        bodyMsg = "Restriction identified but not evaluated: no product "
          + "concentration/context was supplied, so no threshold violation has "
          + "been established (status \u201c" + (res.status || "unknown") + "\u201d).";
      } else if (evalState === "listing_identified") {
        bodyMsg = "Listing identified (status \u201c" + (res.status || "unknown")
          + "\u201d); contextual regulatory review required.";
      } else {
        // A retained, source-bound REACH Annex XVII entry 70 (D4/D5/D6) finding
        // is a restriction finding (identified-but-unevaluated on a label-only
        // check). State that a restriction was identified and that applicability
        // requires the missing product/concentration/date context — never that
        // it "applies" or "was triggered".
        var e70 = res.entry70;
        if (e70 && e70.state && e70.state !== "not_entry70_substance" && e70.state !== "source_not_active") {
          bodyMsg = "REACH Annex XVII entry 70 restriction identified (context "
            + "required). No supported restriction trigger is asserted: "
            + "applicability requires the missing product "
            + "concentration/product-type/date context, so no threshold "
            + "violation has been established.";
        } else {
          bodyMsg = (res.determination || "flag") + " (status " + (res.status || "unknown") + ")";
        }
      }
    } else if (cls === "NOTFOUND") {
      bodyMsg = "No matching record was found in this snapshot. Regulatory status is unknown.";
    } else {
      bodyMsg = "Recognised identity with no verified rule — unknown, never clean.";
    }
    body.appendChild(text(bodyMsg));

    var findings = el("div", "findings", body);
    var rules = res.rules || [];
    var linked = res.linked_rules || [];
    var memberships = res.membership || [];
    // Source-bound REACH Annex XVII entry 70 (D4/D5/D6): surface the
    // identified-but-unevaluated restriction (label-only check), clearly
    // separate from the CosIng findings. Never a category violation by itself.
    if (res.entry70) {
      var r70 = res.entry70;
      var note = el("div", "finding entry70", findings);
      var d70 = el("dl", null, note);
      var rows70 = [
        ["Annex XVII entry 70", r70.substance_short ? ("Substance " + r70.substance_short + " (D4/D5/D6)") : "—"],
        ["State", r70.state || "Not provided"],
        ["Reason", r70.reason || "Not provided"],
        ["Market threshold", r70.threshold || "Not provided"],
        ["Applicable branch", r70.branch || "Not provided"],
        ["Effective date", r70.effective_date || "Not provided"],
        ["Regulation", (r70.source && r70.source.regulation) || "Not provided"],
        ["Source URL", r70.source && r70.source.source_url ? "link" : "Not provided"],
        ["data_updated_at", res.data_updated_at],
      ];
      rows70.forEach(function (r) {
        el("dt", null, d70).textContent = r[0];
        var ddrow = el("dd", null, d70);
        if (r[0] === "Source URL" && isHttpUrl(r70.source && r70.source.source_url)) {
          ddrow.appendChild(safeLink(r70.source.source_url));
        } else {
          ddrow.textContent = r[1];
        }
      });
    }
    memberships.forEach(function (m) {
      findings.appendChild(text(
        "Group member of \u201c" + (m.group_name || m.group_id || "a group entry") +
        "\u201d (Annex " + (m.annex || "?") + ", ref " + (m.official_reference || m.reference_number || "?") +
        "). Group evidence is shown as linked \u2014 membership does not establish every condition applies."));
    });
    if (!rules.length && !linked.length && cls !== "FLAGGED") {
      // Unknown ≠ no rule applies. State absence of a verified record only.
      var note = cls === "NOTFOUND"
        ? "Not found does not mean unrestricted; regulatory status is unknown."
        : "No verified rule was found for this identity; regulatory status is unknown.";
      findings.appendChild(text(note));
    } else {
      rules.concat(linked).forEach(function (rule) {
        renderFinding(res, rule, findings);
        var toggle = el("button", "source-toggle", findings);
        toggle.type = "button";
        toggle.textContent = "Show source details";
        var details = el("div", "source-details", findings);
        details.hidden = true;
        dlFrom(sourceDetailEntries(res, rule), details);
        toggle.addEventListener("click", function () {
          details.hidden = !details.hidden;
          toggle.textContent = details.hidden ? "Show source details" : "Hide source details";
        });
      });
      if (linked.length && !rules.length) {
        findings.appendChild(text(
          "This finding comes from an explicit group membership (linked group rule), not "
          + "the member\u2019s own verified rule."));
      }
    }
    return card;
  }

  function renderCheck(data) {
    resultsList.replaceChildren();
    summaryEl.hidden = false;
    summaryEl.replaceChildren();

    var vh = el("h3", null, summaryEl);
    vh.textContent = "Verdict: " + (data.verdict || "—");
    vh.className = "verdict " + (data.clean ? "verdict-clean"
      : data.verdict === "FLAGGED" ? "verdict-flag" : "verdict-partial");
    el("p", null, summaryEl).textContent = verdictLabel(data.verdict, data.clean);

    var ul = el("ul", null, summaryEl);
    var counts = [
      ["Flagged", data.n_flagged],
      ["Banned", data.n_banned],
      ["Unverified / unknown", data.n_unverified],
    ];
    counts.forEach(function (c) {
      var li = el("li", null, ul);
      li.textContent = c[0] + ": " + c[1];
    });

    (data.results || []).forEach(function (res) {
      resultsList.appendChild(renderCard(res));
    });

    if (!(data.results || []).length && !data.no_ingredients) {
      resultsList.appendChild(text("No results returned."));
    }
  }

  function checkIngredients() {
    // Invalidate any prior findings and retire the superseded generation, then
    // capture the NEW generation for this request (so callbacks are guarded by
    // the generation that was current when the request was started).
    invalidateResults();
    var thisId = generation;
    var newController = new AbortController();
    controller = newController;

    var raw = ingredientsEl.value;
    var tokenizedLen = raw.replace(/\s+/g, " ").trim();

    // Local guardrails mirroring the API's documented limits. These return
    // before any request, so they must still clear prior findings (done above)
    // and never leave the button stuck disabled.
    if (raw.length > MAX_TEXT_LENGTH) {
      setStatus("Input exceeds the " + MAX_TEXT_LENGTH + "-character limit.", "err");
      return;
    }
    if (tokenizedLen === "" || /^[;,]+$/.test(tokenizedLen)) {
      setStatus("No ingredient tokens were parsed (empty or tokenless input).", "err");
      return;
    }

    checkBtn.disabled = true;
    setStatus("Checking…", null);

    var url = new URL("/v1/check", window.location.origin);
    url.searchParams.set("ingredients", raw);

    fetch(url, { signal: newController.signal })
      .then(async function (r) {
        var data = await r.json().catch(function () { return null; });
        // Re-check identity AFTER awaiting so a stale continuation cannot mutate.
        if (!isCurrent(thisId)) return;
        if (!r.ok) {
          var code = (data && data.error && data.error.code) || "error";
          var msg = (data && data.error && data.error.message) || ("Request failed (" + r.status + ")");
          setStatus("Request error [" + code + "]: " + msg, "err");
          return;
        }
        setStatus("Check complete.", "ok");
        renderCheck(data);
      })
      .catch(function (err) {
        if (!isCurrent(thisId)) return;   // stale failure must not change UI
        if (err && err.name === "AbortError") return;
        setStatus("Network failure: could not reach the API — " +
          (err && err.message ? err.message : "unknown error"), "err");
      })
      .finally(function () {
        // Re-enable only if this request is still the current one.
        if (isCurrent(thisId)) { checkBtn.disabled = false; }
      });
  }

  checkBtn.addEventListener("click", checkIngredients);

  refreshCount();
  loadHealth();
})();
