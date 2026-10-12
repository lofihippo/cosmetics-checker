#!/usr/bin/env python3
"""Acquire and verify the pinned Tesseract.js OCR assets.

Regulatory information, not legal advice.

Downloads the exact pinned, versioned npm artifacts from the standard npm/unpkg
CDN into ``packages/scan-to-check/scan_to_check/static/vendor/tesseract/`` and
verifies each file's SHA-256 against the documented values in
``vendor-manifest.json``. Running with ``--verify-only`` checks the already
vendored files without downloading (fail if a file is missing or mismatched).

This is the single, reproducible acquisition/verification path for the vendored
photo-OCR tree. We acquire only the artifacts the app uses (browser main bundle,
worker, core builds, the English LSTM traineddata) plus the matching Apache-2.0
license/NOTICE text. No CDN dependency remains at runtime.

Usage:
    tools/vendor_tesseract.py                      # download + verify
    tools/vendor_tesseract.py --verify-only        # verify the vendored tree

Exit 0 on success; non-zero on any mismatch or missing file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "packages/scan-to-check/scan_to_check/static/vendor/tesseract"
MANIFEST = VENDOR / "vendor-manifest.json"
UA = "cosmetics-checker vendor-tool/1.0 (verification only)"


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def _verify(manifest: dict, verify_only: bool) -> int:
    failures = []
    for asset in manifest["assets"]:
        name = asset["name"]
        expected = asset["sha256"]
        path = VENDOR / name
        if verify_only:
            if not path.exists():
                failures.append(f"missing: {name}")
                continue
        else:
            print(f"downloading {name} <- {asset['upstream']}")
            _download(asset["upstream"], path)
        actual = _sha256_file(path)
        if actual != expected:
            failures.append(f"{name}: sha256 mismatch (expected {expected}, got {actual})")
        else:
            print(f"ok {name} ({expected[:12]}...)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true",
                        help="only verify the vendored files (no download)")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rc = _verify(manifest, verify_only=args.verify_only)
    if rc != 0:
        print("vendor verification FAILED", file=sys.stderr)
    else:
        print("vendor tree verified against vendor-manifest.json")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
