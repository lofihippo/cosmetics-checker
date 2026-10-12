"""Example: check a realistic cosmetics label + a batch against the Annex II registry.

Run from the repo root:

    python packages/eu-ingredient-linter/examples/usage.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "packages" / "eu-ingredient-linter"))

from eu_reg_lists import load  # noqa: E402
from eu_ingredient_linter import batch_report, ingredient_report  # noqa: E402

REG = ROOT / "data" / "processed" / "eu_reg_lists.sqlite"


def main() -> None:
    reg = load(REG)

    label = "Aqua, Benzene, Benzidine, Zinc pyrithione, Alpine lavender extract"
    report = ingredient_report(label, reg)
    print("== Single label ==")
    print(f"  verdict={report['verdict']} n={report['n_ingredients']} "
          f"flagged={report['n_flagged']} banned={report['n_banned']} "
          f"unverified={report['n_unverified']}")
    print(f"  coverage: Annex II present={report['coverage']['has_annex_ii']}; "
          f"missing={report['coverage']['missing']}")
    for r in report["results"]:
        ref = ""
        if r["rules"] and r["rules"][0].get("raw", {}).get("official_reference") is not None:
            ref = f"  [ref #{r['rules'][0]['raw']['official_reference']} " \
                  f"row={r['rules'][0]['raw'].get('row_reference')}]"
        print(f"    {r['ingredient']:<34} det={r['determination']}{ref}")
    print(f"  {report['disclaimer']}")

    print()
    print("== Batch (2 products) ==")
    batch = batch_report(
        ["Aqua, Benzene", "Benzidine, TotallyFakeIngredient123"], reg)
    print(f"  items={batch['n_items']} ingredients={batch['total_ingredients']} "
          f"flagged={batch['total_flagged']} banned={batch['total_banned']} "
          f"unverified={batch['total_unverified']}")
    print(f"  coverage: Annex II present={batch['coverage']['has_annex_ii']}")


if __name__ == "__main__":
    main()
