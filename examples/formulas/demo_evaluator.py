"""Runnable demonstration of the bounded Annex III formula evaluator.

Each scenario is evaluated independently (a single formula cannot repeat the
same ingredient with a different concentration — duplicates are rejected, never
silently combined). This script exercises the Part B acceptance scenarios against
the real processed snapshot.

Run:
    python examples/formulas/demo_evaluator.py
"""

from pathlib import Path

from eu_reg_lists import load
from eu_ingredient_linter import evaluate_formula

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "data" / "processed" / "eu_reg_lists.sqlite"

# A single-ingredient formula helper.
def formula(name, **context):
    return {"ingredients": [{"ingredient": name, **context}]}


def show(label, formula):
    rep = evaluate_formula(formula, reg)
    e = rep["evaluations"][0]
    print(f"{label}")
    print(f"    ingredient : {e['ingredient']}")
    print(f"    verdict    : {e['verdict']}")
    print(f"    rule_status: {e['rule_status']} annex={e['annex']}")
    print(f"    context    : {e['applicable_context']}")
    print(f"    numeric    : {e['numeric_comparison']}")
    print(f"    resolution : {e['resolution']} -> {e['resolved_name']}")
    if e["source_rule"].get("reference_number") is not None:
        print(f"    source ref : {e['source_rule']['reference_number']} "
              f"{e['source_rule']['name']} ({e['source_rule']['regulation']})")
    for reason in e["reasons"]:
        print(f"    reason     : {reason}")
    print()


reg = load(REG)

show("Below limit (Sodium nitrite 0.1% <= 0.2%)",
     formula("Sodium nitrite", percent=0.1, unit="%", basis="ready_for_use",
             product_type="rust_inhibitor"))
show("Exactly at limit (Sodium nitrite 0.2% == 0.2%)",
     formula("Sodium nitrite", percent=0.2, unit="%", basis="ready_for_use",
             product_type="rust_inhibitor"))
show("Above limit (Sodium nitrite 0.5% > 0.2%)",
     formula("Sodium nitrite", percent=0.5, unit="%", basis="ready_for_use",
             product_type="rust_inhibitor"))
show("Missing context (no product type)",
     formula("Sodium nitrite", percent=0.1, unit="%", basis="ready_for_use"))
show("Mismatched concentration basis (after_mixing vs ready_for_use)",
     formula("Sodium nitrite", percent=0.1, unit="%", basis="after_mixing",
             product_type="rust_inhibitor"))
show("Complex rule (thioglycolic acid) -> manual review",
     formula("Thioglycolic acid and its salts", percent=3, unit="%",
             basis="ready_for_use", product_type="hair_product"))
show("Cross-annex exception (Hydroquinone) -> manual review",
     formula("Hydroquinone", percent=0.01, unit="%", basis="after_mixing",
             product_type="artificial_nail_system"))
show("Unknown ingredient",
     formula("TotallyFakeIngredientX", percent=1, unit="%"))
show("No concentration supplied -> unevaluated",
     formula("Selenium sulfide", product_type="antidandruff_shampoo"))

print("Regulatory information, not legal advice.")
