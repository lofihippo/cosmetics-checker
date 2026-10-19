"""Command-level regressions for the quality gate (tools/quality_gate.py).


These are SMALL, isolated tests. They build a tiny throwaway project in a temp
dir and invoke the REAL ``tools/quality_gate.py`` orchestrator against it (via
importlib so CORE_FILES/CORE_TESTS/ROOT are redirected), so each regression
targets one reproduced false-pass case without rerunning the full collector.

Cases covered (previously all returned exit 0 / PASS):
  * a failing pytest collector must yield a non-zero gate exit;
  * an unexecuted required source file must add its real statement count as
    0% coverage (never be silently dropped from the denominator);
  * the exact 90% core target and visible denominator are preserved;
  * non-finite / out-of-range ``--target`` must be rejected.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "quality_gate", ROOT / "tools" / "quality_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def _make_project(tmp_path):
    """A tiny project: one executed module (2 stmts), one unexecuted module
    (2 stmts), and a single passing test that imports only the executed one."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "used.py").write_text(
        "def used():\n    return 1\n", encoding="utf-8")
    (tmp_path / "unused.py").write_text(
        "def never():\n    return 9\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text(
        "from used import used\ndef test():\n    assert used() == 1\n",
        encoding="utf-8")
    return tmp_path


def _run_gate(gate, tmp_path, out, target=0.90, files=None, tests=None):
    gate.ROOT = tmp_path
    gate.CORE_FILES = files if files is not None else ["used.py", "unused.py"]
    gate.CORE_TESTS = tests if tests is not None else ["test_x.py"]
    return gate._run_coverage(out, target, False)


def test_unexecuted_source_is_measured_not_dropped(tmp_path):
    """An unexecuted required file must contribute real 0% statements."""
    project = _make_project(tmp_path / "project")
    gate = _load_gate()
    out = tmp_path / "out.json"
    rc = _run_gate(gate, project, out)
    # 2/4 covered = 50% < 90% -> gate fails, even though the single test passes.
    assert rc == 1
    report = json.loads(out.read_text())
    by_name = {Path(f["file"]).name: f for f in report["per_file"]}
    assert by_name["used.py"]["present"] is True
    assert by_name["used.py"]["statements"] == 2
    assert by_name["unused.py"]["present"] is True
    assert by_name["unused.py"]["statements"] == 2
    assert by_name["unused.py"]["coverage"] == 0.0
    assert report["statements"] == 4
    assert report["covered"] == 2


def test_failing_pytest_collector_forces_nonzero_exit(tmp_path):
    """A failing test must fail the gate regardless of the coverage number."""
    project = _make_project(tmp_path / "project")
    (project / "test_fail.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8")
    gate = _load_gate()
    out = tmp_path / "out.json"
    rc = _run_gate(gate, project, out, tests=["test_x.py", "test_fail.py"])
    assert rc == 1, "a failing pytest collector must fail the gate"
    report = json.loads(out.read_text())
    assert report["pytest_exit"] == 1


def test_missing_required_source_fails_integrity(tmp_path):
    """A missing required source must NOT shrink the denominator."""
    project = _make_project(tmp_path / "project")
    gate = _load_gate()
    out = tmp_path / "out.json"
    with pytest.raises(FileNotFoundError):
        _run_gate(gate, project, out, files=["used.py", "-no-such-file.py"])


def test_invalid_target_rejected():
    gate = _load_gate()
    for bad in ("nan", "inf", "-1", "2"):
        with pytest.raises(ValueError):
            gate._run_coverage(Path("/tmp/never.json"), float(bad), False)
