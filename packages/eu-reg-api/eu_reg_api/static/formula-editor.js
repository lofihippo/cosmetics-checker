"use strict";

(function () {
  // ---- elements ----
  var $ = function (id) { return document.getElementById(id); };
  var rowsEl = $("rows");
  var addRowBtn = $("add-row");
  var submitBtn = $("submit");
  var resultsMessage = $("results-message");
  var summaryEl = $("summary");
  var resultsList = $("formula-results");
  var snapshotStatus = $("snapshot-status");

  // Explicit, documented canonical options. These mirror the API's accepted
  // values; the editor sends canonical values (no fuzzy/substring inference in
  // the browser). They are NOT a duplicate evaluator — the API stays authoritative.
  var UNITS = ["%"];
  var BASES = ["ready_for_use", "after_mixing"];
  // Product types accepted by the bounded Annex III evaluator plus the two
  // explicit REACH Annex XVII entry 70 cosmetic branches (wash-off / other
  // cosmetic). The editor sends canonical values; the API stays authoritative.
  var PRODUCTS = [
    "rust_inhibitor", "hair_product", "nail_product",
    "antidandruff_shampoo", "lash_brow_colour", "oral_product",
    "artificial_nail_system",
    "wash-off", "leave-on", "other_cosmetic"
  ];

  // ---- request lifecycle ----
  // generation increments on EVERY invalidation (field edit, row add/remove,
  // example change) and on every new submit. A request id is current only while
  // its generation matches, so a request that ignores AbortSignal can never
  // overwrite newer state. Only the newest generation may touch the UI.
  var generation = 0;
  var controller = null;

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
  function isHttpUrl(u) { return typeof u === "string" && /^https?:\/\//i.test(u); }
  function safeLink(url, label) {
    label = label || url;
    var a = document.createElement("a");
    a.className = "citation-link"; a.textContent = label; a.href = url;
    a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  function setStatus(value, kind) {
    resultsMessage.className = "status-banner" + (kind ? " " + kind : "");
    resultsMessage.replaceChildren();
    if (value) resultsMessage.appendChild(text(value));
  }
  function clearStatus() {
    resultsMessage.className = "status-banner";
    resultsMessage.replaceChildren();
  }
  function isCurrent(id) { return id === generation; }

  // ---- snapshot / health ----
  function setText(id, value) {
    $("snap-" + id).textContent = value == null || value === "" ? "Not provided" : String(value);
  }
  function markHealthUnavailable(msg) {
    ["version", "dt", "substances", "available", "missing", "not-included", "annex17"].forEach(function (k) {
      setText(k, "Unavailable");
    });
    snapshotStatus.className = "status-banner err";
    snapshotStatus.textContent = msg;
  }
  function healthy(h) {
    return h && typeof h === "object" && h.status === "ready" && !!h.coverage &&
      h.data_updated_at !== undefined && h.count_substances !== undefined &&
      Array.isArray(h.coverage.available) && Array.isArray(h.coverage.missing);
  }
  function applyHealth(h) {
    setText("version", h.version); setText("dt", h.data_updated_at);
    setText("substances", h.count_substances);
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
    fetch("/health").then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (h) {
      if (!healthy(h)) throw new Error("Health response missing expected fields.");
      applyHealth(h);
    }).catch(function (err) {
      markHealthUnavailable("Could not load snapshot: " +
        (err && err.message ? err.message : "network error"));
    });
  }

  // ---- ingredient rows ----
  function selectOptions(values) {
    var html = '<option value="">— not supplied —</option>';
    values.forEach(function (v) { html += '<option value="' + v + '">' + v + "</option>"; });
    return html;
  }
  // Tri-state boolean select: "" -> absent/null, "true" -> true, "false" -> false.
  function triSelect(label, cls, noLabel) {
    var wrap = el("label", null, null);
    var span = el("span", null, wrap);
    span.textContent = label;
    var sel = el("select", cls, wrap);
    sel.innerHTML = '<option value="">— not supplied —</option>' +
      '<option value="true">Yes</option>' +
      '<option value="false">' + (noLabel || "No") + '</option>';
    wrap.appendChild(sel);
    return wrap;
  }
  function triToForm(value) {
    // Keep the three states distinct: "" -> absent, "true" -> true, "false" -> false.
    if (value === "true") return true;
    if (value === "false") return false;
    return null;
  }
  function triToSelect(value) {
    if (value === true) return "true";
    if (value === false) return "false";
    return "";
  }
  function blankRow() {
    // A new/reset row starts with context NOT supplied: unit, basis, product
    // context, body area and both optional booleans are all absent so the API
    // (not the browser) decides what is required. It is never assumed.
    return { ingredient: "", percent: "", unit: "", basis: "",
             product_type: "", body_area: "", professional_use: null,
             ready_for_use: null, assessment_date: "" };
  }
  function rowToForm(rowEl) {
    return {
      ingredient: rowEl.querySelector(".f-ingredient").value.trim(),
      percent: rowEl.querySelector(".f-percent").value.trim(),
      unit: rowEl.querySelector(".f-unit").value || null,
      basis: rowEl.querySelector(".f-basis").value || null,
      product_type: rowEl.querySelector(".f-product").value || null,
      body_area: rowEl.querySelector(".f-body-area").value.trim() || null,
      professional_use: triToForm(rowEl.querySelector(".f-professional").value),
      ready_for_use: triToForm(rowEl.querySelector(".f-ready").value),
      assessment_date: rowEl.querySelector(".f-assessment-date").value.trim() || null
    };
  }
  function setRowValues(rowEl, values) {
    rowEl.querySelector(".f-ingredient").value = values.ingredient || "";
    rowEl.querySelector(".f-percent").value = values.percent || "";
    if (values.unit) rowEl.querySelector(".f-unit").value = values.unit;
    if (values.basis) rowEl.querySelector(".f-basis").value = values.basis;
    if (values.product_type) rowEl.querySelector(".f-product").value = values.product_type;
    rowEl.querySelector(".f-body-area").value = values.body_area || "";
    rowEl.querySelector(".f-professional").value = triToSelect(values.professional_use);
    rowEl.querySelector(".f-ready").value = triToSelect(values.ready_for_use);
    rowEl.querySelector(".f-assessment-date").value = values.assessment_date || "";
  }

  function addIngredientRow(values) {
    values = values || blankRow();
    var row = el("div", "ingredient-row", rowsEl);
    var header = el("header", null, row);
    el("span", "row-title", header).textContent = "Ingredient " + (rowsEl.children.length);
    var remove = el("button", "remove-row", header);
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", "Remove ingredient row");

    var grid = el("div", "field-grid", row);
    // Ingredient
    var ingWrap = el("label", null, grid);
    el("span", null, ingWrap).textContent = "Ingredient name or identifier";
    var ing = el("input", "f-ingredient", ingWrap);
    ing.placeholder = "e.g. Toluene, 108-88-3";
    ing.autocomplete = "off"; ing.spellcheck = false;
    // Percent (sent as a decimal string; never via Number()/parseFloat).
    var pctWrap = el("label", null, grid);
    el("span", null, pctWrap).textContent = "Concentration (decimal)";
    var pct = el("input", "f-percent", pctWrap);
    pct.type = "text"; pct.inputMode = "decimal";
    pct.placeholder = "e.g. 25 or 25.0000000000000001";
    pct.autocomplete = "off"; pct.spellcheck = false;
    // Unit
    var unitWrap = el("label", null, grid);
    el("span", null, unitWrap).textContent = "Concentration unit";
    var unit = el("select", "f-unit", unitWrap);
    unit.innerHTML = selectOptions(UNITS);
    // Basis
    var basisWrap = el("label", null, grid);
    el("span", null, basisWrap).textContent = "Concentration basis";
    var basis = el("select", "f-basis", basisWrap);
    basis.innerHTML = selectOptions(BASES);
    // Product context
    var prodWrap = el("label", null, grid);
    el("span", null, prodWrap).textContent = "Product context";
    var prod = el("select", "f-product", prodWrap);
    prod.innerHTML = selectOptions(PRODUCTS);
    // Optional context
    var ctx = el("div", "optional-context", row);
    ctx.appendChild(ctxCheck("Body area", "f-body-area", "text"));
    ctx.appendChild(boolSelect("Professional use", "f-professional"));
    ctx.appendChild(boolTri("Ready-for-use", "f-ready"));
    ctx.appendChild(ctxCheck("Assessment date (YYYY-MM-DD)", "f-assessment-date", "text"));

    setRowValues(row, values);

    // Any edit on the row invalidates pending requests and clears stale results.
    row.querySelectorAll("input, select").forEach(function (ctrl) {
      ctrl.addEventListener("input", function () {
        invalidateResults(ctrl.closest(".ingredient-row") === row);
        clearStatus();
      });
    });
    remove.addEventListener("click", function () {
      // Remove the row; if it is the last remaining, keep one blank row.
      var wasPending = controller;
      if (row.parentNode === rowsEl) rowsEl.removeChild(row);
      if (rowsEl.children.length === 0) addIngredientRow(blankRow());
      invalidateResults(true);
      clearStatus();
      renumberRows();
      if (wasPending) setStatus("Request cancelled (row removed).", "err");
    });
    return row;
  }

  function ctxCheck(label, cls, type) {
    var wrap = el("label", null, null);
    var span = el("span", null, wrap);
    span.textContent = label;
    var input = el("input", cls, wrap);
    input.type = type; input.placeholder = "e.g. face"; input.autocomplete = "off";
    wrap.appendChild(input);
    return wrap;
  }
  function boolSelect(label, cls) {
    // Both optional booleans expose all three states: absent, Yes, No.
    return triSelect(label, cls);
  }
  function boolTri(label, cls) {
    return triSelect(label, cls, "No (contradictory for ready-for-use)");
  }
  function renumberRows() {
    var i = 1;
    rowsEl.querySelectorAll(".ingredient-row .row-title").forEach(function (t) {
      t.textContent = "Ingredient " + i++;
    });
  }

  // ---- invalidation ----
  // Retire the current generation (so pending requests cannot mutate the UI),
  // abort any in-flight request, clear results and restore the submit button.
  function invalidateResults() {
    generation++;
    if (controller) { controller.abort(); controller = null; }
    resultsList.replaceChildren();
    summaryEl.hidden = true;
    summaryEl.replaceChildren();
    submitBtn.disabled = false;
  }

  // ---- building the request ----
  function collectFormula() {
    var rows = [];
    var seen = {};
    for (var i = 0; i < rowsEl.children.length; i++) {
      var f = rowToForm(rowsEl.children[i]);
      var rowNum = i + 1;
      // Only a completely empty row may be ignored. If any field was supplied
      // but the name/identifier is missing, the formula is incomplete — reject
      // with a clear, row-specific error rather than silently dropping it.
      if (!f.ingredient) {
        var supplied = f.percent !== "" || f.unit || f.basis || f.product_type ||
          f.body_area || f.professional_use !== null || f.ready_for_use !== null ||
          f.assessment_date;
        if (!supplied) continue;      // fully blank row -> ignored
        throw new Error("Row " + rowNum + ": ingredient name/identifier is missing " +
          "but other fields are supplied. Complete the row or clear it.");
      }
      // Keep duplicate-resolved handling to the authoritative API, but catch a
      // trivially obvious duplicate name client-side for better feedback.
      var key = f.ingredient.toLowerCase();
      if (seen[key]) {
        throw new Error("Ingredient '" + f.ingredient + "' appears more than once.");
      }
      seen[key] = true;
      var entry = { ingredient: f.ingredient };
      if (f.percent !== "") entry.percent = f.percent;   // blank -> absent, NOT 0
      if (f.unit) entry.unit = f.unit;
      if (f.basis) entry.basis = f.basis;
      if (f.product_type) entry.product_type = f.product_type;
      if (f.body_area) entry.body_area = f.body_area;
      if (f.professional_use !== null) entry.professional_use = f.professional_use;
      if (f.ready_for_use !== null) entry.ready_for_use = f.ready_for_use;
      if (f.assessment_date) entry.assessment_date = f.assessment_date;
      rows.push(entry);
    }
    if (rows.length === 0) {
      throw new Error("Add at least one non-empty ingredient.");
    }
    return { ingredients: rows };
  }

  // ---- submit ----
  function submitFormula() {
    invalidateResults();
    var thisId = generation;
    var newController = new AbortController();
    controller = newController;

    var payload;
    try {
      payload = collectFormula();
    } catch (err) {
      setStatus(err.message, "err");
      return;
    }

    submitBtn.disabled = true;
    setStatus("Evaluating…", null);

    fetch("/v1/check-formula", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: newController.signal
    }).then(async function (r) {
      var data = await r.json().catch(function () { return null; });
      if (!isCurrent(thisId)) return;
      if (!r.ok) {
        var code = (data && data.error && data.error.code) || "error";
        var msg = (data && data.error && data.error.message) || ("Request failed (" + r.status + ")");
        setStatus("Request error [" + code + "]: " + msg, "err");
        return;
      }
      var summary = data.results;
      setStatus("Evaluation complete.", "ok");
      renderResults(data);
    }).catch(function (err) {
      if (!isCurrent(thisId)) return;
      if (err && err.name === "AbortError") return;   // cancelled; don't show stale
      setStatus("Network failure: could not reach the API — " +
        (err && err.message ? err.message : "unknown error"), "err");
    }).finally(function () {
      if (isCurrent(thisId)) submitBtn.disabled = false;
    });
  }

  // ---- rendering ----
  function verdictLabel(v) {
    switch (v) {
      case "exceeds_supported_limit": return "Above the supported numeric limit.";
      case "within_supported_numeric_limit": return "Within the supported numeric limit (numeric comparison only — not clean/compliant).";
      case "requires_context": return "Requires more context (missing/unsupported unit, basis, or product context).";
      case "requires_manual_review": return "Requires manual review (complex rule, cross-annex exception, or unresolved applicability).";
      case "unknown_ingredient": return "Ingredient not found in the snapshot — regulatory status unknown.";
      case "entry70_supported_trigger": return "REACH Annex XVII entry 70 supported trigger (restriction identified; not a compliance certificate).";
      case "entry70_within_threshold": return "REACH Annex XVII entry 70: within numeric threshold (comparison only — not clean/compliant).";
      case "entry70_missing_context": return "REACH Annex XVII entry 70: restriction identified, context required.";
      case "entry70_transition": return "REACH Annex XVII entry 70: before the branch effective date (not yet applicable).";
      case "entry70_unsupported": return "REACH Annex XVII entry 70: use not a supported cosmetic branch (not evaluated).";
      default: return v || "Unknown";
    }
  }
  function badgeClass(v) {
    if (v === "exceeds_supported_limit" || v === "entry70_supported_trigger") return "unevaluated";
    if (v === "within_supported_numeric_limit" || v === "entry70_within_threshold") return "clean";
    if (v === "requires_context" || v === "entry70_missing_context"
        || v === "entry70_transition" || v === "entry70_unsupported") return "unknown";
    if (v === "requires_manual_review") return "unknown";
    return "notfound";
  }
  function badgeLabel(v) { return v ? v.replace(/_/g, " ") : "unknown"; }

  function renderResults(data) {
    resultsList.replaceChildren();
    summaryEl.hidden = false;
    summaryEl.replaceChildren();
    var h = el("h3", null, summaryEl);
    h.textContent = "Formula evaluation";
    el("p", null, summaryEl).textContent =
      "Review each ingredient’s comparison and unresolved requirements.";
    el("p", null, summaryEl).textContent =
      "data_updated_at: " + (data.data_updated_at || "Not provided");
    if (data.coverage) {
      el("p", null, summaryEl).textContent =
        "Coverage available: " + (data.coverage.available.length ? data.coverage.available.join(", ") : "None") +
        ". Missing (coverage gaps, not clean): " + (data.coverage.missing.length ? data.coverage.missing.join(", ") : "None") +
        ". Not included (deliberately deferred): " + (data.coverage.not_included && data.coverage.not_included.length ? data.coverage.not_included.join(", ") : "None") + ".";
      var a17 = (data.coverage.annex17_entries || []);
      var a17b = (data.coverage.annex17_branches || []);
      if (a17.length) {
        el("p", null, summaryEl).textContent =
          "REACH Annex XVII (entry 70) covered: entry " + a17.join(", ") +
          " cosmetic branches: " + (a17b.length ? a17b.join(", ") : "n/a") +
          ". Not full Annex XVII or REACH coverage.";
      }
    }
    (data.evaluations || []).forEach(function (e) {
      resultsList.appendChild(renderCard(e));
    });
  }

  function renderCard(e) {
    var card = el("article", "eval-card " + badgeClass(e.verdict), null);
    var header = el("header", null, card);
    el("span", "eval-name", header).textContent = e.ingredient;
    var badge = el("span", "badge " + badgeClass(e.verdict), header);
    badge.textContent = badgeLabel(e.verdict);
    var verdictLine = el("p", "verdict-note", card);
    verdictLine.textContent = verdictLabel(e.verdict);
    verdictLine.className = "note";

    var body = el("div", "eval-block", card);
    // Identity/context fields are always relevant regardless of source.
    var identityRows = [
      ["Resolution / identity", (e.resolved_name || "unresolved") + " (" + (e.resolution || "?") + ")"],
      ["In dataset", e.is_in_dataset ? "Yes" : "No"],
      ["Determination", e.determination || "unknown"],
      ["Submitted value", e.submitted_value != null ? e.submitted_value : "Not supplied"],
      ["Unit / basis", (e.unit || "Not supplied") + " / " + (e.basis || "Not supplied")],
      ["Applicability", e.applicable_context],
    ];
    body.appendChild(dlFrom(identityRows, null));

    // CosIng-only source-rule evaluation. These rows are rendered ONLY when a
    // CosIng source rule was actually identified (a CosIng rule is present in
    // rules/linked_rules or the per-rule status/annex fields are populated).
    // When the assessment is the REACH entry 70 finding (e.g. a D4/D5/D6
    // identity with no CosIng rule), the generic "Numeric comparison: Not
    // evaluated" / "Prohibition classification: Not determined" rows are NOT
    // shown — they would mis-describe the populated entry-70 assessment. The
    // entry-70 block below carries its own source-specific comparison/citation.
    var hasCosing = !!(e.rules || []).length || !!(e.linked_rules || []).length ||
      !!e.rule_status || !!e.annex;
    var cosingRows = [
      ["Rule status", e.rule_status],
      ["Annex", e.annex],
      ["Cap", e.cap],
      // A CosIng rule was identified: the numeric comparison is genuinely
      // "not evaluated" when the required context is missing (never a clean
      // claim). This row is NOT rendered for an entry-70-only finding (the
      // entry-70 block below carries its actual comparison).
      ["Numeric comparison", e.numeric_comparison || (hasCosing ? "Not evaluated" : null)],
      ["Prohibition classification", e.prohibition_finding],
    ].filter(function (r) { return r[1] != null && r[1] !== ""; });
    if (hasCosing && cosingRows.length) {
      var ch = el("h4", "source-heading", body);
      ch.textContent = "CosIng source-rule assessment";
      body.appendChild(dlFrom(cosingRows, null));
    }

    // Unresolved requirements / warnings, shown prominently.
    if ((e.unresolved_requirements && e.unresolved_requirements.length) ||
        (e.reasons && e.reasons.length)) {
      var ul = el("ul", "unresolved-list", body);
      var head = el("li", "unresolved-heading", ul);
      head.textContent = "Unresolved requirements / notes";
      (e.unresolved_requirements || []).forEach(function (r) {
        var li = el("li", null, ul); li.textContent = r;
      });
    }

    // Every identified rule, with its own status + citation + source text.
    (e.rules || []).forEach(function (rule) {
      var rb = el("div", "finding", body);
      ruletBlock(rb, rule, e);
    });

    // Explicit source-backed group membership + linked rules (never merged).
    (e.membership || []).forEach(function (m) {
      var mm = el("p", "note", body);
      mm.textContent = "Group member of \u201c" + (m.group_name || m.group_id || "a group") +
        "\u201d (Annex " + (m.annex || "?") + ", ref " + (m.official_reference || m.reference_number || "?") +
        ", " + (m.source_kind || "") + (m.row_reference || "") + "). Linked evidence shown below; " +
        "membership does not establish that every condition applies.";
    });
    (e.linked_rules || []).forEach(function (rule) {
      var rb = el("div", "finding member-linked", body);
      ruletBlock(rb, rule, e);
    });
    if ((e.linked_rules || []).length && !(e.rules || []).length) {
      var ln = el("p", "note", body);
      ln.textContent = "This finding comes from an explicit group membership (linked group rule), "
        + "not the member\u2019s own verified rule.";
    }

    // Source-bound REACH Annex XVII entry 70 (D4/D5/D6) evidence, rendered as a
    // distinct source-specific block. It is separate evidence and never replaces
    // the CosIng findings above. The loaded-snapshot timestamp and the actual
    // entry-70 comparison (threshold/operator/branch/effective date) plus the
    // regulation and source URL are shown here, labelled as REACH entry 70.
    var e70 = e.entry70;
    if (e70) {
      var e70Title = el("h4", "source-heading", body);
      e70Title.textContent = "REACH Annex XVII entry 70 assessment";
      var e70b = el("div", "finding entry70", body);
      var dh = el("dl", null, e70b);
      var s = e70.source || {};
      // Readable labels for machine states/branches without changing the value
      // (the authoritative value is still available as evidence).
      function stateLabel(st) {
        return ({ "applicable_exceeds": "applicable (exceeds threshold)",
                  "applicable_within": "applicable (within threshold)",
                  "transition_not_effective": "before branch effective date",
                  "missing_context": "context required",
                  "unsupported_use": "unsupported cosmetic use",
                  "not_entry70_substance": "not an entry 70 substance",
                  "source_not_active": "entry 70 source not active" }[st] || st);
      }
      function branchLabel(b) {
        return ({ "washoff_cosmetic": "wash-off cosmetic",
                  "other_cosmetic": "other (non-wash-off) cosmetic" }[b] || b);
      }
      [["REACH entry 70 substance", e70.substance_short],
       ["State", stateLabel(e70.state)],
       ["Reason", e70.reason],
       ["Market threshold", e70.threshold],
       ["Operator", e70.operator],
       ["Branch", branchLabel(e70.branch)],
       ["Effective date", e70.effective_date],
       ["Assessment date", e70.assessment_date],
       ["Submitted concentration", e70.submitted_percent],
       ["Submitted product type", e70.product_type],
       ["Concentration basis", e70.basis],
       ["Ready for use", e70.ready_for_use],
       ["Regulation", s.regulation],
       ["CELEX", s.celex],
       ["Document SHA-256", s.document_sha256],
       ["Source URL", null],
       ["data_updated_at", e70.data_updated_at]
      ].forEach(function (entry) {
        var dt = el("dt", null, dh); dt.textContent = entry[0];
        var dd = el("dd", null, dh);
        if (entry[0] === "Source URL") {
          if (isHttpUrl(s.source_url)) dd.appendChild(safeLink(s.source_url));
          else dd.textContent = "Not provided";
        } else if (entry[1] == null || entry[1] === "") {
          dd.textContent = "Not provided";
        } else {
          dd.textContent = String(entry[1]);
        }
      });
      (e70.unresolved_requirements || []).forEach(function (u) {
        var un = el("li", "note", e70b); un.textContent = "Entry 70 unresolved: " + u;
      });
    }

    // CosIng provenance (machine evidence): source reference, row, scope/exception.
    // Rendered only for a CosIng source-rule assessment — never as empty generic
    // rows that would mis-describe an entry-70-only finding. The entry-70 block
    // above carries its own regulation/source URL/loaded-snapshot timestamp.
    var prov = e.provenance || {};
    var hasProv = ["official_reference", "row_reference", "source_file",
                   "source_sha256", "retrieved_at", "source_updated_at",
                   "data_updated_at", "regulation"].some(function (k) {
                     return prov[k] != null && prov[k] !== "";
                   });
    if ((e.rules || []).length || (e.linked_rules || []).length || hasProv) {
      var precision = [
        ["Official reference", prov.official_reference],
        ["File-row reference", prov.row_reference],
        ["Source file", prov.source_file],
        ["Source SHA-256", (prov.source_sha256 || "").slice(0, 16) + (prov.source_sha256 ? "…" : "")],
        ["retrieved_at", prov.retrieved_at],
        ["source_updated_at", prov.source_updated_at],
        ["data_updated_at", prov.data_updated_at],
        ["regulation", prov.regulation],
      ];
      body.appendChild(dlFrom(precision, null));
    }
    return card;
  }

  function ruletBlock(container, rule, e) {
    var d = el("dl", null, container);
    var url = rule.source_url;
    var raw = rule.raw || {};
    var src = raw.source_row || {};
    var scope = (src["Chemical name / INN"] || "").trim();
    var off = raw.official_reference != null ? raw.official_reference : (raw.reference_number || null);
    var entries = [
      ["Status", rule.status],
      ["Annex", rule.annex],
      ["Regulation", rule.regulation],
      ["Conditions", rule.conditions],
      ["Source URL", null],
      ["Source scope / exception", scope || null],
      ["Official reference", off != null ? off : null],
      ["File-row reference", raw.row_reference || null],
    ];
    var linkDd = null;
    entries.forEach(function (entry) {
      var dt = el("dt", null, d); dt.textContent = entry[0];
      var dd = el("dd", null, d);
      if (entry[0] === "Source URL") { linkDd = dd; return; }
      if (entry[1] == null || entry[1] === "") dd.textContent = "Not provided";
      else dd.textContent = String(entry[1]);
    });
    if (linkDd) {
      if (isHttpUrl(url)) linkDd.appendChild(safeLink(url));
      else linkDd.textContent = "Not provided";
    }
  }

  // ---- examples ----
  var EXAMPLES = {
    "supported": [
      { ingredient: "Toluene", percent: "20", unit: "%", basis: "ready_for_use", product_type: "nail_product" }
    ],
    "missing-context": [
      { ingredient: "Toluene", percent: "20", unit: "%" }
    ],
    "benzene": [
      { ingredient: "Benzene", percent: "1", unit: "%" }
    ],
    "hydroquinone": [
      { ingredient: "Hydroquinone", percent: "0.01", unit: "%", basis: "after_mixing", product_type: "artificial_nail_system" }
    ],
    "unknown": [
      { ingredient: "TotallyFakeIngredientX", percent: "1", unit: "%" }
    ],
    "entry70-washoff": [
      { ingredient: "Cyclopentasiloxane", percent: "0.5", unit: "%",
        basis: "ready_for_use", product_type: "wash-off",
        assessment_date: "2028-01-01" }
    ]
  };
  document.querySelectorAll(".example-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var rows = EXAMPLES[btn.getAttribute("data-example")];
      if (!rows) return;
      invalidateResults(); clearStatus();
      clearRows();
      rows.forEach(function (r) { addIngredientRow(r); });
      renumberRows();
      setStatus("Example loaded.", null);
    });
  });

  function clearRows() { rowsEl.replaceChildren(); }

  // ---- wire up ----
  addRowBtn.addEventListener("click", function () {
    addIngredientRow(blankRow());
    invalidateResults(); clearStatus();
    renumberRows();
  });
  submitBtn.addEventListener("click", submitFormula);

  // Start with one blank row.
  addIngredientRow(blankRow());
  loadHealth();
})();
