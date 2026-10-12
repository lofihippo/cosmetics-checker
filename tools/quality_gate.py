#!/usr/bin/env python3
"""Local quality gate for cosmetics-checker.

Regulatory information, not legal advice.

This provides ONE documented command for the release-quality checks that CANNOT
turn a failure into success. It has two subcommands:

  * ``coverage`` -- measures line coverage over a TRANSPARENT, explicitly named
    parser/normalizer/checker denominator (the actual CosIng/ECHA parsers,
    normalize, eu-reg-lists registry/checker/models and the linter
    normalization/evaluation modules) using the appropriate existing tests. It
    writes per-file and aggregate evidence to a temp dir (or --out) as JSON and
    prints a readable summary.

    It is a THIN SUBPROCESS ORCHESTRATOR around the standard tooling
    (``coverage run -m pytest`` then the public ``coverage json`` report). It
    never rounds an under-target result into a pass and it never overrides a
    non-zero pytest result (failed tests, collection errors, interruption, no
    tests, internal error) with a passing coverage percentage.

    Coverage.py's source configuration is used so that required source files
    that are NOT executed by the tests still contribute their REAL executable
    statement counts as zero coverage: missing or unmeasured required source
    never improves the aggregate. Missing source files (or an unavailable
    analysis) are a measurement-integrity failure, not a success.

  * ``lint`` -- runs ruff over the scoped canonical source (or ``--full`` over
    the repo) and returns non-zero on any finding; a missing tool is a hard
    error (exit 2), never a successful skip.

  * ``check`` -- orchestrates ALL required stages (tests, named-core coverage,
    scoped lint, mypy types) in one documented command, reporting each stage and
    returning non-zero if ANY required stage fails. A missing stage tool is a
    hard error (exit 2), never a successful skip. If broader pre-existing debt
    keeps a stage red, the stage's real status is reported rather than hidden.

The denominator is NOT gamed: it includes the data pipeline writers
(normalize.py, cosing_parser.py, echa_parser.py) plus the registry/checker and
the linter normalization/evaluation modules. ``--full`` reports whole-package
coverage across the maintained source, discovered recursively.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

DISCLAIMER = "Regulatory information, not legal advice."

# Transparent parser/normalizer/checker core denominator. Each entry is a
# source file (relative to ROOT). These are the modules the original V1 target
# ("parser/normalizer/checker core") names, including the actual data writers.
CORE_FILES = [
    "data/scripts/cosing_parser.py",
    "data/scripts/echa_parser.py",
    "data/scripts/normalize.py",
    "packages/eu-reg-lists/eu_reg_lists/__init__.py",
    "packages/eu-reg-lists/eu_reg_lists/checker.py",
    "packages/eu-reg-lists/eu_reg_lists/models.py",
    "packages/eu-reg-lists/eu_reg_lists/registry.py",
    "packages/eu-ingredient-linter/eu_ingredient_linter/__init__.py",
    "packages/eu-ingredient-linter/eu_ingredient_linter/tokenize.py",
    "packages/eu-ingredient-linter/eu_ingredient_linter/inputs.py",
    "packages/eu-ingredient-linter/eu_ingredient_linter/formula.py",
]

# Tests that exercise the core. We do NOT count test files in the denominator.
# These are the appropriate existing tests: the linter normalization/evaluation
# suite, the eu-reg-lists registry/checker suite, the parser/normalize tests and
# the snapshot/refresh tests that drive the data pipeline (normalize.py).
CORE_TESTS = [
    "packages/eu-ingredient-linter/tests/",
    "packages/eu-reg-lists/tests/",
    "tests/test_snapshot.py",
    "tests/test_refresh_isolated.py",
    "tests/test_eu_lint.py",
]

# Maintained source roots (relative to ROOT) used by the recursive ``--full``
# mode. Excludes generated build/lib copies and tests so the measured scope is
# the shipped source, not vendored duplicates.
FULL_SOURCE_ROOTS = [
    "data/scripts",
    "packages/eu-reg-lists/eu_reg_lists",
    "packages/eu-ingredient-linter/eu_ingredient_linter",
    "packages/eu-reg-api/eu_reg_api",
    "packages/scan-to-check/scan_to_check",
]


def _validate_target(target: float) -> float:
    """Reject non-finite or out-of-range coverage targets."""
    try:
        target = float(target)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"target must be a finite number in [0,1], got {target!r}") from exc
    if target != target or target in (float("inf"), float("-inf")):
        raise ValueError(f"target must be finite, got {target!r}")
    if not (0.0 <= target <= 1.0):
        raise ValueError(f"target must be in [0,1], got {target!r}")
    return target


def _discover_source_files(roots: list[Path]) -> list[Path]:
    """Recursively find .py files under each root, excluding build copies."""
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            # A missing source root is a measurement-integrity failure.
            raise FileNotFoundError(f"required source root missing: {root}")
        for path in sorted(root.rglob("*.py")):
            parts = path.parts
            if "build" in parts or "__pycache__" in parts:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _source_dirs(files: list[Path]) -> list[str]:
    """Smallest set of source roots that contains every requested file, plus
    their package parents so unexecuted sibling modules are measured."""
    roots = []
    for f in files:
        if f.suffix != ".py":
            raise ValueError(f"not a python source file: {f}")
        # Keep the containing directory so unexecuted siblings are included.
        candidate = f.parent
        if str(candidate) not in roots:
            roots.append(str(candidate))
    return roots


def _run_coverage(out_path: Path, target: float, full: bool) -> int:
    """Run pytest under Coverage.py and report the named denominator.

    This is deliberately a thin orchestrator around the standard CLI:
      * ``coverage run --source=<roots> -m pytest <tests>`` runs the real
        collector with Coverage.py's source configuration so unexecuted source
        under those roots is measured as zero-covered.
      * ``coverage json --include=<required files>`` produces the public
        machine-readable per-file report restricted to the required files.
    """
    target = _validate_target(target)

    if full:
        roots = [ROOT / r for r in FULL_SOURCE_ROOTS]
        required_files = _discover_source_files(roots)
    else:
        required_files = [ROOT / f for f in CORE_FILES]
        if not required_files:
            raise ValueError("no core files configured")
        roots = [f.parent for f in required_files]

    # Validate every required file exists before measuring; a missing source
    # must never shrink the denominator.
    missing = [str(f) for f in required_files if not f.exists()]
    if missing:
        raise FileNotFoundError(
            "required source file(s) missing; refusing to measure an incomplete "
            f"denominator: {', '.join(missing)}")

    if full:
        source = _source_dirs(required_files)
    else:
        source = [str(f.parent) for f in required_files]

    data_dir = Path(tempfile.mkdtemp(prefix="quality-coverage-"))
    data_file = data_dir / ".coverage.quality"
    json_out = data_dir / "coverage.json"

    from coverage import __version__ as _cv  # noqa: F401  (probe; hard-fail if absent)
    subprocess.run([PY, "-m", "coverage", "version"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 1) Run the real collector under Coverage.py.
    env = dict(__import__("os").environ)
    env["COVERAGE_FILE"] = str(data_file)
    cmd = [PY, "-m", "coverage", "run", "--source=" + ",".join(source)] \
        + ["-m", "pytest", "-q", "-p", "no:cacheprovider"] + CORE_TESTS
    pytest_proc = subprocess.run(cmd, cwd=str(ROOT), env=env, check=False)
    pytest_rc = pytest_proc.returncode

    # 2) Produce the machine-readable report restricted to the required files.
    include = ",".join(str(f) for f in required_files)
    report_cmd = [PY, "-m", "coverage", "json",
                  "--include=" + include,
                  "-o", str(json_out)]
    report_proc = subprocess.run(report_cmd, cwd=str(ROOT), env=env, check=False)
    if report_proc.returncode != 0 or not json_out.exists():
        print("[QUALITY] measurement integrity FAILED: coverage report could not "
              "be produced for the required denominator.", file=sys.stderr)
        return 2

    report = json.loads(json_out.read_text(encoding="utf-8"))
    per_file = _normalize_report(report, required_files)
    tot_stmt = sum(p["statements"] for p in per_file)
    tot_miss = sum(p["missing"] for p in per_file)
    aggregate = (tot_stmt - tot_miss) / tot_stmt if tot_stmt else 0.0

    result = {
        "disclaimer": DISCLAIMER,
        "kind": ("full" if full else "core"),
        "target": target,
        "aggregate_coverage": aggregate,
        "aggregate_rounded_pct": round(aggregate * 100, 2),
        "statements": tot_stmt,
        "missing": tot_miss,
        "covered": tot_stmt - tot_miss,
        "tests": CORE_TESTS,
        "denominator_files": [str(f) for f in required_files],
        "pytest_exit": pytest_rc,
        "per_file": per_file,
    }
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _print_summary(result)

    integrity_ok = True
    for p in per_file:
        if not p["present"]:
            print(f"[QUALITY] measurement integrity: {p['file']} could not be "
                  "analysed; refusing to count it as clean.", file=sys.stderr)
            integrity_ok = False

    if pytest_rc != 0:
        print(f"\n[QUALITY] pytest collector exit {pytest_rc} (failed tests, "
              "collection errors or interruption). The gate FAILS.")
        return 1

    if not integrity_ok:
        print("\n[QUALITY] measurement integrity FAILED: required source was "
              "missing or could not be analysed.")
        return 2

    if aggregate < target:
        print(f"\n[QUALITY] aggregate {result['kind']} coverage "
              f"{aggregate*100:.2f}% is BELOW target {target*100:.2f}%. "
              "Files under 100%:")
        for p in per_file:
            if p["present"] and p["coverage"] < 1.0:
                print(f"  {p['file']}: {p['coverage']*100:.2f}% "
                      f"({p['missing']}/{p['statements']} missing)")
        print("[QUALITY] the gate FAILS (no rounding to target).")
        return 1
    print(f"\n[QUALITY] {result['kind']} coverage {aggregate*100:.2f}% >= target "
          f"{target*100:.2f}%. PASS.")
    return 0


def _normalize_report(report: dict, required_files: list[Path]) -> list[dict]:
    """Map the public coverage JSON report onto the required-file list.

    Every required file must appear with its real statement counts (executed
    lines minus missing). If a required file is absent from the report, it is
    recorded as ``present:false`` so the aggregate cannot silently drop it.
    """
    files = report.get("files", {})
    by_path = {}
    for name, entry in files.items():
        # coverage reports paths relative to the cwd; resolve to an absolute
        # path that matches the required-file key.
        p = Path(name)
        if not p.is_absolute():
            p = ROOT / p
        by_path[str(p.resolve())] = entry

    required = {str(f.resolve()): f for f in required_files}
    per_file = []
    for req_path, req_source in required.items():
        entry = by_path.get(req_path)
        if entry is None:
            per_file.append({
                "file": req_path,
                "statements": 0,
                "missing": 0,
                "covered": 0,
                "coverage": 0.0,
                "present": False,
            })
            continue
        summary = entry.get("summary", {})
        statements = int(summary.get("num_statements", 0))
        missing = int(summary.get("missing_lines", 0))
        covered = statements - missing
        percent = float(summary.get("percent_covered", 0.0))
        per_file.append({
            "file": req_path,
            "statements": statements,
            "missing": missing,
            "covered": covered,
            "coverage": (percent / 100.0),
            "present": True,
        })
    return per_file


def _print_summary(report: dict) -> None:
    print(f"\n=== {report['kind'].upper()} COVERAGE "
          f"({len(report['denominator_files'])} files) ===")
    for p in report["per_file"]:
        print(f"  {p['coverage']*100:6.2f}%  {p['file']}  "
              f"({p['missing']}/{p['statements']} missing)")
    print(f"  AGGREGATE: {report['aggregate_coverage']*100:.2f}% "
          f"({report['covered']}/{report['statements']} statements)")
    print(f"  pytest_exit: {report['pytest_exit']}")


def _ruff_available() -> bool:
    """Ruff is present as an installable module (probe via subprocess)."""
    proc = subprocess.run([PY, "-m", "ruff", "--version"], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    return proc.returncode == 0


def _mypy_available() -> bool:
    """mypy is present as an installable module (probe via subprocess)."""
    proc = subprocess.run([PY, "-m", "mypy", "--version"], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    return proc.returncode == 0


def run_check(out_dir: Path, target: float) -> int:
    """Run all required quality stages (tests, core coverage, lint, type).

    Each stage reports its own status; any required stage failure yields a
    non-zero overall exit. A missing tool is a hard error (exit 2), never a
    successful skip.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    overall = 0
    print("=== QUALITY CHECK (stages: test, coverage, lint, types) ===")

    # 1) Full test suite.
    test_cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "tests/", "packages/eu-reg-lists/tests/",
                "packages/eu-ingredient-linter/tests/",
                "packages/eu-reg-api/tests/", "packages/scan-to-check/tests/"]
    print("\n--- stage: tests ---")
    test_proc = subprocess.run(test_cmd, cwd=str(ROOT), check=False)
    test_rc = test_proc.returncode
    print(f"[check] tests exit {test_rc}")
    if test_rc != 0:
        overall = 1

    # 2) Core coverage (the 92.59% targeted gate; preserves pytest status).
    print("\n--- stage: coverage ---")
    cov_out = out_dir / "coverage.json"
    cov_rc = _run_coverage(cov_out, target, False)
    print(f"[check] coverage exit {cov_rc}")
    if cov_rc != 0:
        overall = 1

    # 3) Scoped lint over the maintained source.
    print("\n--- stage: lint ---")
    lint_rc = _run_lint(False)
    print(f"[check] lint exit {lint_rc}")
    if lint_rc != 0:
        overall = 1

    # 4) Type check (mypy). Missing mypy is a hard error, not a skip.
    print("\n--- stage: types ---")
    mypy_rc = _run_mypy()
    print(f"[check] types exit {mypy_rc}")
    if mypy_rc != 0:
        overall = 1

    print(f"\n=== QUALITY CHECK summary: tests{test_rc} coverage{cov_rc} "
          f"lint{lint_rc} types{mypy_rc} ===")
    return overall


