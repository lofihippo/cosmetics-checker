"""Restore the pre-change (reference) snapshot bundle into the live data tree.

This restores the exact raw exports + processed outputs that existed before the
Part A placeholder-identifier normalization was applied. Use it to reproduce the
original false-quarantine counts or to roll back the processed bundle.

Usage:
    python data/scripts/restore_baseline.py [--dry-run]

By default copies from ``.local-reference/restore-baseline/`` back into
``data/raw/`` and ``data/processed/``. ``--dry-run`` only reports what would be
copied and verifies the manifest hashes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / ".local-reference" / "restore-baseline"
MANIFEST = BUNDLE / "manifest.json"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    dry = "--dry-run" in (argv or sys.argv[1:])
    if not MANIFEST.exists():
        print(f"restore bundle not found: {MANIFEST}", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text())
    ok = True
    for rel, expected in manifest.items():
        src = BUNDLE / rel
        if not src.exists():
            print(f"MISSING {rel}", file=sys.stderr)
            ok = False
            continue
        actual = _sha256(src)
        if actual != expected:
            print(f"HASH MISMATCH {rel}: {actual} != {expected}", file=sys.stderr)
            ok = False
    if not ok:
        print("bundle is not intact; refusing to restore", file=sys.stderr)
        return 1
    if dry:
        print(f"bundle intact ({len(manifest)} files); dry-run, nothing copied")
        return 0
    for rel in manifest:
        src = BUNDLE / rel
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"restored {len(manifest)} files from the reference bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
