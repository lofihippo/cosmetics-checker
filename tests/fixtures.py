"""Shared deterministic synthetic snapshot fixtures.


The quality/acceptance/action regression suites must run on a clean checkout
with NO production snapshot (``data/processed/*.sqlite`` / ``*.jsonl`` are
gitignored) and NO ``.local-reference``. This module builds a *small, clearly
synthetic* snapshot tree in a caller-owned temporary directory using the real
maintained writers (``normalize.build_processed``) and, where required, exports
a deterministic snapshot bundle via ``snapshot.export_snapshot``.

Synthetic evidence is labelled as such and is NEVER presented as real official
(or real regulatory/performance) coverage. Real-data acceptance remains a
separately configured check.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Root of the source checkout (this file lives under tests/).
ROOT = Path(__file__).resolve().parents[1]
NORMALIZE = ROOT / "data" / "scripts" / "normalize.py"
SNAPSHOT = ROOT / "data" / "scripts" / "snapshot.py"

DISCLAIMER = "Regulatory information, not legal advice."

# Fixed, deterministic snapshot timestamp so the synthetic snapshot is
# byte-reproducible (build_processed normally uses wall-clock time).
FIXED_DATA_UPDATED_AT = "2026-01-01T00:00:00+00:00"

# The synthetic baseline's substance / verified-rule counts (derived from the
# fixture Annex II + III exports). Used by refresh tests that assert the counts.
BASELINE_SUBSTANCES = 110
BASELINE_VERIFIED_RULES = 5

# A small, deterministic Annex II export. Includes a banned substance that the
# Action/linter tests rely on (Benzene) so a lookup returns a real rule. The
# header must match the real CosIng format the parser expects.
ANNEX_II_CSV = (
    '"File creation date: 08/10/2026"\n'
    '"ANNEX II","Last update: 06/10/2026"\n'
    '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
    '"Substance identification"\n'
    '"Reference Number","Chemical name / INN","CAS Number",'
    '"EC Number",Regulation,"Other Directives/Regulations",'
    '"SCCS opinions","Chemical/IUPAC Name",'
    '"Identified INGREDIENTS or substances e.g.",CMR,"Update Date"\n'
    '47,Benzene,71-43-2,200-753-7,"(EC) 1223/2009",,'
    '"","Benzene","",,"\n'
    '1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",'
    ',,,,,15/10/2010\n'
    '1339,"1,4-Dihydroxybenzene (Hydroquinone), with the exception of entry 14 '
    'in Annex III",123-31-9,204-617-8,"(EC) 1223/2009",,,,'
    '"HYDROQUINONE",,15/10/2010\n'
)

# A small, deterministic Annex III export (restricted substances). The refresh
# pipeline tests need a second source so a changed/wrong-annex/stale candidate
# can be staged per-source.
ANNEX_III_CSV = (
    '"File creation date: 08/10/2026"\n'
    '"ANNEX III","Last update: 06/10/2026"\n'
    '"LIST OF SUBSTANCES WHICH COSMETIC PRODUCTS MUST NOT CONTAIN EXCEPT '
    'SUBJECT TO THE RESTRICTIONS LAID DOWN"\n'
    '"Substance identification",Restrictions\n'
    '"Reference Number","Chemical name / INN","Name of Common Ingredients '
    'Glossary","CAS Number","EC Number","Product Type, body parts",'
    '"Maximum concentration in ready for use preparation",Other,'
    '"Wording of conditions of use and warnings",Regulation,'
    '"Other Directives/Regulations","SCCS opinions","Chemical/IUPAC Name",'
    '"Identified INGREDIENTS or substances e.g.",CMR,"Update Date"\n'
    '185,"Toluene",TOLUENE,108-88-3,203-625-9,"Nail products",25%,"",'
    ',"(EC) 1223/2009",,,,,\n'
    '17,"Sodium nitrite",,7632-00-0,231-555-9,"Rust inhibitor",0.2%,,'
    ',"(EC) 1223/2009",,,,,\n'
    '45,"Benzyl alcohol",BENZYL ALCOHOL,100-51-6,202-859-9,"Fragrance",1%,,'
    ',"(EC) 1223/2009",,,,,\n'
    '46,"Benzyl alcohol",BENZYL ALCOHOL,100-51-6,202-859-9,"Fragrance (rinse-off)",0.5%,,'
    ',"(EC) 1223/2009",,,,,\n'
)


def build_tree(root: Path) -> Path:
    """Build a synthetic snapshot tree under ``root``.

    Writes minimal raw Annex II + III exports and a manifest, runs the real
    writer to emit ``data/processed/{eu_reg_lists.sqlite,eu_reg_lists.jsonl,
    metadata.json}`` (with a fixed snapshot timestamp), and adds the fixed
    files (LICENSE, LICENSES.md, raw manifest) a verified snapshot tree
    requires. Returns the snapshot tree root.
    """
    raw = root / "data" / "raw"
    ii_dir = raw / "cosing-annex-ii"
    ii_dir.mkdir(parents=True, exist_ok=True)
    ii_export = ii_dir / "COSING_Annex_II_v2.txt"
    ii_export.write_text(ANNEX_II_CSV, encoding="utf-8")
    iii_dir = raw / "cosing-annex-iii"
    iii_dir.mkdir(parents=True, exist_ok=True)
    iii_export = iii_dir / "COSING_Annex_III_v2.txt"
    iii_export.write_text(ANNEX_III_CSV, encoding="utf-8")

    manifest = {
        "cosing-annex-ii": {
            "fetched": True, "accepted": True, "filename": ii_export.name,
            "sha256": hashlib.sha256(ii_export.read_bytes()).hexdigest(),
            "byte_len": ii_export.stat().st_size,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        },
        "cosing-annex-iii": {
            "fetched": True, "accepted": True, "filename": iii_export.name,
            "sha256": hashlib.sha256(iii_export.read_bytes()).hexdigest(),
            "byte_len": iii_export.stat().st_size,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        },
    }
    (raw / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    processed = root / "data" / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    import sys as _sys
    if str(ROOT / "data" / "scripts") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "data" / "scripts"))
    normalize = _load_module("normalize_fixture", NORMALIZE)
    setattr(normalize, "_now_iso", lambda: FIXED_DATA_UPDATED_AT)
    normalize.build_processed(output_dir=processed, inputs_dir=raw)

    (root / "LICENSE").write_text("Synthetic fixture license", encoding="utf-8")
    (root / "LICENSES.md").write_text(
        "Synthetic fixture attribution (not real regulatory data).", encoding="utf-8")
    return root


def build_bundle(root: Path, output: Path) -> Path:
    """Export a deterministic snapshot bundle from ``root`` (must be a tree built
    by :func:`build_tree`). Fails if the target already exists."""
    import sys as _sys
    if str(ROOT / "data" / "scripts") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "data" / "scripts"))
    if str(ROOT / "packages" / "eu-reg-lists") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
    snapshot = _load_module("snapshot_fixture", SNAPSHOT)
    snapshot.export_snapshot(root, output)
    return output


def _load_module(name: str, path: Path):
    """Import a source module by path, with a guard against a missing loader."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sqlite(root: Path) -> Path:
    return root / "data" / "processed" / "eu_reg_lists.sqlite"
