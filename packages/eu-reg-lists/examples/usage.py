#!/usr/bin/env python3
"""Command-line example for the eu-reg-lists package.

Usage:
    python packages/eu-reg-lists/examples/usage.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repo root
PACKAGE = ROOT / "packages" / "eu-reg-lists"
sys.path.insert(0, str(PACKAGE))

import eu_reg_lists as erl  # noqa: E402


def main() -> None:
    reg = erl.load(ROOT / "data" / "processed" / "eu_reg_lists.sqlite")
    print(f"loaded {len(reg)} substances "
          f"(data_updated_at={reg.data_updated_at})")

    print("\n-- search by name / CAS / E-number / alias --")
    for q in ["Red 40", "25956-17-6", "E129", "lily aldehyde"]:
        hits = reg.search(q)
        names = [f"{s.name} ({s.determination})" for s in hits[:3]]
        print(f"  {q!r}: {names}")

    print("\n-- check a couple of ingredients (honest unknown semantics) --")
    for token in ["red 40", "zinc pyrithione", "water", "totally unknown xyz"]:
        r = erl.check_token(token, reg, data_updated_at=reg.data_updated_at)
        flag = (f"{r.status} [{r.annex}] {r.regulation}"
                if r.is_in_dataset and r.determination != "unknown"
                else "unverified / not-in-dataset (NOT clean)")
        print(f"  {token!r}: in_dataset={r.is_in_dataset} "
              f"determination={r.determination} -> {flag}")


if __name__ == "__main__":
    main()
