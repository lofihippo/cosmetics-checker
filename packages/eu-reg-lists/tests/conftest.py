"""Make the eu-reg-lists package importable from tests without installation."""

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
PKG_DIR = THIS.parents[1]      # packages/eu-reg-lists
REPO_ROOT = THIS.parents[3]    # repo root

for p in (PKG_DIR, REPO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