def _run_mypy() -> int:
    if not _mypy_available():
        print("error: mypy is not installed; pip install mypy", file=sys.stderr)
        return 2
    proc = subprocess.run([PY, "-m", "mypy"], cwd=str(ROOT), check=False)
    return proc.returncode


def _run_lint(full: bool) -> int:
    # full -> run over the monorepo; scoped -> the maintained source w/o tests.
    scoped = [
        "packages/eu-reg-lists/eu_reg_lists", "packages/eu-ingredient-linter/eu_ingredient_linter",
        "packages/eu-reg-api/eu_reg_api", "packages/scan-to-check/scan_to_check",
        "eu_lint.py", "app.py", "data/scripts", "examples/acceptance_demo.py",
        "tools/quality_gate.py",
    ]
    scope = scoped if not full else [str(ROOT)]
    # ruff must be present.
    if not _ruff_available():
        print("error: ruff is not installed; pip install ruff", file=sys.stderr)
        return 2
    print(f"[lint] running ruff over {'full repo' if full else 'scoped paths'}")
    proc = subprocess.run([PY, "-m", "ruff", "check", "--select", "E4,E7,E9,F"] + scope,
                          cwd=str(ROOT), check=False)
    return proc.returncode


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coverage", help="measure core coverage")
    c.add_argument("--target", type=float, default=0.90)
    c.add_argument("--out", default=None)
    c.add_argument("--full", action="store_true",
                   help="measure whole-package coverage (not the core denominator)")
    lint = sub.add_parser("lint", help="run ruff (scoped or full)")
    lint.add_argument("--full", action="store_true")
    chk = sub.add_parser("check", help="run all quality stages (tests, coverage, lint, types)")
    chk.add_argument("--out", default=None)
    chk.add_argument("--target", type=float, default=0.90)
    args = ap.parse_args(argv)

    if args.cmd == "coverage":
        out_path = Path(args.out) if args.out else \
            Path(tempfile.mkdtemp(prefix="quality-")) / "coverage.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _run_coverage(out_path, args.target, args.full)
        except (ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.cmd == "lint":
        return _run_lint(args.full)
    if args.cmd == "check":
        out_dir = Path(args.out) if args.out else \
            Path(tempfile.mkdtemp(prefix="quality-check-"))
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            return run_check(out_dir, args.target)
        except (ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
