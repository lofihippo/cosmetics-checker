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
  var PRODUCTS = [
    "rust_inhibitor", "hair_product", "nail_product",
    "antidandruff_shampoo", "lash_brow_colour", "oral_product",
    "artificial_nail_system"
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
    ["version", "dt", "substances", "available", "missing"].forEach(function (k) {
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
    snapshotStatus.className = "status-banner ok";
    snapshotStatus.textContent =
      "Ready. Coverage: available lists above; missing lists are gaps, not clean results.";
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
             ready_for_use: null };
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
      ready_for_use: triToForm(rowEl.querySelector(".f-ready").value)
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
          f.body_area || f.professional_use !== null || f.ready_for_use !== null;
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
      default: return v || "Unknown";
    }
  }
  function badgeClass(v) {
    if (v === "exceeds_supported_limit") return "unevaluated";
    if (v === "within_supported_numeric_limit") return "clean";
    if (v === "requires_context") return "unknown";
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
      "Every ingredient result is reported with its own rule status, applicability, numeric comparison and unresolved requirements. A numeric \"within limit\" is not a compliance determination.";
    el("p", null, summaryEl).textContent =
      "data_updated_at: " + (data.data_updated_at || "Not provided");
    if (data.coverage) {
      el("p", null, summaryEl).textContent =
        "Coverage available: " + (data.coverage.available.length ? data.coverage.available.join(", ") : "None") +
        ". Missing (coverage gaps, not clean): " + (data.coverage.missing.length ? data.coverage.missing.join(", ") : "None") + ".";
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
    var rows = [
      ["Resolution / identity", (e.resolved_name || "unresolved") + " (" + (e.resolution || "?") + ")"],
      ["In dataset", e.is_in_dataset ? "Yes" : "No"],
      ["Determination", e.determination || "unknown"],
      ["Rule status", e.rule_status || "Not provided"],
      ["Annex", e.annex || "Not provided"],
      ["Applicability", e.applicable_context || "Not assessed"],
      ["Submitted value", e.submitted_value != null ? e.submitted_value : "Not supplied"],
      ["Unit / basis", (e.unit || "Not supplied") + " / " + (e.basis || "Not supplied")],
      ["Cap", e.cap || "Not applicable"],
      ["Numeric comparison", e.numeric_comparison || "Not evaluated"],
      ["Prohibition classification", e.prohibition_finding || "Not determined"],
    ];
    body.appendChild(dlFrom(rows, null));

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

    // Provenance (machine evidence): source reference, row, scope/exception.
    var prov = e.provenance || {};
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
      setStatus("Loaded example (illustrative, not a recommendation).", null);
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
