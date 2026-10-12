"""Streamlit paste-label demo for the Phase 0 eu_lint.py prototype.

Run with:
    streamlit run app.py
"""

import json
from pathlib import Path

import streamlit as st

import eu_lint

st.set_page_config(page_title="EU Ingredient Linter (Phase 0)", layout="wide")

st.title("EU Ingredient Linter — Phase 0 prototype")
st.caption("Regulatory information, not legal advice.")

default_label = "water, red 40, titanium dioxide, bht"
label = st.text_area(
    "Paste an ingredient list",
    value=default_label,
    height=150,
    help="One ingredient per line, or comma/semicolon separated.",
)

fmt = st.radio("Output format", ["table", "json"], horizontal=True)

if st.button("Check"):
    dataset = eu_lint.Dataset(Path(eu_lint.DEFAULT_DATASET))
    result = eu_lint.check_label(label, dataset)

    if fmt == "json":
        st.json(json.loads(eu_lint.render_json(result)))
    else:
        # Compact per-ingredient table.
        st.write("## Results")
        rows = [
            {
                "Ingredient": r["ingredient"],
                "Status": eu_lint.status_label(r),
                "Determination": (
                    r.get("determination", "unknown")
                    if r["is_in_dataset"]
                    else "not_in_dataset"
                ),
                "Annex": r.get("annex") or "—",
                "Regulation": r.get("regulation") or "—",
                "Source": r.get("source_url") or "—",
                "data_updated_at": r.get("data_updated_at") or "—",
            }
            for r in result["results"]
        ]
        st.table(rows)
        st.write(f"**Ingredients:** {result['n_ingredients']}")
        st.write(f"**Flagged:** {result['n_flagged']}")
        st.write(f"**Not in dataset:** {result['n_not_in_dataset']}")
        st.write(f"**Unverified identity:** {result['n_unverified_identity']}")

    if result["clean"]:
        st.success("No EU ban/restriction/condition triggered; all ingredients "
                   "resolved to a verified determination.")
    elif result["n_flagged"]:
        st.warning(
            "FLAGGED: an EU ban/restriction/conditional rule was triggered. "
            "See details above."
        )
    else:
        st.warning(
            "NOT-FULLY-VERIFIED: one or more ingredients are not in the "
            "dataset or have no verified EU determination. This is NOT a "
            "clean result."
        )

st.info(
    "A clean result means every ingredient resolved to a verified "
    "determination and no EU ban/restriction/condition was triggered. "
    "It is NOT a safety certificate or legal advice. The Phase 1 "
    "eu-reg-lists package replaces this dataset with official export files."
)
