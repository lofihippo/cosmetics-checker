"""Path config so tests can import eu_reg_lists and eu_ingredient_linter.

Both packages are standalone source trees under packages/; these tests run
against the source (not an installed wheel), so we add both to sys.path.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"

for pkg in (PACKAGES / "eu-reg-lists", PACKAGES / "eu-ingredient-linter"):
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
